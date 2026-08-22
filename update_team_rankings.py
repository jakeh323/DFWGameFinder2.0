import json
import math
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

YEAR = 2026
SEASON_FILE = Path("season.json")
OUT = Path("team-rankings.json")
BASE = "https://www.pigskinprep.com/showweeklyscoresdetail.asp"

CLASSES = {
    8: "6A",
    1: "5A",
    2: "4A",
    3: "3A",
    4: "2A",
    5: "1A",
}

session = requests.Session()
retry = Retry(
    total=6, connect=6, read=6, status=6,
    backoff_factor=1.25,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
session.mount("https://", HTTPAdapter(max_retries=retry))
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LoneStarGameFinder/1.0)"})

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\b(high school|senior high school|hs)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

ALIASES = {
    "humble atascocita":"atascocita",
    "humble summer creek":"summer creek",
    "galena park north shore":"north shore",
    "austin westlake":"westlake",
    "schertz cibolo steele":"steele",
    "frisco lone star":"lone star",
    "denton guyer":"guyer",
    "denton ryan":"ryan",
    "richmond randle":"randle",
}

def canon(s):
    k = norm(s)
    return ALIASES.get(k, k)

def get(url, params=None):
    for attempt in range(1, 6):
        try:
            r = session.get(url, params=params, timeout=(15, 45))
            r.raise_for_status()
            time.sleep(.25)
            return r
        except requests.RequestException as e:
            if attempt == 5:
                print(f"WARNING: failed source {url}: {e}")
                return None
            time.sleep(min(20, 2 ** attempt))

def preseason_seeds():
    data = json.loads(SEASON_FILE.read_text(encoding="utf-8"))
    vals = defaultdict(list)
    names = {}
    for wk in data.values():
        for g in wk.get("games", []):
            tq = float(g.get("teamQuality", 65))
            for t in [g.get("away"), g.get("home")]:
                if not t:
                    continue
                k = canon(t)
                vals[k].append(tq)
                names.setdefault(k, t)
    out = {}
    for k, arr in vals.items():
        # Map existing game-quality estimate into a conservative preseason team rating.
        avg = sum(arr) / len(arr)
        out[k] = {
            "name": names[k],
            "seed": max(45, min(95, 45 + avg * .52))
        }
    return out

def parse_week(cid, wk):
    cls = CLASSES[cid]
    url = f"{BASE}?cid={cid}&wk={wk}&yr={YEAR}"
    resp = get(url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        line = " ".join(tr.stripped_strings)
        line = re.sub(r"\s+", " ", line).strip()
        if re.match(r"^\d{1,2}-[A-Z][a-z]{2}\s+", line):
            rows.append(line)
    if not rows:
        rows = [
            re.sub(r"\s+", " ", x).strip()
            for x in soup.get_text("\n").splitlines()
            if re.match(r"^\s*\d{1,2}-[A-Z][a-z]{2}\s+", x)
        ]

    games = []
    for line in rows:
        # Supports both upcoming 0-0 rows and completed scores such as 35 - 21.
        m = re.match(
            r"^(\d{1,2})-([A-Z][a-z]{2})\s+(.+?)\s+(\d+)\s*-\s*(\d+)\s+(at|vs)\s+(.+?)\s+(\d{1,2}:\d{2}\s*(?:AM|PM))$",
            line, re.I
        )
        if not m:
            continue
        day, mon, team, s1, s2, rel, tail, tm = m.groups()
        tail = re.sub(
            r"\s+(?:[1-6]A(?:\s*-\s*(?:\d+|Ind\.?))?|11M(?:Div\.\s*\d+\s*-\s*\d+)?|SPC\s*-\s*\d+A)\s*$",
            "", tail, flags=re.I
        )
        tail = re.sub(r"\(at\s+[^)]+\)", "", tail, flags=re.I)
        opp = re.sub(r"\s+", " ", tail).strip()
        if not opp or opp.lower() == "open":
            continue
        dt = datetime.strptime(f"{day}-{mon}-{YEAR}", "%d-%b-%Y")
        games.append({
            "date": dt,
            "team": team.strip(),
            "opp": opp,
            "teamScore": int(s1),
            "oppScore": int(s2),
            "relation": rel.lower(),
            "class": cls,
            "source": url,
        })
    return games

def completed_results():
    now = datetime.now()
    raw = []
    class_by_team = {}
    for wk in range(0, 11):
        for cid, cls in CLASSES.items():
            for g in parse_week(cid, wk):
                class_by_team[canon(g["team"])] = cls
                raw.append(g)

    dedup = {}
    for g in raw:
        k1, k2 = canon(g["team"]), canon(g["opp"])
        key = (g["date"].date().isoformat(), tuple(sorted([k1, k2])))
        # 0-0 means scheduled/not yet final in the 2026 pages.
        if g["teamScore"] == 0 and g["oppScore"] == 0:
            continue
        if g["date"] > now:
            continue
        if key not in dedup or g["relation"] == "vs":
            dedup[key] = g
    return list(dedup.values()), class_by_team

def result_rows(results):
    rows = defaultdict(list)
    display = {}
    for g in results:
        a, b = canon(g["team"]), canon(g["opp"])
        display.setdefault(a, g["team"])
        display.setdefault(b, g["opp"])
        sa, sb = g["teamScore"], g["oppScore"]
        rows[a].append({"opp": b, "pf": sa, "pa": sb, "date": g["date"]})
        rows[b].append({"opp": a, "pf": sb, "pa": sa, "date": g["date"]})
    return rows, display

def result_score(pf, pa):
    if pf > pa: return 100
    if pf < pa: return 0
    return 50

def build_rankings():
    seeds = preseason_seeds()
    results, classes = completed_results()
    rows, display = result_rows(results)

    teams = set(seeds) | set(rows)
    rating = {t: seeds.get(t, {}).get("seed", 60.0) for t in teams}

    # Iterative opponent adjustment.
    metrics = {}
    for _ in range(8):
        new_rating = {}
        for t in teams:
            games = sorted(rows.get(t, []), key=lambda x: x["date"])
            seed = seeds.get(t, {}).get("seed", 60.0)
            if not games:
                new_rating[t] = seed
                metrics[t] = {
                    "games":0, "wins":0, "losses":0, "ties":0,
                    "sos":50.0, "margin":0.0, "recent":50.0, "actual":seed
                }
                continue

            wins = sum(1 for g in games if g["pf"] > g["pa"])
            losses = sum(1 for g in games if g["pf"] < g["pa"])
            ties = len(games) - wins - losses
            win_pct = (wins + .5 * ties) / len(games) * 100

            opp_ratings = [rating.get(g["opp"], 60.0) for g in games]
            sos = sum(opp_ratings) / len(opp_ratings)
            # Convert ratings roughly to a 0-100 component around 50-95.
            sos_component = max(25, min(100, 20 + sos * .85))

            margins = [max(-35, min(35, g["pf"] - g["pa"])) for g in games]
            avg_margin = sum(margins) / len(margins)
            margin_component = max(0, min(100, 50 + avg_margin * 1.35))

            recent_games = games[-3:]
            recent = sum(result_score(g["pf"], g["pa"]) for g in recent_games) / len(recent_games)

            actual = (
                .35 * win_pct +
                .30 * sos_component +
                .20 * margin_component +
                .15 * recent
            )

            # Phase out preseason influence over the first five completed games.
            actual_weight = min(1.0, len(games) / 5.0)
            blended = seed * (1 - actual_weight) + actual * actual_weight
            new_rating[t] = max(0, min(100, blended))
            metrics[t] = {
                "games":len(games), "wins":wins, "losses":losses, "ties":ties,
                "sos":round(sos_component,1),
                "margin":round(avg_margin,1),
                "recent":round(recent,1),
                "actual":round(actual,1),
            }
        rating = new_rating

    records = []
    for t in teams:
        m = metrics.get(t, {})
        rec = f'{m.get("wins",0)}-{m.get("losses",0)}'
        if m.get("ties",0):
            rec += f'-{m["ties"]}'
        records.append({
            "teamId": t,
            "team": display.get(t) or seeds.get(t, {}).get("name") or t.title(),
            "class": classes.get(t, ""),
            "record": rec,
            "gamesPlayed": m.get("games",0),
            "rating": round(rating[t],1),
            "strengthOfSchedule": m.get("sos",50.0),
            "avgScoringMargin": m.get("margin",0.0),
            "recentForm": m.get("recent",50.0),
            "preseasonSeed": round(seeds.get(t, {}).get("seed",60.0),1),
        })

    records.sort(key=lambda x: (-x["rating"], x["team"]))
    for i, r in enumerate(records, 1):
        r["stateRank"] = i

    by_class = defaultdict(list)
    for r in records:
        if r["class"]:
            by_class[r["class"]].append(r)
    for cls, arr in by_class.items():
        arr.sort(key=lambda x: (-x["rating"], x["team"]))
        for i, r in enumerate(arr, 1):
            r["classRank"] = i

    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "actualPerformance": "35% opponent-adjusted win/loss, 30% strength of schedule, 20% capped scoring margin, 15% recent performance",
            "preseasonBlend": "Preseason seed influence phases out over the first five completed games",
            "note": "Lone Star Rating is independently calculated from game results and is not PigskinPrep's proprietary rating."
        },
        "teams": records,
    }

if __name__ == "__main__":
    data = build_rankings()
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f'Wrote {len(data["teams"]):,} team rankings to {OUT}.')
