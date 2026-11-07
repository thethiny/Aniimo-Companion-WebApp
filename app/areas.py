# Area names per scene, merged from every collection (names are only in game memory while the map UI has
# them loaded, so each run adds what it saw). Persisted in data/areas.json.
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "areas.json")


class AreaStore:
    def __init__(self, path=PATH):
        self.path = path
        self.lock = threading.Lock()
        self.scenes = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}

    def merge(self, scene, found):
        """Merge one collection for the scene the game was showing."""
        with self.lock:
            entry = self.scenes.setdefault(str(scene), {"indexToBlock": {}, "blocks": {}, "regions": {}})
            before = json.dumps(entry, sort_keys=True)
            for key in ("indexToBlock", "blocks", "regions"):
                entry[key].update({str(k): v for k, v in found.get(key, {}).items()})
            if json.dumps(entry, sort_keys=True) != before:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                json.dump(self.scenes, open(self.path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    def get(self, scene):
        """{indexToBlock, blocks, regions, mainRegion} for a scene ({} if nothing is known)."""
        with self.lock:
            entry = dict(self.scenes.get(str(scene), {}))
        regions = entry.get("regions") or {}
        if regions:
            # The small-area mask belongs to the scene's main region: the one listed first by the game
            entry["mainRegion"] = min(regions.values(), key=lambda r: r.get("order", 1e18))["name"]
        return entry
