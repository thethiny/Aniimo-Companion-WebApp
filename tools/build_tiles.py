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
from PIL import Image, ImageFilter

from build_icons import bundle_priority
from game import BUNDLES  # noqa: E402  (found through the running game)
from pmdata import PmData, Text
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "web")
TILE = 256
OCEAN = (21, 63, 99)
REGION_SCALE = 32        # landmass detection runs on a 1/32 thumbnail
REGION_MIN_AREA = 0.01   # ignore islands smaller than 1% of the map
REGION_PAD = 0.04        # padding around a landmass, as a fraction of its size
NO_SEA = 0.8             # a "landmass" covering this much of the image means the map has no sea (Astra)
# Detailed minimap tiles first; the coarse map tiles fill any gaps
SOURCES = [("MapMini", r"xgui_mini_(\d+)_"), ("Map", r"xgui_texture_(\d+)_")]
TILE_NAME = re.compile(r"UI_Img_(MapMini|Map)_(\d+)_(\d+)_(\d+)$")
MARKS_PAD = 300     # map pixels around a scene's marks when they frame its Fit view
# Area masks: red = round(small-area index * 4.25), green = 255 inside an area (low on borders)
AREA_BUNDLE, AREA_NAME = r"xgui_tex2k_map_", re.compile(r"UI_Img_MapArea_(\d+)_(\d+)_(\d+)$")
AREA_STEP = 255 / 60
AREA_SCALE = 16          # one area-grid cell per 16 map pixels
BORDER_GREEN = 160       # green below this inside the mask is a seam between areas
BORDER_COAST_GAP = 15    # seams this close (mask pixels) to the sea are the coast fade, not borders


def load_tiles():
    """{scene: {kind: {(col, row): Image}}}. Most tiles are in each map's own bundles; some are in the shared
    UI panel bundles and in the xpt<N> patches (newer art: the highest N wins). Those only complete maps
    that have bundles of their own (the panels also hold small maps of places we don't draw)."""
    ranked = defaultdict(lambda: defaultdict(dict))   # scene -> kind -> (col, row) -> (rank, Image)
    own = set()
    for name in sorted(os.listdir(BUNDLES)):
        dedicated = any(re.search(p, name) for _, p in SOURCES)
        shared = bundle_priority(name)
        if not dedicated and shared is None:
            continue
        rank = shared or 0
        for obj in UnityPy.load(os.path.join(BUNDLES, name)).objects:
            if obj.type.name != "Texture2D" or not TILE_NAME.match(obj.peek_name() or ""):
                continue
            tex = obj.read()
            kind, scene, col, row = TILE_NAME.match(tex.m_Name).groups()
            if dedicated:
                own.add(scene)
            slot = ranked[scene][kind]
            if (int(col), int(row)) not in slot or rank > slot[(int(col), int(row))][0]:
                slot[(int(col), int(row))] = (rank, tex.image.convert("RGB"))
    return {scene: {kind: {pos: image for pos, (_, image) in tiles.items()} for kind, tiles in kinds.items()}
            for scene, kinds in ranked.items() if scene in own}


def load_area_masks():
    """{scene: {(col, row): Image}} from the area mask tiles."""
    found = defaultdict(dict)
    for name in sorted(os.listdir(BUNDLES)):
        if not re.search(AREA_BUNDLE, name):
            continue
        for obj in UnityPy.load(os.path.join(BUNDLES, name)).objects:
            if obj.type.name == "Texture2D":
                tex = obj.read()
                m = AREA_NAME.match(tex.m_Name)
                if m:
                    found[m[1]][(int(m[2]), int(m[3]))] = tex.image.convert("RGB")
    return found


def compose_area_mask(tiles):
    tile_px = next(iter(tiles.values())).size[0]
    cols, rows = max(c for c, _ in tiles) + 1, max(r for _, r in tiles) + 1
    mask = Image.new("RGB", (cols * tile_px, rows * tile_px))
    for (c, r), img in tiles.items():
        mask.paste(img, (c * tile_px, r * tile_px))
    return mask


def write_area_grid(scene, mask, width, height):
    """Grayscale PNG of small-area indices (0 = none), one cell per AREA_SCALE map pixels."""
    grid_w, grid_h = math.ceil(width / AREA_SCALE), math.ceil(height / AREA_SCALE)
    # Nearest sampling keeps index values exact (no blending across borders)
    px = np.asarray(mask.resize((grid_w, grid_h), Image.NEAREST), dtype=np.float32)
    index = np.where(px[..., 1] >= 128, np.rint(px[..., 0] / AREA_STEP), 0).astype(np.uint8)
    os.makedirs(os.path.join(WEB, "areas"), exist_ok=True)
    Image.fromarray(index).save(os.path.join(WEB, "areas", f"{scene}.png"))
    return {"file": f"areas/{scene}.png", "scale": AREA_SCALE}


def write_area_borders(scene, mask):
    """Transparent PNG of the lines between small areas (the mask's dark seams, minus the coastline)."""
    px = np.asarray(mask, dtype=np.float32)
    outside = Image.fromarray((px[..., 0] == 0).astype(np.uint8) * 255)
    near_coast = np.asarray(outside.filter(ImageFilter.MaxFilter(BORDER_COAST_GAP))) > 0
    strength = np.clip((BORDER_GREEN - px[..., 1]) / BORDER_GREEN, 0, 1)
    strength[near_coast] = 0
    rgba = np.zeros(px.shape[:2] + (4,), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = (strength * 230).astype(np.uint8)
    Image.fromarray(rgba).save(os.path.join(WEB, "areas", f"{scene}_borders.png"), optimize=True)
    return f"areas/{scene}_borders.png"


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


def scene_info():
    """{scene: {"name", "points", "offset"}} from scene_data: the name the game shows ("Idyll", "Whisperwake
    Isles", "Astra", ...), two world points with their map positions (map units, y pointing up) and where
    the scene sits in the world it's drawn on (wholemapOffset: Whisperwake Isles is a scene of its own with
    its own origin, laid out next to Idyll; the game's coordinates are the position plus this offset)."""
    text, info = Text(), {}
    for scene, row in PmData().table("scene_data", depth=2).items():
        keys = ("PointAPositon", "MapAPosition", "PointBPosition", "MapBPosition")
        points = [[row[k][1], row[k][2]] for k in keys if isinstance(row.get(k), dict)]
        offset = row.get("wholemapOffset") or {}
        overlays = row.get("mapDisplayOverlayScenes") or {}
        info[str(scene)] = {"name": text.get(row.get("sceneName")), "points": points if len(points) == 4 else None,
                            "offset": [offset.get(1, 0), offset.get(3, 0)],
                            "overlays": [str(s) for s in overlays.values()]}
    return info


def config_calibration(points, width, offset=(0, 0)):
    """{"x": [s, o], "z": [s, o]} position -> map pixel from the scene's two reference points and its world
    offset, or None. Map units are image pixels, except on maps the game ships smaller than their units:
    those are scaled down by the power of two that fits (Whisperwake Isles: Idyll's 9216-unit frame in a
    2048 px image, a quarter)."""
    if not points:
        return None
    (ax, az), (max_, maz), (bx, bz), (mbx, mbz) = points
    if ax == bx or az == bz:
        return None
    span = max(abs(max_), abs(mbx))
    k = 1.0 if span <= width else 2.0 ** round(math.log2(width / span))
    sx, sz = (mbx - max_) / (bx - ax) * k, -(mbz - maz) / (bz - az) * k
    ox, oz = max_ * k - sx * ax, -maz * k - sz * az
    return {"x": [sx, ox + sx * offset[0]], "z": [sz, oz + sz * offset[1]]}


def main():
    scenes = scene_info()
    regions_only = "--regions-only" in sys.argv   # recompute landmasses, keep existing tiles
    wanted = {a for a in sys.argv[1:] if not a.startswith("--")}
    maps_path = os.path.join(WEB, "maps.json")
    maps = json.load(open(maps_path, encoding="utf-8")) if os.path.exists(maps_path) else {}
    area_masks = load_area_masks()
    for scene, sets in sorted(load_tiles().items()):
        if wanted and scene not in wanted:
            continue
        image = compose(sets)
        if regions_only and scene in maps:
            max_zoom = maps[scene]["maxZoom"]
        else:
            max_zoom = write_pyramid(scene, image)
        regions = find_regions(image)
        info = scenes.get(scene) or {}
        maps[scene] = {"name": info.get("name") or f"Map {scene}", "width": image.width, "height": image.height,
                       "maxZoom": max_zoom, "tileSize": TILE, "regions": regions,
                       "calibration": config_calibration(info.get("points"), image.width, info.get("offset")),
                       "worldOffset": info.get("offset")}
        if scene in area_masks:
            mask = compose_area_mask(area_masks[scene])
            maps[scene]["areaGrid"] = write_area_grid(scene, mask, image.width, image.height)
            maps[scene]["areaBorders"] = write_area_borders(scene, mask)
        print(f"{scene}: {image.width}x{image.height}, zoom 0-{max_zoom}, regions {regions}")
    add_overlays(maps, scenes)
    frame_sealess(maps, json.load(open(os.path.join(WEB, "mapdata.json"), encoding="utf-8")))
    json.dump(maps, open(maps_path, "w", encoding="utf-8"), indent=1)


def add_overlays(maps, scenes):
    """Scenes the game draws on another map (its mapDisplayOverlayScenes: Whisperwake Isles on Idyll's, where
    its isles are drawn in full detail; its own image is a low-resolution copy) show that map's tiles. Their
    positions go through the parent's calibration after the scene's world offset, the same numbers the
    game's own markers use there. Their Fit view frames their marks."""
    mapdata = json.load(open(os.path.join(WEB, "mapdata.json"), encoding="utf-8"))
    for parent_id, parent_info in scenes.items():
        parent = maps.get(parent_id)
        if not parent or parent.get("tiles") or not parent.get("calibration"):
            continue
        for scene in parent_info.get("overlays", []):
            add_overlay(maps, scenes, mapdata, scene, parent_id)


def marks_box(mapdata, scene, fit):
    """[x0, y0, x1, y1] map pixels around the scene's marks (from mapdata) through `fit`, or None."""
    points = [(fit["x"][0] * m["world"][0] + fit["x"][1], fit["z"][0] * m["world"][1] + fit["z"][1])
              for m in mapdata.get("scenes", {}).get(scene, {}).get("marks", []) if m.get("world")]
    if not (points and fit):
        return None
    return [round(min(p[0] for p in points)) - MARKS_PAD, round(min(p[1] for p in points)) - MARKS_PAD,
            round(max(p[0] for p in points)) + MARKS_PAD, round(max(p[1] for p in points)) + MARKS_PAD]


def frame_sealess(maps, mapdata):
    """Maps without sea (one landmass over nearly the whole image: Astra) frame their Fit view on their marks."""
    for scene, info in maps.items():
        regions = info.get("regions") or []
        if info.get("tiles") or len(regions) != 1 or not info.get("calibration"):
            continue
        x0, y0, x1, y1 = regions[0]
        if (x1 - x0) * (y1 - y0) >= NO_SEA * info["width"] * info["height"]:
            box = marks_box(mapdata, scene, info["calibration"])
            if box:
                info["regions"] = [[max(0, box[0]), max(0, box[1]), min(info["width"], box[2]), min(info["height"], box[3])]]


def add_overlay(maps, scenes, mapdata, scene, parent_id):
    parent, info = maps[parent_id], scenes.get(scene) or {}
    offset = info.get("offset") or [0, 0]
    (sx, ox), (sz, oz) = parent["calibration"]["x"], parent["calibration"]["z"]
    fit = {"x": [sx, ox + sx * offset[0]], "z": [sz, oz + sz * offset[1]]}
    box = marks_box(mapdata, scene, fit)
    maps[scene] = {"name": info.get("name") or f"Map {scene}", "tiles": parent_id,
                   **{k: parent[k] for k in ("width", "height", "maxZoom", "tileSize")},
                   "regions": [box] if box else parent.get("regions", []),
                   "calibration": fit, "worldOffset": info.get("offset")}


if __name__ == "__main__":
    main()
