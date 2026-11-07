# Extracts textures, sprites and text assets from Aniimo Unity bundles whose name matches a filter
# Usage: python extract_bundles.py <out_dir> <name substring> [more substrings...]
import os
import sys

import UnityPy

GAME = r"J:\SteamLibrary\steamapps\common\Aniimo"
BUNDLES = os.path.join(GAME, r"Aniimo_Data\StreamingAssets\cvs\res\uab\win\DefaultPackage")

out_dir, filters = sys.argv[1], [f.lower() for f in sys.argv[2:]]
os.makedirs(out_dir, exist_ok=True)

for name in sorted(os.listdir(BUNDLES)):
    if not name.endswith(".uab") or not any(f in name.lower() for f in filters):
        continue
    sub = os.path.join(out_dir, name.rsplit("_", 1)[0])
    env = UnityPy.load(os.path.join(BUNDLES, name))
    count = 0
    for obj in env.objects:
        kind = obj.type.name
        if kind not in ("Texture2D", "Sprite", "TextAsset", "MonoBehaviour"):
            continue
        data = obj.read()
        asset = getattr(data, "m_Name", "") or f"{kind}_{obj.path_id}"
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, f"{asset}.{kind}")
        try:
            if kind in ("Texture2D", "Sprite"):
                data.image.save(path + ".png")
            elif kind == "TextAsset":
                open(path + ".bytes", "wb").write(data.m_Script.encode("utf-8", "surrogateescape"))
            else:
                tree = obj.read_typetree()
                open(path + ".txt", "w", encoding="utf-8").write(repr(tree))
            count += 1
        except Exception as e:
            print(f"  skip {asset} ({kind}): {e}")
    print(f"{name}: {count} assets")
