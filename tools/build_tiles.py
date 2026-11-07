# Builds Leaflet tile pyramids for every in-game map from the game's UI map bundles.
# Output: app/web/tiles/<scene>/<z>/<x>_<y>.webp and app/web/maps.json
# Usage: python build_tiles.py [scene ...]
import json
import math
import os
import re
import sys
from collections import defaultdict, deque

import numpy as np
import UnityPy
from PIL import Image

GAME = r"J:\SteamLibrary\steamapps\common\Aniimo"
BUNDLES = os.path.join(GAME, r"Aniimo_Data\StreamingAssets\cvs\res\uab\win\DefaultPackage")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "web")
TILE = 256
OCEAN = (21, 63, 99)
REGION_SCALE = 32        # landmass detection runs on a 1/32 thumbnail
REGION_MIN_AREA = 0.01   # ignore islands smaller than 1% of the map
REGION_PAD = 0.04        # padding around a landmass, as a fraction of its size
# Detailed minimap tiles first; the coarse map tiles fill any gaps
SOURCES = [("MapMini", r"xgui_mini_(\d+)_"), ("Map", r"xgui_texture_(\d+)_")]
NAMES = {"3000": "Idyll", "501": "Astra"}
TILE_NAME = re.compile(r"UI_Img_(MapMini|Map)_(\d+)_(\d+)_(\d+)$")


def load_tiles():
    """{scene: {kind: {(col, row): Image}}}"""
    found = defaultdict(lambda: defaultdict(dict))
    for name in sorted(os.listdir(BUNDLES)):
        if not any(re.search(p, name) for _, p in SOURCES):
            continue
        for obj in UnityPy.load(os.path.join(BUNDLES, name)).objects:
            if obj.type.name != "Texture2D":
                continue
            tex = obj.read()
            m = TILE_NAME.match(tex.m_Name)
            if m:
                kind, scene, col, row = m[1], m[2], int(m[3]), int(m[4])
                found[scene][kind][(col, row)] = tex.image.convert("RGB")
    return found


def compose(sets):
    """Full-resolution image: detailed tiles, gaps filled from coarser sets scaled to match."""
    kinds = [k for k, _ in SOURCES if k in sets]
    base = sets[kinds[0]]
    tile_px = next(iter(base.values())).size[0]
    cols = max(c for c, _ in base) + 1
    rows = max(r for _, r in base) + 1
    width, height = cols * tile_px, rows * tile_px
    image = Image.new("RGB", (width, height), OCEAN)
    for kind in reversed(kinds):
        tiles = sets[kind]
        k_cols = max(c for c, _ in tiles) + 1
        k_rows = max(r for _, r in tiles) + 1
        step_x, step_y = width / k_cols, height / k_rows
        for (c, r), img in tiles.items():
            box = (round(c * step_x), round(r * step_y), round((c + 1) * step_x), round((r + 1) * step_y))
            image.paste(img.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS), box[:2])
    return image


def find_regions(image):
    """Bounding boxes [x0, y0, x1, y1] (map px) of the landmasses, largest first."""
    w, h = image.width // REGION_SCALE, image.height // REGION_SCALE
    px = np.asarray(image.resize((w, h), Image.BILINEAR), dtype=np.int16)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    land = ~((b - r > 45) & (g < b + 10))      # sea is strongly blue; lavender land is not
    # Close small gaps (rivers, straits between shoreline props)
    grown = land.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        grown |= np.roll(np.roll(land, dy, 0), dx, 1)
    seen = np.zeros_like(grown)
    regions = []
    for sy, sx in zip(*np.nonzero(grown)):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        queue, cells = deque([(sy, sx)]), []
        while queue:
            y, x = queue.popleft()
            cells.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and grown[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if len(cells) < REGION_MIN_AREA * w * h:
            continue
        ys, xs = [c[0] for c in cells], [c[1] for c in cells]
        x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        pad_x, pad_y = (x1 - x0) * REGION_PAD, (y1 - y0) * REGION_PAD
        box = [max(0, x0 - pad_x), max(0, y0 - pad_y), min(w, x1 + pad_x), min(h, y1 + pad_y)]
        regions.append((len(cells), [round(v * REGION_SCALE) for v in box]))
    return [box for _, box in sorted(regions, reverse=True)]


def write_pyramid(scene, image):
    width, height = image.size
    max_zoom = math.ceil(math.log2(max(width, height) / TILE))
    for z in range(max_zoom, -1, -1):
        scale = 2 ** (max_zoom - z)
        level = image if scale == 1 else image.resize((math.ceil(width / scale), math.ceil(height / scale)), Image.LANCZOS)
        out = os.path.join(WEB, "tiles", scene, str(z))
        os.makedirs(out, exist_ok=True)
        for ty in range(math.ceil(level.height / TILE)):
            for tx in range(math.ceil(level.width / TILE)):
                # Edge tiles: crop only what exists so the rest keeps the ocean colour
                box = (tx * TILE, ty * TILE, min((tx + 1) * TILE, level.width), min((ty + 1) * TILE, level.height))
                tile = Image.new("RGB", (TILE, TILE), OCEAN)
                tile.paste(level.crop(box), (0, 0))
                tile.save(os.path.join(out, f"{tx}_{ty}.webp"), quality=82, method=4)
    return max_zoom


def main():
    regions_only = "--regions-only" in sys.argv   # recompute landmasses, keep existing tiles
    wanted = {a for a in sys.argv[1:] if not a.startswith("--")}
    maps_path = os.path.join(WEB, "maps.json")
    maps = json.load(open(maps_path, encoding="utf-8")) if os.path.exists(maps_path) else {}
    for scene, sets in sorted(load_tiles().items()):
        if wanted and scene not in wanted:
            continue
        image = compose(sets)
        if regions_only and scene in maps:
            max_zoom = maps[scene]["maxZoom"]
        else:
            max_zoom = write_pyramid(scene, image)
        regions = find_regions(image)
        maps[scene] = {"name": NAMES.get(scene, f"Map {scene}"), "width": image.width, "height": image.height,
                       "maxZoom": max_zoom, "tileSize": TILE, "regions": regions}
        print(f"{scene}: {image.width}x{image.height}, zoom 0-{max_zoom}, regions {regions}")
    json.dump(maps, open(maps_path, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
