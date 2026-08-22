# Texas HS Game Finder — NCES-Resilient Build

This version fixes the JSONDecodeError from NCES.

Changes:
- Verifies NCES response body/content type before parsing JSON.
- Stops pagination safely if NCES returns HTML/empty content.
- Keeps any successfully loaded NCES pages.
- Falls back to coordinates already present in season.json instead of crashing.
- Existing schedule-source retry/backoff remains enabled.

Run the same GitHub Action again:
Actions > Build Full Texas Schedule > Run workflow
