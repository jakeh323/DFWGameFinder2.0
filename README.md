# Texas HS Game Finder — Resilient Statewide Build

This version hardens `update_schedule.py` against temporary source-site disconnects.

Changes:
- Automatic HTTP retry/backoff
- Retries 429 and 5xx responses
- Skips a single unreachable weekly/classification page instead of killing the whole run
- Keeps processing the remaining statewide schedule pages
- Safety threshold prevents a bad partial scrape from overwriting a good `season.json`

Run:
GitHub > Actions > Build Full Texas Schedule > Run workflow

If one page temporarily fails, the workflow should still finish.
