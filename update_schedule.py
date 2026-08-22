import json
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

YEAR = 2026
BASE = "https://www.pigskinprep.com/"
ROOT_SCHEDULE = f"{BASE}showfreeschedule.asp?yr={YEAR}"
NCES_QUERY = (
    "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/"
    "EDGE_GEOCODE_PUBLICSCH_2324/MapServer/0/query"
)
OUT = Path("season.json")

# The site's 10 regular-season windows.
WEEKS = {
    "1": {"label": "Week 1 • Aug. 27–29, 2026", "start": "2026-08-27", "end": "2026-08-29"},
    "2": {"label": "Week 2 • Sep. 3–5, 2026", "start": "2026-09-03", "end": "2026-09-05"},
    "3": {"label": "Week 3 • Sep. 10–12, 2026", "start": "2026-09-10", "end": "2026-09-12"},
    "4": {"label": "Week 4 • Sep. 17–19, 2026", "start": "2026-09-17", "end": "2026-09-19"},
    "5": {"label": "Week 5 • Sep. 24–26, 2026", "start": "2026-09-24", "end": "2026-09-26"},
    "6": {"label": "Week 6 • Oct. 1–3, 2026", "start": "2026-10-01", "end": "2026-10-03"},
    "7": {"label": "Week 7 • Oct. 8–10, 2026", "start": "2026-10-08", "end": "2026-10-10"},
    "8": {"label": "Week 8 • Oct. 15–17, 2026", "start": "2026-10-15", "end": "2026-10-17"},
    "9": {"label": "Week 9 • Oct. 22–24, 2026", "start": "2026-10-22", "end": "2026-10-24"},
    "10": {"label": "Week 10 • Oct. 29–31, 2026", "start": "2026-10-29", "end": "2026-10-31"},
}

# Known blue-chip / major-program talent index. Unknown schools receive a class-based baseline.
ELITE = {
    "duncanville": 99, "desoto": 99, "north crowley": 97, "north shore": 98,
    "atascocita": 96, "south oak cliff": 96, "allen": 94, "southlake carroll": 94,
    "westlake": 95, "lake travis": 92, "aledo": 92, "denton guyer": 92,
    "guyer": 92, "prosper": 90, "katy": 90, "summer creek": 92, "steele": 93,
    "smithson valley": 89, "vandegrift": 90, "coppell": 87, "waxahachie": 89,
    "lancaster": 91, "longview": 88, "carthage": 88, "argyle": 88,
    "frisco lone star": 87, "denton ryan": 87, "highland park": 86,
    "lovejoy": 83, "celina": 86, "red oak": 84, "cedar hill": 86,
    "rockwall": 85, "rockwall-heath": 84, "midland legacy": 83,
    "permian": 82, "lubbock cooper": 82, "amarillo tascosa": 80,
}

CLASS_BASE = {
    "6A": (72, 60),
    "5A": (68, 56),
    "4A": (63, 52),
    "3A": (58, 49),
    "2A": (54, 46),
    "1A": (50, 43),
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TexasHSGameFinder/1.0 (+schedule-index; public schedule research)"
})

def get(url, params=None):
    r = SESSION.get(url, params=params, timeout=30)
    r.raise_for_status()
    time.sleep(0.12)
    return r

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\b(high school|hs|school)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def strip_prefix(s):
    # PigskinPrep often uses city prefixes: "Houston North Shore", "Denton Guyer".
    tokens = norm(s).split()
    return " ".join(tokens[-3:]) if len(tokens) > 3 else " ".join(tokens)

def load_nces():
    params = {
        "where": "STATE='TX'",
        "outFields": "NAME,CITY,STATE,LAT,LON",
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": 2000,
    }
    first = get(NCES_QUERY, params=params).json()
    features = first.get("features", [])
    offset = len(features)
    while first.get("exceededTransferLimit"):
        params["resultOffset"] = offset
        page = get(NCES_QUERY, params=params).json()
        page_features = page.get("features", [])
        if not page_features:
            break
        features.extend(page_features)
        offset += len(page_features)
        first = page
    schools = []
    for f in features:
        a = f.get("attributes", {})
        if a.get("LAT") is None or a.get("LON") is None:
            continue
        schools.append({
            "name": a.get("NAME", ""),
            "city": a.get("CITY", ""),
            "lat": a["LAT"],
            "lng": a["LON"],
            "key": norm(a.get("NAME", "")),
        })
    return schools

def location_matcher(nces):
    keys = [x["key"] for x in nces]
    exact = {}
    for x in nces:
        exact.setdefault(x["key"], x)

    cache = {}
    def match(team):
        k = norm(team)
        if not k:
            return None
        if k in cache:
            return cache[k]
        if k in exact:
            cache[k] = exact[k]
            return exact[k]

        # Try city-prefix-stripped name first.
        short = strip_prefix(team)
        candidates = []
        for query in [k, short]:
            results = process.extract(query, keys, scorer=fuzz.WRatio, limit=5)
            for _, score, idx in results:
                candidates.append((score, nces[idx]))
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Conservative threshold to avoid placing a game in the wrong city.
        best = candidates[0][1] if candidates and candidates[0][0] >= 83 else None
        cache[k] = best
        return best
    return match

def class_from_heading(text):
    m = re.search(r"Class\s+([1-6]A)", text, re.I)
    return m.group(1).upper() if m else "4A"

def discover_district_pages():
    soup = BeautifulSoup(get(ROOT_SCHEDULE).text, "html.parser")
    class_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "showfreeregion.asp" in href and "yr=2026" in href:
            class_links.append(urljoin(BASE, href))

    found = []
    seen = set()
    for curl in sorted(set(class_links)):
        csoup = BeautifulSoup(get(curl).text, "html.parser")
        heading = csoup.get_text(" ", strip=True)
        cls = class_from_heading(heading)
        for a in csoup.find_all("a", href=True):
            href = a["href"]
            if "showfreeschedules.asp" not in href or "distID=" not in href:
                continue
            url = urljoin(BASE, href)
            if url in seen:
                continue
            seen.add(url)
            found.append((cls, url))
    return found

def parse_team_header(line):
    if not line.endswith("Score Time"):
        return None
    # Remove Score Time and records.
    x = line[:-len("Score Time")].strip()
    x = re.sub(r"\(\d+-\d+\)\s*\(\d+-\d+\)\s*$", "", x).strip()
    # Remove class/district prefix, e.g. "2A - 1"
    x = re.sub(r"^[1-6]A(?:\s+D[12])?\s*-\s*\d+\s+", "", x).strip()
    return x or None

def parse_game_line(line):
    m = re.match(r"^(\d{1,2}/\d{1,2})\s+(at|vs)\s+(.+)$", line, re.I)
    if not m:
        return None
    md, relation, tail = m.groups()
    tm = re.search(r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*$", tail, re.I)
    if not tm:
        return None
    time_str = tm.group(1).upper()
    opp = tail[:tm.start()].strip()
    neutral = None
    nm = re.search(r"\(at\s+([^)]+)\)", opp, re.I)
    if nm:
        neutral = nm.group(1).strip()
    opp = re.sub(r"\(at\s+[^)]+\)", " ", opp, flags=re.I)
    opp = re.sub(r"\(\d+-\d+\)\*?", " ", opp)
    opp = opp.replace("*", " ")
    opp = re.sub(r"\s+", " ", opp).strip(" -")
    if not opp or opp.lower() == "open":
        return None
    month, day = map(int, md.split("/"))
    try:
        dt = datetime(YEAR, month, day)
    except ValueError:
        return None
    return {
        "date_obj": dt,
        "relation": relation.lower(),
        "opponent": opp,
        "time": time_str,
        "district": "*" in tail,
        "neutral": neutral,
    }

def week_for(dt):
    for wk, meta in WEEKS.items():
        s = datetime.fromisoformat(meta["start"])
        e = datetime.fromisoformat(meta["end"])
        if s.date() <= dt.date() <= e.date():
            return wk
    return None

def talent(team, cls):
    k = norm(team)
    for name, value in ELITE.items():
        if name in k or k in name:
            return value
    return CLASS_BASE.get(cls, (60, 50))[1]

def quality(team, cls):
    base = CLASS_BASE.get(cls, (60, 50))[0]
    t = talent(team, cls)
    return min(99, round(base + max(0, t - 55) * 0.55))

def game_components(team, opp, cls, opp_cls="4A", district=False):
    tq1, tq2 = quality(team, cls), quality(opp, opp_cls)
    rt1, rt2 = talent(team, cls), talent(opp, opp_cls)
    team_quality = round((tq1 + tq2) / 2)
    recruit = round((rt1 + rt2) / 2)
    matchup = max(55, round(98 - abs(tq1 - tq2) * 1.2 - abs(rt1 - rt2) * 0.35))
    importance = 82 if district else 68
    if tq1 >= 85 and tq2 >= 85:
        importance += 8
    if recruit >= 88:
        importance += 6
    return team_quality, recruit, min(100, matchup), min(100, importance)

def score(g):
    return round(
        g["teamQuality"] * .40 +
        g["recruitTalent"] * .35 +
        g["matchupQuality"] * .15 +
        g["importance"] * .10
    )

def main():
    print("Downloading NCES Texas public-school coordinates...")
    nces = load_nces()
    print(f"Loaded {len(nces):,} public school locations.")
    locate = location_matcher(nces)

    pages = discover_district_pages()
    print(f"Discovered {len(pages)} free PigskinPrep district schedule pages.")

    raw = []
    class_by_team = {}

    for n, (cls, url) in enumerate(pages, 1):
        soup = BeautifulSoup(get(url).text, "html.parser")
        lines = [re.sub(r"\s+", " ", x).strip() for x in soup.stripped_strings]
        current = None
        for line in lines:
            header = parse_team_header(line)
            if header:
                current = header
                class_by_team[norm(current)] = cls
                continue
            if not current:
                continue
            game = parse_game_line(line)
            if not game:
                continue
            wk = week_for(game["date_obj"])
            if not wk:
                continue
            raw.append({
                "week": wk,
                "team": current,
                "opponent": game["opponent"],
                "relation": game["relation"],
                "date_obj": game["date_obj"],
                "time": game["time"],
                "district": game["district"],
                "neutral": game["neutral"],
                "class": cls,
                "source": url,
            })
        if n % 25 == 0:
            print(f"Parsed {n}/{len(pages)} district pages...")

    # De-duplicate because each matchup usually appears on both team schedules.
    seen = {}
    for r in raw:
        key = (
            r["date_obj"].strftime("%Y-%m-%d"),
            tuple(sorted([norm(r["team"]), norm(r["opponent"])]))
        )
        # Prefer a record with 'vs' because that identifies the home team's page.
        if key not in seen or (r["relation"] == "vs" and seen[key]["relation"] != "vs"):
            seen[key] = r

    season = {
        k: {**v, "games": []}
        for k, v in WEEKS.items()
    }

    matched = 0
    for r in seen.values():
        team_loc = locate(r["team"])
        opp_loc = locate(r["opponent"])

        if r["relation"] == "at":
            away, home = r["team"], r["opponent"]
            venue_loc = opp_loc
        else:
            away, home = r["opponent"], r["team"]
            venue_loc = team_loc

        # Public-school coordinates are essential for proximity. If neither school
        # can be confidently located, skip rather than put it in the wrong area.
        if venue_loc is None:
            continue
        matched += 1

        opp_cls = class_by_team.get(norm(r["opponent"]), "4A")
        tq, rt, mq, imp = game_components(
            r["team"], r["opponent"], r["class"], opp_cls, r["district"]
        )

        g = {
            "away": away,
            "home": home,
            "date": r["date_obj"].strftime("%A, %b. %-d"),
            "time": r["time"],
            "venue": f'{venue_loc["city"]}, TX',
            "lat": venue_loc["lat"],
            "lng": venue_loc["lng"],
            "teamQuality": tq,
            "recruitTalent": rt,
            "matchupQuality": mq,
            "importance": imp,
            "source": r["source"],
        }
        season[r["week"]]["games"].append(g)

    for wk in season.values():
        wk["games"].sort(key=score, reverse=True)

    OUT.write_text(json.dumps(season, indent=2), encoding="utf-8")
    total = sum(len(x["games"]) for x in season.values())
    print(f"Wrote {total:,} unique, geolocated regular-season games to {OUT}.")
    print(f"Coordinate match count: {matched:,}")

if __name__ == "__main__":
    main()
