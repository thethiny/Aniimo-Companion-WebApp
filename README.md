# Aniimo Live Map

A live map of Aniimo in the browser: the game's own map art, your position, facing and coordinates, and the
route the game is guiding you along. The game is only ever read, never written to.

## Layout

| Folder | What |
|---|---|
| `aniimo/` | Read-only game reader: process memory, IL2CPP lookup by name, Unity transforms, player/scene/route |
| `app/` | `server.py` (local server on the LAN), `calibration.py`, `web/` (the page), `data/calibration.json` |
| `tools/` | `build_tiles.py` (map tiles from the game bundles) and research scripts |
| `dumps/` | Local game dumps (decrypted metadata, extracted assets). Ignored by git |

## Setup

Python 3.9+ with `pip install UnityPy pillow numpy`.

1. Build the map tiles once (and after game updates that change the map): `python tools/build_tiles.py`
2. Start the game, then `python app/server.py`. Attaching takes 1-2 minutes.
3. Open the printed `http://<lan-ip>:8777/` on any device on the LAN.
4. First time on each map: **Calibrate**, then click where your in-game map arrow is, at two far-apart places.

The game path is set in `tools/build_tiles.py` (`GAME`).
