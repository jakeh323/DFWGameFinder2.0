import json
from pathlib import Path

# This file intentionally keeps recruit data separate from the schedule index.
# Add verified players to recruits.json as recruiting updates are confirmed.
# The website automatically matches school names and recalculates Recruit Talent.

p = Path("recruits.json")
data = json.loads(p.read_text(encoding="utf-8"))
data["players"] = sorted(
    data["players"],
    key=lambda x: (-x.get("rating", 0), x.get("name", ""))
)
p.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"Normalized {len(data['players'])} recruits.")
