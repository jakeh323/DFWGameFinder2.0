import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

YEAR = 2026
OUT = Path("season.json")
BASE = "https://www.pigskinprep.com/showweeklyscoresdetail.asp"

# PigskinPrep public weekly schedule classification IDs.
CLASSES = {
    8: "6A",
    1: "5A",
    2: "4A",
    3: "3A",
    4: "2A",
    5: "1A",
}

WEEKS = {
    "1": {"label":"Week 1 • Aug. 27–29, 2026","start":"2026-08-27","end":"2026-08-29"},
    "2": {"label":"Week 2 • Sep. 3–5, 2026","start":"2026-09-03","end":"2026-09-05"},
    "3": {"label":"Week 3 • Sep. 10–12, 2026","start":"2026-09-10","end":"2026-09-12"},
    "4": {"label":"Week 4 • Sep. 17–19, 2026","start":"2026-09-17","end":"2026-09-19"},
    "5": {"label":"Week 5 • Sep. 24–26, 2026","start":"2026-09-24","end":"2026-09-26"},
    "6": {"label":"Week 6 • Oct. 1–3, 2026","start":"2026-10-01","end":"2026-10-03"},
    "7": {"label":"Week 7 • Oct. 8–10, 2026","start":"2026-10-08","end":"2026-10-10"},
    "8": {"label":"Week 8 • Oct. 15–17, 2026","start":"2026-10-15","end":"2026-10-17"},
    "9": {"label":"Week 9 • Oct. 22–24, 2026","start":"2026-10-22","end":"2026-10-24"},
    "10":{"label":"Week 10 • Oct. 29–31, 2026","start":"2026-10-29","end":"2026-10-31"},
    "11":{"label":"Week 11 • Nov. 5–7, 2026","start":"2026-11-05","end":"2026-11-07"},
    "12":{"label":"Bi-District • Nov. 12–14, 2026","start":"2026-11-12","end":"2026-11-14"},
    "13":{"label":"Area • Nov. 19–21, 2026","start":"2026-11-19","end":"2026-11-21"},
    "14":{"label":"Regional Round • Nov. 26–28, 2026","start":"2026-11-26","end":"2026-11-28"},
    "15":{"label":"Regional / State Quarterfinals • Dec. 3–5, 2026","start":"2026-12-03","end":"2026-12-05"},
    "16":{"label":"State Semifinals • Dec. 9–12, 2026","start":"2026-12-09","end":"2026-12-12"},
    "17":{"label":"State Championships • Dec. 16–19, 2026","start":"2026-12-16","end":"2026-12-19"},
}

# Texas public-school points from NCES EDGE.
NCES_URL = (
    "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/"
    "EDGE_GEOCODE_PUBLICSCH_2324/MapServer/0/query"
)

SESSION = requests.Session()
retry = Retry(
    total=1,
    connect=1,
    read=0,
    status=1,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; TexasHSGameFinder/1.2; +https://github.com/)"
})

# Local program strength/talent overrides. Unknown schools get classification baselines.
ELITE = {
    "duncanville":(99,99),"desoto":(99,99),"north crowley":(97,97),
    "galena park north shore":(98,98),"north shore":(98,98),
    "humble atascocita":(96,96),"atascocita":(96,96),
    "south oak cliff":(96,96),"allen":(94,94),"southlake carroll":(94,94),
    "austin westlake":(95,95),"westlake":(95,95),"lake travis":(92,92),
    "aledo":(92,92),"denton guyer":(92,92),"prosper":(90,90),
    "katy":(90,90),"humble summer creek":(92,92),"summer creek":(92,92),
    "schertz cibolo steele":(93,93),"steele":(93,93),
    "smithson valley":(89,89),"austin vandegrift":(90,90),
    "vandegrift":(90,90),"coppell":(87,87),"waxahachie":(89,89),
    "lancaster":(91,91),"longview":(88,88),"carthage":(88,88),
    "argyle":(88,88),"frisco lone star":(87,87),"denton ryan":(87,87),
    "highland park":(86,86),"lovejoy":(83,83),"celina":(86,86),
    "red oak":(84,84),"cedar hill":(86,86),"rockwall":(85,85),
    "rockwall heath":(84,84),"midland legacy":(83,83),"odessa permian":(82,82),
}

BASELINE = {
    "6A":(72,60),"5A":(68,56),"4A":(63,52),
    "3A":(58,49),"2A":(54,46),"1A":(50,43)
}

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&"," and ")
    s = re.sub(r"\b(high school|senior high school|h s)\b"," ",s)
    s = re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def get(url, params=None):
    last_error = None
    for attempt in range(1, 3):
        try:
            r = SESSION.get(url, params=params, timeout=(5, 12))
            if r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            if r.status_code == 429:
                time.sleep(attempt * 2)
                continue
            r.raise_for_status()
            return r
        except (requests.RequestException, ConnectionError) as e:
            last_error = e
            wait = attempt * 2
            print(f"Request failed ({attempt}/2): {url} -> {e}. Retrying in {wait}s...", flush=True)
            time.sleep(wait)
    print(f"WARNING: skipping unreachable source after retries: {url} ({last_error})")
    return None

def load_cached_city_locations():
    """Use the already-published season to locate postseason stadium cities.

    During playoffs a normal refresh should not depend on a slow NCES bulk query.
    The old regular-season games already contain thousands of Texas venue
    coordinates. Aggregate them by venue city and preserve their existing
    coordinates; use NCES only on the initial full-season build.
    """
    if not OUT.exists() or datetime.now().date() < datetime(YEAR, 11, 8).date():
        return []
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if sum(len(w.get("games", [])) for w in old.values()) < 250:
            return []
        cities = {}
        for wk in old.values():
            for game in wk.get("games", []):
                venue = (game.get("venue") or "").strip()
                lat, lng = game.get("lat"), game.get("lng")
                if lat is None or lng is None or not venue:
                    continue
                parts = [p.strip() for p in venue.split(",")]
                if len(parts) < 2 or parts[-1].upper() not in ("TX", "TEXAS"):
                    continue
                city = parts[-2]
                key = norm(city)
                if not key or key in ("texas", "tx"):
                    continue
                entry = cities.setdefault(key, {"name": city, "lat": [], "lng": []})
                entry["lat"].append(float(lat))
                entry["lng"].append(float(lng))

        # Seed regional hubs that can host neutral-site postseason games.
        hubs = Path("locations.json")
        if hubs.exists():
            for name, coords in json.loads(hubs.read_text(encoding="utf-8")).items():
                key = norm(name)
                if key not in cities and isinstance(coords, list) and len(coords) == 2:
                    cities[key] = {"name": name, "lat": [float(coords[0])],
                                   "lng": [float(coords[1])]}

        if len(cities) < 50:
            return []
        schools = []
        for key, city in cities.items():
            schools.append({"name": city["name"], "city": city["name"],
                            "lat": sum(city["lat"]) / len(city["lat"]),
                            "lng": sum(city["lng"]) / len(city["lng"]),
                            "key": key})
        print(f"Reusing {len(schools)} cached venue cities; skipping slow NCES download.",
              flush=True)
        return schools
    except (OSError, ValueError, TypeError) as exc:
        print(f"WARNING: cached venue coordinates not available: {exc}", flush=True)
        return []


def load_nces():
    if os.environ.get("FORCE_NCES_REFRESH") != "1":
        cached = load_cached_city_locations()
        if cached:
            return cached
    params = {
        "where":"STATE='TX'",
        "outFields":"NAME,CITY,STATE,LAT,LON",
        "returnGeometry":"false",
        "f":"json",
        "resultRecordCount":2000,
        "resultOffset":0,
    }
    out=[]
    page_no=0

    while True:
        page_no += 1
        resp = get(NCES_URL, params)

        if resp is None:
            print("WARNING: NCES request failed after retries. Using whatever school locations were already loaded.")
            break

        body = resp.text.strip()
        ctype = (resp.headers.get("content-type") or "").lower()

        if not body:
            print(f"WARNING: NCES returned an empty body on page {page_no}. Stopping pagination.")
            break

        if "json" not in ctype and not body.startswith("{"):
            print(
                f"WARNING: NCES returned non-JSON content on page {page_no} "
                f"(content-type={ctype!r}, first bytes={body[:80]!r}). "
                "Stopping pagination instead of crashing."
            )
            break

        try:
            data = resp.json()
        except ValueError as e:
            print(f"WARNING: NCES JSON decode failed on page {page_no}: {e}. Stopping pagination.")
            break

        feats = data.get("features", [])
        if not isinstance(feats, list):
            print(f"WARNING: NCES response did not contain a feature list on page {page_no}.")
            break

        for f in feats:
            a=f.get("attributes",{})
            lat=a.get("LAT")
            lon=a.get("LON")
            if lat is None or lon is None:
                continue
            out.append({
                "name":a.get("NAME",""),
                "city":a.get("CITY",""),
                "lat":lat,
                "lng":lon,
                "key":norm(a.get("NAME",""))
            })

        if not data.get("exceededTransferLimit") or not feats:
            break

        params["resultOffset"] += len(feats)

    if out:
        return out

    # Last-resort fallback: use any geolocated teams already present in the current
    # season.json so the workflow can still proceed instead of dying on NCES.
    fallback=[]
    try:
        if OUT.exists():
            current=json.loads(OUT.read_text(encoding="utf-8"))
            seen=set()
            for wk in current.values():
                for g in wk.get("games",[]):
                    lat=g.get("lat")
                    lng=g.get("lng")
                    if lat is None or lng is None:
                        continue
                    for team in [g.get("home"), g.get("away")]:
                        if not team:
                            continue
                        k=norm(team)
                        if k in seen:
                            continue
                        seen.add(k)
                        fallback.append({
                            "name":team,
                            "city":(g.get("venue") or "").replace(", TX",""),
                            "lat":lat,
                            "lng":lng,
                            "key":k,
                        })
    except Exception as e:
        print(f"WARNING: could not build location fallback from existing season.json: {e}")

    if fallback:
        print(f"WARNING: NCES unavailable; using {len(fallback)} fallback school locations from current season.json.")
        return fallback

    raise RuntimeError("NCES returned no usable school-location data and no fallback locations were available.")

def make_locator(schools):
    keys=[s["key"] for s in schools]
    exact={}
    for s in schools:
        exact.setdefault(s["key"],s)
    cache={}

    def locate(team):
        k=norm(team)
        if k in cache: return cache[k]
        if k in exact:
            cache[k]=exact[k]
            return exact[k]

        # Try full name and suffixes because PigskinPrep often prefixes city names.
        queries=[k]
        parts=k.split()
        if len(parts)>=2: queries.append(" ".join(parts[-2:]))
        if len(parts)>=3: queries.append(" ".join(parts[-3:]))

        cand=[]
        for q in queries:
            for _,score,idx in process.extract(q,keys,scorer=fuzz.WRatio,limit=6):
                cand.append((score,schools[idx]))
        cand.sort(key=lambda x:x[0],reverse=True)
        best=cand[0][1] if cand and cand[0][0]>=80 else None
        cache[k]=best
        return best
    return locate

def parse_week_page(cid, week):
    url=f"{BASE}?cid={cid}&wk={week}&yr={YEAR}"
    resp=get(url)
    if resp is None:
        return []
    soup=BeautifulSoup(resp.text,"html.parser")

    rows=[]
    # PigskinPrep pages are table-based. Reading each tr separately is much cleaner.
    for tr in soup.find_all("tr"):
        line=" ".join(tr.stripped_strings)
        line=re.sub(r"\s+"," ",line).strip()
        if re.match(r"^\d{1,2}-[A-Z][a-z]{2}\s+",line):
            rows.append(line)

    # Fallback for alternate page markup.
    if not rows:
        text=soup.get_text("\n")
        rows=[
            re.sub(r"\s+"," ",x).strip()
            for x in text.splitlines()
            if re.match(r"^\s*\d{1,2}-[A-Z][a-z]{2}\s+",x)
        ]

    games=[]
    for line in rows:
        # Example:
        # 28-Aug Lancaster 0 - 0 at Galena Park North Shore 6A - 17 7:00 PM
        # 28-Aug Mansfield Summit 0 - 0 vs Red Oak (at Lancaster) 6A - 11 7:00 PM
        m=re.match(
            r"^(\d{1,2})-([A-Z][a-z]{2})\s+(.+?)\s+(\d+)\s*-\s*(\d+)\s+(at|vs)\s+(.+?)\s+(\d{1,2}:\d{2}\s*(?:AM|PM))$",
            line,re.I
        )
        if not m:
            continue
        day,mon,team,team_score,opp_score,rel,tail,time_s=m.groups()
        team_score=int(team_score)
        opp_score=int(opp_score)
        # Remove trailing classification label from opponent side.
        tail=re.sub(r"\s+(?:[1-6]A(?:\s*-\s*(?:\d+|Ind\.?))?|11M(?:Div\.\s*\d+\s*-\s*\d+)?|SPC\s*-\s*\d+A)\s*$","",tail,flags=re.I)
        neutral=None
        nm=re.search(r"\(at\s+([^)]+)\)",tail,re.I)
        if nm:
            neutral=nm.group(1).strip()
            tail=re.sub(r"\(at\s+[^)]+\)","",tail,flags=re.I)
        opp=re.sub(r"\s+"," ",tail).strip()
        if not opp or opp.lower()=="open":
            continue
        date_obj=datetime.strptime(f"{day}-{mon}-{YEAR}","%d-%b-%Y")
        games.append({
            "team":team.strip(),"rel":rel.lower(),"opp":opp,
            "team_score":team_score,"opp_score":opp_score,
            "date_obj":date_obj,"time":time_s.upper(),
            "neutral":neutral,"source":url
        })
    return games

def strength(team, cls):
    k=norm(team)
    for key,(q,t) in ELITE.items():
        if key in k or k in key:
            return q,t
    return BASELINE[cls]

def components(a,b,cls_a,cls_b):
    qa,ta=strength(a,cls_a); qb,tb=strength(b,cls_b)
    tq=round((qa+qb)/2)
    rt=round((ta+tb)/2)
    mq=max(55,round(98-abs(qa-qb)*1.25-abs(ta-tb)*.35))
    imp=68
    if qa>=85 and qb>=85: imp+=10
    if rt>=88: imp+=8
    return tq,rt,min(100,mq),min(100,imp)

def game_score(g):
    return round(
        g["teamQuality"]*.40+g["recruitTalent"]*.35+
        g["matchupQuality"]*.15+g["importance"]*.10
    )


# Playoff schedules are posted round-by-round, not before the season.
# txhsfb.net publishes public weekly matchups, kickoff times, and neutral-site stadiums.
PLAYOFF_BASE = "https://txhsfb.net/"
# Actual published statewide schedules. The same site changes its visible
# article headings as the postseason progresses, so prefer stable article URLs.
PUBLISHED_PLAYOFF_PAGES = {
    12: "https://txhsfb.net/?p=29290",
    13: "https://txhsfb.net/?p=29577",
    14: "https://txhsfb.net/?p=30121",
}

def playoff_page(week):
    if week in PUBLISHED_PLAYOFF_PAGES:
        return PUBLISHED_PLAYOFF_PAGES[week]

    # The publisher changes titles and search ranking each week. Try both
    # detailed and broad searches, then the recent-posts homepage, matching
    # the actual article title rather than relying on any one search result.
    search_urls = [
        f"https://txhsfb.net/?s=2026+Playoffs+Week+{week}+Schedule",
        f"https://txhsfb.net/?s=Week+{week}+Schedule",
        PLAYOFF_BASE,
    ]
    for search_url in search_urls:
        resp = get(search_url)
        if resp is None:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href]"):
            label = re.sub(r"\\s+", " ", a.get_text(" ", strip=True))
            if not (re.search(rf"\\b{YEAR}\\b", label)
                    and re.search(rf"\\bWeek\\s+{week}\\b", label, re.I)
                    and re.search(r"\\bSchedules?\\b", label, re.I)):
                continue
            href = urljoin(PLAYOFF_BASE, a.get("href", ""))
            if urlparse(href).netloc.lower() == "txhsfb.net":
                print(f"Found playoff week {week} article: {href}", flush=True)
                return href
    return None

def parse_playoff_week(week):
    url = playoff_page(week)
    if not url:
        print(f"Playoff week {week}: source schedule not yet found.", flush=True)
        return []
    resp = get(url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    games = []
    seen = set()
    # Schedule rows may use either "11/20" or "Nov. 20" date notation.
    # Score result lines do not contain "vs." and are intentionally excluded.
    line_pattern = re.compile(
        r"^(.+?)\s+vs\.\s+(.+?),\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun),"
        r"\s*((?:\d{1,2}/\d{1,2})|(?:[A-Za-z]{3,9}\.?\s+\d{1,2})),"
        r"\s*(\d{1,2}:\d{2}\s*[AP]M),\s*(.+),\s*([^,]+)$",
        re.I,
    )
    for line in soup.get_text("\n").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        m = line_pattern.match(line)
        if not m:
            continue
        away, home, day_name, date_str, kickoff, stadium, city = [
            v.strip() for v in m.groups()
        ]
        dt = None
        clean_date = date_str.replace(".", "")
        for fmt in ("%m/%d/%Y", "%b %d %Y", "%B %d %Y"):
            try:
                dt = datetime.strptime(f"{clean_date} {YEAR}" if fmt != "%m/%d/%Y"
                                       else f"{clean_date}/{YEAR}", fmt)
                break
            except ValueError:
                pass
        if dt is None:
            continue
        period = WEEKS[str(week)]
        if not (period["start"] <= dt.strftime("%Y-%m-%d") <= period["end"]):
            continue
        key = (tuple(sorted([norm(away), norm(home)])), dt.strftime("%Y-%m-%d"))
        if key in seen:
            continue
        seen.add(key)
        games.append({
            "away": away, "home": home, "date_obj": dt,
            "time": kickoff.upper(), "stadium": stadium, "city": city,
            "source": url,
        })
    print(f"Playoff week {week}: parsed {len(games)} games from {url}", flush=True)
    # A real, published page with zero parseable games should not silently pass
    # if the page contains game-matchup lines: show a diagnostic.
    if not games and re.search(r"\bvs\.\s+.+?,\s*(?:Thu|Fri|Sat),", soup.get_text(" "), re.I):
        print(f"WARNING: week {week} source has matchups, but none matched the parser.", flush=True)
    return games

def add_playoff_games(season, schools, class_by_team):
    by_city = {}
    for school in schools:
        key = norm(school.get("city"))
        if key and school.get("lat") is not None and school.get("lng") is not None:
            by_city.setdefault(key, []).append(school)
    for wk in range(12, 18):
        round_games = parse_playoff_week(wk)
        if wk in PUBLISHED_PLAYOFF_PAGES and not round_games:
            raise RuntimeError(f"Published playoff Week {wk} has zero parsed games; refusing to overwrite the saved schedule.")
        for g in round_games:
            # Neutral-site location: approximate stadium city, NEVER use home-team campus.
            city_points = by_city.get(norm(g["city"]), [])
            if not city_points:
                continue
            lat = sum(float(p["lat"]) for p in city_points) / len(city_points)
            lng = sum(float(p["lng"]) for p in city_points) / len(city_points)
            cls_a = class_by_team.get(norm(g["away"]), "4A")
            cls_b = class_by_team.get(norm(g["home"]), "4A")
            tq, rt, mq, imp = components(g["away"], g["home"], cls_a, cls_b)
            # Postseason games receive a modest significance uplift.
            imp = min(100, imp + 12 + (wk - 12) * 3)
            dt = g["date_obj"]
            season[str(wk)]["games"].append({
                "away": g["away"], "home": g["home"],
                "date": dt.strftime("%A, %b. %d").replace(" 0", " "),
                "dateISO": dt.strftime("%Y-%m-%d"), "time": g["time"],
                "status": "Scheduled", "awayScore": None, "homeScore": None,
                "winner": None, "venue": f'{g["stadium"]}, {g["city"]}, TX',
                "lat": lat, "lng": lng, "locationApproximate": True,
                "playoffRound": WEEKS[str(wk)]["label"].split(" • ")[0],
                "teamQuality": tq, "recruitTalent": rt,
                "matchupQuality": mq, "importance": imp,
                "source": g["source"],
            })

def main():
    print("Loading Texas public-school locations from NCES...")
    schools=load_nces()
    locate=make_locator(schools)
    print(f"Loaded {len(schools):,} school points.")

    season={k:{**v,"games":[]} for k,v in WEEKS.items()}
    class_by_team={}
    raw=[]

    # Reuse completed regular-season weeks during postseason rebuilds.
    # Otherwise every daily refresh makes 60 unnecessary HTTP requests.
    old_season={}
    if OUT.exists():
        try:
            old_season=json.loads(OUT.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"WARNING: cannot reuse previous season index: {exc}", flush=True)
    completed_week_keys=[
        str(wk) for wk in range(1,11)
        if old_season.get(str(wk),{}).get("games")
    ]
    reuse_regular=(datetime.now().date() >= datetime(YEAR,11,8).date()
                   and len(completed_week_keys)==10)
    if reuse_regular:
        for key in completed_week_keys:
            season[key]["games"]=old_season[key]["games"]
        print("Reusing saved Weeks 1–10; refreshing Week 11 and playoffs.", flush=True)

    # On a fresh build, fetch all 11 weeks. On postseason refresh, only Week 11.
    for wk in (range(11,12) if reuse_regular else range(1,12)):
        print(f"Week {wk}...")
        for cid,cls in CLASSES.items():
            try:
                page_games=parse_week_page(cid,wk)
            except Exception as e:
                print(f"WARNING: week {wk} class {cls} failed and will be skipped: {e}")
                page_games=[]
            for g in page_games:
                g["cls"]=cls
                class_by_team[norm(g["team"])]=cls
                raw.append((str(wk),g))

    # Deduplicate home/away mirror copies.
    dedup={}
    for wk,g in raw:
        key=(wk,g["date_obj"].strftime("%Y-%m-%d"),tuple(sorted([norm(g["team"]),norm(g["opp"])])))
        if key not in dedup or (g["rel"]=="vs" and dedup[key]["rel"]!="vs"):
            dedup[key]=g

    geolocated=0
    skipped=0
    for (wk,_,_),g in dedup.items():
        team_loc=locate(g["team"])
        opp_loc=locate(g["opp"])

        if g["rel"]=="at":
            away,home=g["team"],g["opp"]
            away_score,home_score=g["team_score"],g["opp_score"]
            venue=opp_loc
        else:
            away,home=g["opp"],g["team"]
            away_score,home_score=g["opp_score"],g["team_score"]
            venue=team_loc

        # Public schedule pages use 0-0 for games that have not been played yet.
        # Treat non-zero posted scores on past/current dates as final results.
        today=datetime.now().date()
        game_date=g["date_obj"].date()
        is_final=(away_score!=0 or home_score!=0) and game_date<=today

        if venue is None:
            skipped+=1
            continue

        geolocated+=1
        cls_b=class_by_team.get(norm(g["opp"]),"4A")
        tq,rt,mq,imp=components(g["team"],g["opp"],g["cls"],cls_b)

        season[wk]["games"].append({
            "away":away,
            "home":home,
            "date":g["date_obj"].strftime("%A, %b. %d").replace(" 0"," "),
            "dateISO":g["date_obj"].strftime("%Y-%m-%d"),
            "time":g["time"],
            "status":"Final" if is_final else "Scheduled",
            "awayScore":away_score if is_final else None,
            "homeScore":home_score if is_final else None,
            "winner":(
                away if is_final and away_score>home_score else
                home if is_final and home_score>away_score else
                "Tie" if is_final else None
            ),
            "venue":f'{venue["city"]}, TX',
            "lat":venue["lat"],"lng":venue["lng"],
            "teamQuality":tq,"recruitTalent":rt,
            "matchupQuality":mq,"importance":imp,
            "source":g["source"]
        })

    # Populate team classifications from retained results where available.
    # The rank/baseline logic still works if a team is not classified.
    add_playoff_games(season, schools, class_by_team)

    # Keep previously indexed Week 11 and playoff rounds if a source is
    # temporarily unavailable. Week 11's completed scores must not disappear.
    if not season["11"]["games"] and old_season.get("11", {}).get("games"):
        season["11"]["games"] = old_season["11"]["games"]

    # Keep previously indexed playoff rounds if a source is temporarily unavailable.
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            for wk in range(12, 18):
                key = str(wk)
                if not season[key]["games"]:
                    season[key]["games"] = old.get(key, {}).get("games", [])
        except (ValueError, OSError) as exc:
            print(f"WARNING: existing playoff fallback unavailable: {exc}")

    for wk in season.values():
        wk["games"].sort(key=game_score,reverse=True)

    counts={k:len(v["games"]) for k,v in season.items()}
    total=sum(counts.values())
    if total < 250:
        raise RuntimeError(
            f"Only {total} games were indexed, which is below the safety threshold. "
            "Existing season.json was NOT overwritten."
        )
    OUT.write_text(json.dumps(season,indent=2),encoding="utf-8")
    print("Games indexed by week:", counts)
    print(f"Geolocated: {geolocated:,}; skipped without confident location: {skipped:,}")

if __name__=="__main__":
    main()
