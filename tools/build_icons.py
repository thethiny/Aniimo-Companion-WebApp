# Extracts the game's map mark icons, Aniimo portraits, weather icons and player heads / head frames into
# app/web/icons/.
# Which assets are icons comes from the asset manifest's string table (their folder); the images are in
# the mres_xgui_texture_expng, pet head and item icon bundles, and in the xpt<N> patch bundles (highest N
# wins).
# Usage: python build_icons.py
import glob
import os
import re

import UnityPy
from PIL import Image

from build_mapdata import (PICKUP_ICONS, PRIMEGY, SKILL_POINT, collect_stats, collection_icons, explore_abilities,
                           friendship_tiers, icon_name, profile_pictures)
from game import BUNDLES, MANIFESTS  # noqa: E402  (found through the running game)
from pmdata import PmData, Text
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "web", "icons")
ICON_FOLDERS = ("/Texture/Icon/Map_MarkPoint/", "/Texture/Image/Pet_head/", "/Texture/Icon/Weather/",
                "/Texture/Icon/Player_head/", "/Texture/Image/Head_Frame/", "/Texture/Icon/Personal/")
BUNDLE_PATTERNS = (r"^mres_xgui_texture_expng_", r"^ar_xgui_image_pet_he_", r"^mres_xgui_panel_expng_",
                   r"^mres_xgui_common_expng_", r"^ar_xgui_icon_item_i_")
# Single sprites from the map's own atlases: the small moon the map adds to weather icons at night, the
# blue wink it shows when a weather-exclusive Aniimo is out, and the area panel's cat and crown. Item icons
# come from config_icons(): the item folder holds thousands
EXTRA_ICONS = {"UI_Img_moon_small", "UI_Map_Icon_Weather5", "UI_Img_Map_PetArea_FloatTab_Pet_Nml",
               "UI_Img_Map_PetArea_Gather_Schedule_Icon"}
PATCH_PATTERN = re.compile(r"^xpt(\d+)_mres_expng_")   # hot patches: newer icons and replacements
MAX_SIZE = 96   # icons are drawn at ~30 px; keep some headroom for high-DPI screens


def icon_names():
    """Asset names (without extension) in the icon folders, from the newest string table."""
    stab = max(glob.glob(os.path.join(MANIFESTS, "*_stab.txt")), key=os.path.getmtime)
    names = set()
    for line in open(stab, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line.endswith(".png"):
            continue
        name = os.path.splitext(os.path.basename(line))[0]
        if any(folder in line for folder in ICON_FOLDERS):
            names.add(name)
    return names


def config_icons():
    """Item icons the page shows: Primegy, Skill Points, every egg that hatches and each region's Lumin Amber
    (player profile), and the map's pickups (PICKUP_ICONS)."""
    pm = PmData()
    items = pm.table("item_data", depth=0)
    ambers = {levels.get("0", levels.get(0, {})).get("stoneId") for levels in pm.table("leylinetree_data", depth=2).values()}
    wanted = {PRIMEGY, SKILL_POINT} | set(pm.table("pet_hatch_egg_data")) | ambers
    names = [icon_name((items.get(item) or {}).get("icon")) for item in wanted]
    text = Text()   # party cards: friendship tiers, explore abilities, Collection Progress
    names += [row["icon"] for row in friendship_tiers(pm, text) + collect_stats(pm, text)]
    names += [row["icon"] for row in explore_abilities(pm, text).values()]
    names += [icon for pictures in profile_pictures(pm) for icon in pictures.values()]   # profile pictures, frames
    return {os.path.splitext(name)[0] for name in names + PICKUP_ICONS if name}


def bundle_priority(name):
    """0 for base bundles, N for xptN patches, None for bundles without icons."""
    patch = PATCH_PATTERN.match(name)
    if patch:
        return int(patch[1])
    return 0 if any(re.search(p, name) for p in BUNDLE_PATTERNS) else None


def main():
    wanted = icon_names() | EXTRA_ICONS | config_icons() | {os.path.splitext(name)[0] for name in collection_icons()}
    best = {}   # icon name -> (rank, image); patches beat base bundles, sprite crops beat whole textures
    for name in sorted(os.listdir(BUNDLES)):
        priority = bundle_priority(name)
        if priority is None:
            continue
        for obj in UnityPy.load(os.path.join(BUNDLES, name)).objects:
            if obj.type.name not in ("Sprite", "Texture2D") or obj.peek_name() not in wanted:
                continue
            rank = (priority, obj.type.name == "Sprite")
            if obj.peek_name() not in best or rank > best[obj.peek_name()][0]:
                best[obj.peek_name()] = (rank, obj.read().image)
    os.makedirs(OUT, exist_ok=True)
    for icon, (_, image) in best.items():
        image.thumbnail((MAX_SIZE, MAX_SIZE), Image.LANCZOS)
        image.save(os.path.join(OUT, icon + ".png"), optimize=True)
    print(f"{len(best)} icons saved to {os.path.normpath(OUT)} ({len(wanted)} icon assets in the manifest)")


if __name__ == "__main__":
    main()
