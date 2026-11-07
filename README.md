# Aniimo Live Map

A companion app for Aniimo that runs in the browser next to the game: the game's own map art with every
map mark, your live position, and your profile, quests and Aniimo collection. It reads the game's memory
and files and never writes to either. Open it from any device on your LAN.

## Features

**Map**
- The game's own map tiles for every map (Idyll, Whisperwake Isles, Astra, ...); it follows you from map
  to map, with a smooth arrow showing where you face
- Every map mark from the game's data, with a filter laid out like the game's (search, groups, Show
  Completed); live locked / completed state once you open the big map in game
- Area names, borders and level ranges; click an area for a card like the game's: completion and tiers,
  the Aniimo living there (seen / caught), current weather and how to find each one
- Tracking: the spawn areas of the Aniimo you track in game, or one you pick in the app
- The route the game is guiding you along
- Location panel: region, area, coordinates (click to copy), height and time of day

**Profile** (top left)
- Picture, frame, name, rank, level and XP
- Primegy with its next point and when it's full; time to the next daily reset
- Daily Activity with today's task checklist
- Skill Points, Lumin Amber per Branch, eggs hatching (3 slots)

**Quests**
- The quests tracked in the HUD (Lore, Quests, Journey), each opening to its steps and progress

**Aniilog collection** (Aniilog row in the profile)
- Full screen over the map: Breezy Plains, Whisperwake Isles, Season 1 Keepsakes, or all of them;
  Family Mode and Form Mode like the game
- The game's counters (owned, Sparkling, Prismana), Umbral in storage, and the collection bar against
  the game's totals
- Every Aniimo with caught / seen / not met yet (uncaught ones faded), family progress, elements,
  variant badges (Sparkling, Alpha, Prismana, Umbral) and how many you have in storage now
- Detail card: every form with its how-to-find text, the ones you own (level, rarity, personality,
  gender, tags), and Show on map, which tracks that form and zooms to where it spawns
- Filters: search, element, caught / seen / not seen / in storage / missing a variant

## Layout

| Folder | What |
|---|---|
| `aniimo/` | Read-only game readers: process memory, IL2CPP lookup, the LuaJIT heap, player, scene, route, handbook, profile, quests, weather, collection |
| `app/` | `server.py` (LAN server and live reads), calibration, live stores, `web/` (the page) |
| `tools/` | Build tools (map data, icons, tiles from the game's files) and research helpers |
| `app/data/`, `dumps/`, `extracted/` | Local game data and last live reads. Ignored by git, as is everything built from the game |

## Setup

Python 3.9+ with `pip install UnityPy pillow numpy msgpack`. The game must be running for the build tools
(they find the install through the running game) and for live data.

1. Build the game data once, and again after game updates:
   - `python tools/build_mapdata.py`: marks, areas, Aniimo, profile data, `collection.json` (needs the
     game's Lua scripts in `extracted/lua_bc`)
   - `python tools/build_icons.py`: icons and Aniimo portraits
   - `python tools/build_tiles.py [scene ...]`: map tiles (takes a while)
2. Start the game, then `python app/server.py` (add `--web` to also open it in its own window). Attaching
   takes about 30 seconds; the first profile read 1-3 minutes.
3. Open the printed `http://<lan-ip>:8777/` on any device on the LAN.

Most maps calibrate themselves from the game's data or minimap. If one doesn't, use **Calibrate** and click
where your in-game arrow is at two far-apart places.
