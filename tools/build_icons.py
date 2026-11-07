# Extracts the game's map mark icons, Aniimo portraits and weather icons into app/web/icons/.
# Which assets are icons comes from the asset manifest's string table (their folder); the images are in
# the mres_xgui_texture_expng and pet head bundles, and in the xpt<N> patch bundles (highest N wins).
# Usage: python build_icons.py
import glob
import os
import re

import UnityPy
from PIL import Image

GAME = r"J:\SteamLibrary\steamapps\common\Aniimo"
BUNDLES = os.path.join(GAME, r"Aniimo_Data\StreamingAssets\cvs\res\uab\win\DefaultPackage")
MANIFESTS = os.path.join(GAME, r"Aniimo_Data\cvs\res\uab\win\DefaultPackage")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "web", "icons")
ICON_FOLDERS = ("/Texture/Icon/Map_MarkPoint/", "/Texture/Image/Pet_head/", "/Texture/Icon/Weather/")
BUNDLE_PATTERNS = (r"^mres_xgui_texture_expng_", r"^ar_xgui_image_pet_he_")
PATCH_PATTERN = re.compile(r"^xpt(\d+)_mres_expng_")   # hot patches: newer icons and replacements
MAX_SIZE = 96   # icons are drawn at ~30 px; keep some headroom for high-DPI screens


def icon_names():
    """Asset names (without extension) in the icon folders, from the newest string table."""
    stab = max(glob.glob(os.path.join(MANIFESTS, "*_stab.txt")), key=os.path.getmtime)
    names = set()
    for line in open(stab, encoding="utf-8", errors="replace"):
        line = line.strip()
        if any(folder in line for folder in ICON_FOLDERS) and line.endswith(".png"):
            names.add(os.path.splitext(os.path.basename(line))[0])
    return names


def bundle_priority(name):
    """0 for base bundles, N for xptN patches, None for bundles without icons."""
    patch = PATCH_PATTERN.match(name)
    if patch:
        return int(patch[1])
    return 0 if any(re.search(p, name) for p in BUNDLE_PATTERNS) else None


def main():
    wanted = icon_names()
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
