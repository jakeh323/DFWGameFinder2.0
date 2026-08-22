# Texas HS Game Finder — Weekly Full Index

This version fixes the sparse Houston/Tyler/etc. problem by sourcing PigskinPrep's
statewide WEEKLY schedule pages instead of a hand-curated season file.

The updater downloads 6A, 5A, 4A, 3A, 2A and 1A weekly schedules for all ten
regular-season weeks, deduplicates the games, matches home schools to NCES public
school coordinates, and rewrites season.json.

## Important first step

After uploading this version to GitHub, run:

GitHub > Actions > Build Full Texas Schedule > Run workflow

The website will remain on the older season.json until that workflow completes and
commits the newly generated file.

No API key is required.

The radius on the website remains a hard boundary.
