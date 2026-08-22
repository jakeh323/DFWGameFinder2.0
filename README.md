# Texas HS Game Finder — Full Schedule Index

This version fixes the small-area-data problem.

## Data sources

**Schedules:** PigskinPrep's free 2026 team/district schedule pages.
PigskinPrep's 2026 schedule index reports more than 1,500 Texas teams in the system.

**Locations:** NCES EDGE public-school point locations. NCES states this location
dataset is in the public domain.

## How it works

`update_schedule.py`:
1. Finds every free PigskinPrep 2026 public-school class/district schedule page.
2. Parses all 6A–1A regular-season games.
3. Deduplicates home/away copies of the same matchup.
4. Downloads Texas public-school coordinates from NCES.
5. Fuzzy-matches schedule school names to NCES schools.
6. Assigns the game's venue coordinate from the home school.
7. Writes a much larger `season.json`.

The website then applies the user's selected location/radius as a HARD boundary.
It does not widen the range.

## Free weekly refresh

The included GitHub Action runs every Tuesday morning and can also be launched
manually from GitHub > Actions > Refresh Texas Football Schedule > Run workflow.

When `season.json` changes, GitHub commits it. Vercel sees the GitHub commit and
redeploys automatically.

## Files

- index.html
- season.json
- locations.json
- texas-hs-game-finder-logo.png
- update_schedule.py
- requirements.txt
- vercel.json
- .github/workflows/refresh-schedule.yml

No OpenAI API key or paid sports-data API is required.

## Ranking note

Schedules and locations are bulk-sourced. The 40/35/15/10 ranking inputs use
classification baselines plus a local blue-chip program table. That ranking layer
can be improved separately without reducing schedule coverage.
