import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RECRUITS_FILE = Path("recruits.json")
SEASON_FILE = Path("season.json")

COLLEGES = {
    "Texas": "texas-longhorns",
    "Texas A&M": "texas-am-aggies",
    "Texas Tech": "texas-tech-red-raiders",
    "TCU": "tcu-horned-frogs",
    "Baylor": "baylor-bears",
    "SMU": "smu-mustangs",
    "Houston": "houston-cougars",
    "North Texas": "north-texas-mean-green",
    "UTSA": "utsa-roadrunners",
    "UTEP": "utep-miners",
    "Texas State": "texas-state-bobcats",
    "Sam Houston": "sam-houston-bearkats",
    "Rice": "rice-owls",
    "UIW": "incarnate-word-cardinals",
}

YEARS = [2027, 2028]

session = requests.Session()
retry = Retry(
    total=6, connect=6, read=6, status=6,
    backoff_factor=1.25,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
session.mount("https://", HTTPAdapter(max_retries=retry))
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; LoneStarGameFinder/2.0)"
})

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\b(high school|senior high school|hs)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# Deliberately explicit aliases; no generic substring matching.
ALIASES = {
    "humble atascocita":"atascocita",
    "humble summer creek":"summer creek",
    "galena park north shore":"north shore",
    "austin westlake":"westlake",
    "schertz cibolo steele":"steele",
    "garland lakeview centennial":"lakeview centennial",
    "houston langham creek":"langham creek",
    "spring westfield":"westfield",
    "richmond randle":"randle",
    "fort bend travis":"travis",
    "san antonio brandeis":"brandeis",
    "san antonio sotomayor":"sotomayor",
    "frisco lone star":"lone star",
    "denton guyer":"guyer",
    "denton ryan":"ryan",
    "arlington bowie":"bowie",
    "arlington lamar":"lamar",
    "mansfield summit":"summit",
    "mansfield lake ridge":"lake ridge",
    "prosper walnut grove":"walnut grove",
    "odessa permian":"permian",
    "amarillo palo duro":"palo duro",
}

def canon(s):
    k = norm(s)
    return ALIASES.get(k, k)

def get(url):
    for attempt in range(1, 6):
        try:
            r = session.get(url, timeout=(15, 45))
            r.raise_for_status()
            time.sleep(.3)
            return r
        except requests.RequestException as e:
            if attempt == 5:
                print(f"WARNING: unable to read {url}: {e}")
                return None
            time.sleep(min(20, 2 ** attempt))

def known_schools():
    data = json.loads(SEASON_FILE.read_text(encoding="utf-8"))
    schools = {}
    for wk in data.values():
        for g in wk.get("games", []):
            for team in [g.get("away"), g.get("home")]:
                if not team:
                    continue
                schools.setdefault(canon(team), team)
    # Longest names first prevents Crowley from stealing North Crowley.
    return sorted(schools.items(), key=lambda kv: len(kv[0]), reverse=True)

MASCOTS = [
    "Longhorns","Eagles","Bulldogs","Panthers","Rangers","Mustangs","Tigers",
    "Wildcats","Lions","Jaguars","Patriots","Falcons","Bears","Texans","Dragons",
    "Raiders","Cougars","Warriors","Wolves","Mules","Pirates","Hornets","Indians",
    "Bobcats","Rockets","Cardinals","Spartans","Owls","Broncos","Hawks","Vikings",
    "Knights","Leopards","Yellowjackets","Brahmas","Coyotes","Buffaloes","Bucks",
    "Chaps","Chaparrals","Dons","Scots","Redhawks","Mavericks","Gators","Trojans"
]

def strip_mascot(s):
    x = s.strip()
    for mascot in sorted(MASCOTS, key=len, reverse=True):
        if x.endswith(" " + mascot):
            return x[:-(len(mascot)+1)].strip()
    return x

def prospect_score(state_rank, commitment, watchlist=False):
    # Proprietary Lone Star score, not a copied composite.
    if state_rank is not None:
        if state_rank <= 5: base = 98
        elif state_rank <= 15: base = 95
        elif state_rank <= 30: base = 92
        elif state_rank <= 50: base = 89
        elif state_rank <= 75: base = 86
        elif state_rank <= 100: base = 83
        else: base = 80
    else:
        base = 76

    # Any verified FBS/FCS commitment makes an NR prospect worth surfacing.
    if commitment and commitment != "Uncommitted":
        base += 3
    if watchlist:
        base = max(base, 89)
    return min(100, base)

def star_tier(score):
    if score >= 96: return 5
    if score >= 88: return 4
    return 3

def parse_commitment_page(college, slug, year, schools):
    url = f"https://www.texasfootball.com/team/{slug}/commitments/{year}"
    resp = get(url)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = []
    for a in soup.find_all("a"):
        txt = " ".join(a.stripped_strings)
        if "Verbal Commit" not in txt or "Position Rank:" not in txt:
            continue
        candidates.append(txt)

    # Fallback: extract card-like lines from page text.
    if not candidates:
        for line in soup.get_text("\n").splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if "Verbal Commit" in line and "Position Rank:" in line:
                candidates.append(line)

    out = []
    for txt in candidates:
        txt = re.sub(r"\s+", " ", txt).strip()
        rm = re.match(r"^(#(\d+)|NR)\s+(.+?)\s+Verbal Commit\s+(.+?)Position Rank:", txt, re.I)
        if not rm:
            continue
        rank_token, rank_num, left, position = rm.groups()
        state_rank = int(rank_num) if rank_num else None
        left = left.strip()
        position = position.strip()

        # Identify school from the RIGHT side of the card text.
        # Use explicit school names/aliases only and prefer the longest match,
        # so "North Crowley" cannot collapse into "Crowley".
        school_display = None
        school_canon = None
        left_clean = re.sub(r"\s+", " ", strip_mascot(left)).strip()
        left_norm = norm(left_clean)

        for ck, display in schools:
            candidates = {ck, canon(display), norm(display)}
            matched = False
            for candidate in sorted(candidates, key=len, reverse=True):
                if not candidate:
                    continue
                if left_norm == candidate or left_norm.endswith(" " + candidate):
                    school_canon = ck
                    school_display = display
                    matched = True
                    break
            if matched:
                break

        if not school_display:
            continue

        # Strip the matched school text from the right while preserving the
        # player's original capitalization/punctuation from the page.
        # Try multiple display variants because source pages may include a city prefix.
        school_variants = {
            school_display,
            strip_mascot(school_display),
        }
        # Include known alias spellings that canonicalize to this school.
        for alias_name, alias_target in ALIASES.items():
            if alias_target == school_canon:
                school_variants.add(alias_name)

        name = None
        for variant in sorted(school_variants, key=len, reverse=True):
            # Compare using normalized suffix, but trim from the original string by token count.
            vnorm = norm(variant)
            ltokens = left_clean.split()
            vtokens = variant.split()
            if len(ltokens) <= len(vtokens):
                continue
            suffix = " ".join(ltokens[-len(vtokens):])
            if canon(suffix) == school_canon or norm(suffix) == vnorm:
                candidate_name = " ".join(ltokens[:-len(vtokens)]).strip()
                if candidate_name:
                    name = candidate_name
                    break

        if not name:
            # Fallback by canonical token count, still removing only the rightmost school tokens.
            ltokens = left_clean.split()
            school_token_count = len(school_canon.split())
            if len(ltokens) > school_token_count:
                name = " ".join(ltokens[:-school_token_count]).strip()

        if not name:
            continue

        score = prospect_score(state_rank, college)
        out.append({
            "name": name,
            "school": school_display,
            "schoolId": school_canon,
            "position": position,
            "classYear": year,
            "stateRank": state_rank,
            "commitment": college,
            "prospectScore": score,
            "stars": star_tier(score),
            "tier": "Ranked recruit" if state_rank is not None else "College commit",
            "source": url,
            "sourceType": "public commitment page",
        })
    return out


def repair_existing_name(name, school):
    """
    Remove school/city/alias fragments accidentally appended to a player's name.

    Examples:
      Jalen Brewster Cedar + Cedar Hill -> Jalen Brewster
      Landen Williams Callis Richmond Randle + Richmond Randle -> Landen Williams Callis
      Alexander Herrera Crowley + Crowley -> Alexander Herrera
      Montre Jackson Garland + Lakeview Centennial -> Montre Jackson
    """
    original = re.sub(r"\\s+", " ", name or "").strip()
    if not original:
        return original

    school_id = canon(school)

    # Build every token that can legitimately describe this school:
    # display name + canonical name + all explicit alias spellings.
    school_tokens = set(norm(school).split()) | set(school_id.split())

    for alias_name, alias_target in ALIASES.items():
        if alias_target == school_id:
            school_tokens.update(norm(alias_name).split())

    tokens = original.split()
    if len(tokens) <= 1:
        return original

    # Strip consecutive right-edge school/city/alias tokens.
    cut = len(tokens)
    while cut > 1 and norm(tokens[cut - 1]) in school_tokens:
        cut -= 1

    candidate = " ".join(tokens[:cut]).strip()
    return candidate or original


def clean_player_name(name):
    return re.sub(r"\s+", " ", name or "").strip()

def merge_players(existing, fresh):
    # Existing exact player+school keys preserve correct capitalization and manually verified detail.
    merged = {}
    for p in existing:
        p = dict(p)
        p["name"] = repair_existing_name(p.get("name"), p.get("school"))
        k = (norm(p.get("name")), canon(p.get("school")))
        if not k[0] or not k[1]:
            continue
        p["schoolId"] = canon(p.get("school"))
        if "prospectScore" not in p:
            p["prospectScore"] = p.get("rating", 76)
        merged[k] = p

    # When parser guesses capitalization, attempt to match by school + first/last name tokens.
    for p in fresh:
        school_id = p["schoolId"]
        p_tokens = norm(p["name"]).split()
        matched_key = None
        for k, old in merged.items():
            if k[1] != school_id:
                continue
            old_tokens = norm(old.get("name")).split()
            if p_tokens and old_tokens and p_tokens[0] == old_tokens[0] and p_tokens[-1] == old_tokens[-1]:
                matched_key = k
                break

        if matched_key:
            old = merged[matched_key]
            old["commitment"] = p["commitment"]
            if p.get("stateRank") is not None:
                old["stateRank"] = p["stateRank"]
            old["prospectScore"] = max(
                p["prospectScore"],
                old.get("prospectScore", old.get("rating", 0))
            )
            old["stars"] = star_tier(old["prospectScore"])
            old["tier"] = p["tier"]
            old["source"] = p["source"]
            old["sourceType"] = p["sourceType"]
            old["schoolId"] = school_id
        else:
            k = (norm(p["name"]), school_id)
            merged[k] = p

    return list(merged.values())


def dedupe_players(players):
    grouped = {}

    for raw in players:
        p = dict(raw)
        p["name"] = repair_existing_name(p.get("name"), p.get("school"))
        p["schoolId"] = canon(p.get("school"))

        key = (norm(p.get("name")), p["schoolId"])
        if not key[0] or not key[1]:
            continue

        if key not in grouped:
            grouped[key] = p
            continue

        old = grouped[key]

        def choose_score(x):
            return (
                1 if x.get("stateRank") is not None else 0,
                x.get("prospectScore") or x.get("rating") or 0,
                1 if x.get("commitment") and x.get("commitment") not in ("Uncommitted", "Committed") else 0,
                1 if x.get("position") else 0,
            )

        best, other = (p, old) if choose_score(p) > choose_score(old) else (old, p)
        merged = dict(best)

        for field in [
            "position", "classYear", "stateRank", "nationalRank", "commitment",
            "prospectScore", "rating", "stars", "tier", "source", "sourceType"
        ]:
            if merged.get(field) in (None, "", 0) and other.get(field) not in (None, "", 0):
                merged[field] = other[field]

        if merged.get("commitment") in (None, "", "Committed", "Uncommitted"):
            if other.get("commitment") not in (None, "", "Committed", "Uncommitted"):
                merged["commitment"] = other["commitment"]

        merged["name"] = repair_existing_name(merged.get("name"), merged.get("school"))
        merged["schoolId"] = key[1]
        grouped[key] = merged

    return list(grouped.values())


def main():
    existing_data = json.loads(RECRUITS_FILE.read_text(encoding="utf-8"))
    existing = existing_data.get("players", [])
    schools = known_schools()

    fresh = []
    for year in YEARS:
        for college, slug in COLLEGES.items():
            print(f"Refreshing {college} {year}...")
            fresh.extend(parse_commitment_page(college, slug, year, schools))

    players = merge_players(existing, fresh)

    # Recalculate Lone Star prospect ranking independently.
    # Final cleanup and de-duplication pass.
    players = dedupe_players(players)
    players = [
        p for p in players
        if p.get("name") and norm(p["name"]) != canon(p.get("school"))
    ]

    players.sort(key=lambda p: (
        -(p.get("prospectScore") or 0),
        p.get("stateRank") if p.get("stateRank") is not None else 9999,
        p.get("name","")
    ))
    for i, p in enumerate(players, 1):
        p["loneStarRank"] = i

    out = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "name": "Lone Star Prospect Ranking",
            "note": "Internal ranking based on prospect tier, verified in-state commitment status, and curated watchlist/ranking inputs. It is not a copied recruiting-service composite.",
            "schoolMatching": "Exact school IDs / approved aliases only; no generic substring matching."
        },
        "playerCount": len(players),
        "players": players
    }
    RECRUITS_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Updated {len(players)} unique prospects after cleanup/deduplication; {len(fresh)} commitment records were parsed.")
    bad_suffixes = [
        p for p in players
        if p.get("school") and any(
            norm(p.get("name","")).endswith(" " + tok)
            for tok in norm(p.get("school","")).split()
        )
    ]
    print(f"Potential remaining school-suffix names: {len(bad_suffixes)}")

if __name__ == "__main__":
    main()
