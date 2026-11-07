# Builds the offline map: a static site that needs neither the game nor the server, and holds no player data
# (no position, quests, party or handbook: the profile shows the game's maximums, every Aniimo shows as
# caught, every Alpha / Omega as not fought). Publish the output folder as is on any static host, GitHub Pages
# included: relative paths only, a .nojekyll file, and file names checked for exact case (Pages is
# case-sensitive, Windows isn't).
# It ships, per map, what the server would send: its marks (from mapdata.json, without the live overlay) and
# its area names (app/data/areas.json, collected while playing: map structure, nothing personal).
# Needs the built game data (build_mapdata.py, build_icons.py, build_tiles.py); not the running game.
# Usage: python build_offline.py [out_dir]   (default: docs/, which GitHub Pages publishes from master -> /docs)
import json
import os
import re
import shutil
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APP, WEB = os.path.join(ROOT, "app"), os.path.join(ROOT, "app", "web")
sys.path.insert(0, APP)
from areas import AreaStore  # noqa: E402
from marks import MarkStore  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "docs")
PAGE_FILES = ("app.js", "style.css", "collection.js", "collection.css", "party.js", "party.css",
              "maps.json", "collection.json")
FOLDERS = ("icons", "tiles", "areas")
OFFLINE_FLAG = "<script>window.OFFLINE_MAP = true;</script>"   # read by app.js before anything loads
UNNAMED_MAP = "Map "             # build_tiles names maps the game gives no name "Map <id>": not offered
BUILD_MARKER = os.path.join("offline", "maps.json")   # a folder holding this is an earlier build: safe to clear
IMAGE_NAME = re.compile(r"[A-Za-z0-9_\-]+\.(?:png|webp)")
IMAGE_FOLDERS = ("icons/", "areas/")
KEEP = ("CNAME",)                 # GitHub Pages' custom domain file: survives rebuilds


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def copy_page(out):
    """The page itself, with the offline flag set before its scripts run."""
    for name in PAGE_FILES:
        shutil.copy2(os.path.join(WEB, name), os.path.join(out, name))
    for folder in FOLDERS:
        shutil.copytree(os.path.join(WEB, folder), os.path.join(out, folder), dirs_exist_ok=True)
    page = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    if "<script" not in page:
        raise SystemExit("index.html has no scripts to put the offline flag before")
    page = page.replace("<script", OFFLINE_FLAG + "\n<script", 1)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(page)


def offline_maps(maps):
    """[{id, name}] of the maps worth offering: named ones that have marks."""
    marks = MarkStore(path=os.path.join(ROOT, "no-live-marks.json"))   # no such file: static marks only
    offered = []
    for scene, info in maps.items():
        data = marks.get(scene)
        if data["marks"] and not str(info.get("name", "")).startswith(UNNAMED_MAP):
            offered.append({"id": scene, "name": info["name"], "marks": data})
    return offered


def clear(out):
    """Empties an earlier build so nothing stale is published; refuses any other non-empty folder."""
    if not os.path.isdir(out) or not os.listdir(out):
        os.makedirs(out, exist_ok=True)
        return
    if not os.path.exists(os.path.join(out, BUILD_MARKER)):
        raise SystemExit(f"{out} isn't empty and isn't an earlier offline build: pick another folder")
    for name in os.listdir(out):
        if name in KEEP:
            continue
        path = os.path.join(out, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)


def check_case(out):
    """Image names the site references that only exist with other letter case (they'd break on Pages)."""
    files = {os.path.relpath(os.path.join(root, f), out).replace(os.sep, "/")
             for root, _, names in os.walk(out) for f in names}
    lower = {f.lower(): f for f in files}
    wrong = set()
    for path in files:
        if path.endswith((".json", ".js", ".html", ".css")):
            for name in IMAGE_NAME.findall(open(os.path.join(out, path), encoding="utf-8", errors="replace").read()):
                wrong.update(f"{folder}{name}" for folder in IMAGE_FOLDERS
                             if f"{folder}{name}" not in files and f"{folder}{name}".lower() in lower)
    return sorted(wrong)


def main():
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
    clear(out)
    copy_page(out)
    open(os.path.join(out, ".nojekyll"), "w").close()   # GitHub Pages: serve files as they are
    maps = json.load(open(os.path.join(WEB, "maps.json"), encoding="utf-8"))
    areas = AreaStore()
    offered = offline_maps(maps)
    for entry in offered:
        write_json(os.path.join(out, "offline", "marks", f"{entry['id']}.json"), entry.pop("marks"))
        write_json(os.path.join(out, "offline", "areas", f"{entry['id']}.json"), areas.get(entry["id"]))
    write_json(os.path.join(out, "offline", "maps.json"), offered)
    wrong = check_case(out)
    if wrong:
        raise SystemExit(f"File names differ only in case from what the page asks for (breaks on GitHub Pages): {wrong}")
    print(f"Offline map with {len(offered)} maps ({', '.join(m['name'] for m in offered)}) -> {out}")


if __name__ == "__main__":
    main()
