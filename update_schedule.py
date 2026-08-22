import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

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
}

# Texas public-school points from NCES EDGE.
NCES_URL = (
    "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/"
    "EDGE_GEOCODE_PUBLICSCH_2324/MapServer/0/query"
)

SESSION = requests.Session()
retry = Retry(
    total=8,
    connect=8,
    read=8,
    status=8,
    backoff_factor=1.5,
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
    for attempt in range(1, 7):
        try:
            r = SESSION.get(url, params=params, timeout=(15, 45))
            if r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            if r.status_code == 429:
                time.sleep(min(30, attempt * 5))
                continue
            r.raise_for_status()
            time.sleep(0.35)
            return r
        except (requests.RequestException, ConnectionError) as e:
            last_error = e
            wait = min(45, 2 ** attempt)
            print(f"Request failed ({attempt}/6): {url} -> {e}. Retrying in {wait}s...")
            time.sleep(wait)
    print(f"WARNING: skipping unreachable source after retries: {url} ({last_error})")
    return None

def load_nces():
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
            r"^(\d{1,2})-([A-Z][a-z]{2})\s+(.+?)\s+0\s*-\s*0\s+(at|vs)\s+(.+?)\s+(\d{1,2}:\d{2}\s*(?:AM|PM))$",
            line,re.I
        )
        if not m:
            continue
        day,mon,team,rel,tail,time_s=m.groups()
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

def main():
    print("Loading Texas public-school locations from NCES...")
    schools=load_nces()
    locate=make_locator(schools)
    print(f"Loaded {len(schools):,} school points.")

    season={k:{**v,"games":[]} for k,v in WEEKS.items()}
    class_by_team={}
    raw=[]

    # Fetch six statewide pages per week = only 60 pages total.
    for wk in range(1,11):
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
            venue=opp_loc
        else:
            away,home=g["opp"],g["team"]
            venue=team_loc

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
            "time":g["time"],
            "venue":f'{venue["city"]}, TX',
            "lat":venue["lat"],"lng":venue["lng"],
            "teamQuality":tq,"recruitTalent":rt,
            "matchupQuality":mq,"importance":imp,
            "source":g["source"]
        })

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
