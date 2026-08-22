# Texas HS Game Finder — Strict Radius / Area-First

Core behavior:
1. User selects a preset location.
2. User selects a radius.
3. Radius is a HARD boundary.
4. Only games inside that radius are eligible.
5. Eligible games are ranked using:
   - 40% Team Quality
   - 35% Recruit Talent
   - 15% Matchup Quality
   - 10% Importance
6. Display up to the Top 30.

IMPORTANT:
The app must NOT widen the radius to fill 30 spots.
If an area has fewer than 30 indexed games, the correct solution is to expand
the statewide schedule index with more real games from that same geographic area.

Example:
Tyler + 75 miles should never pull Dallas, Houston, Austin, or San Antonio games
just to reach 30. It should show only games within 75 miles of Tyler.

Data strategy:
Maintain one large statewide weekly schedule pool with enough local games in every
region so each preset area can usually return 30 games while respecting the user's radius.
