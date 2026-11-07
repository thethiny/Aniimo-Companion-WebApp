# Map marks per scene, merged from every collection (the game only holds the records the big map has
# built, so each run adds what it saw and refreshes statuses). Persisted in data/marks.json.
import json
import os
import threading
import time

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "marks.json")

# Categories, matched on the icon name first, then on the mark type (like the game's map filters)
CATEGORIES = [
    ("waypoint", "Teleport waypoints", ("Transmit",)),
    ("boss", "Alpha Aniimo", ("MythicalAnimals",)),
    ("fight", "Fights", ("BattleRoom", "PetHead")),
    ("challenge", "Challenges", ("Challenge", "Temple", "SpecialTraining")),
    ("collection", "Collection", ("Chest", "Cave", "Ecology", "Tree")),
    ("mission", "Missions", ("Clue", "DailySurvey", "Quest", "HelpAniimo")),
    ("mine", "My marks", ("Mark01", "MarkShare")),
]
AREA_LABEL_TYPE = 13      # area name labels (they carry a weatherAreaId)


def category(mark, icon):
    if mark.get("markType") == AREA_LABEL_TYPE:
        return "area"
    for key, _, words in CATEGORIES:
        if icon and any(w in icon for w in words):
            return key
    return "other"


class MarkStore:
    def __init__(self, path=PATH):
        self.path = path
        self.lock = threading.Lock()
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        self.scenes = data.get("scenes", {})
        self.config_icons = data.get("configIcons", {})

    def merge(self, found):
        now = int(time.time())
        with self.lock:
            self.config_icons.update({str(k): v for k, v in found.get("configIcons", {}).items()})
            for spawner, mark in found.get("marks", {}).items():
                scene = self.scenes.setdefault(str(mark["scene"]), {})
                scene[str(spawner)] = dict(mark, seen=now)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            json.dump({"scenes": self.scenes, "configIcons": self.config_icons},
                      open(self.path, "w", encoding="utf-8"), ensure_ascii=False)

    def get(self, scene):
        """{"marks": [...], "categories": [[key, label], ...]} for a scene, icons and categories resolved."""
        with self.lock:
            marks = dict(self.scenes.get(str(scene), {}))
            icons = dict(self.config_icons)
        out = []
        for spawner, mark in marks.items():
            icon = mark.get("icon") or icons.get(str(mark.get("markConfigId")))
            out.append(dict(mark, id=spawner, icon=icon, category=category(mark, icon)))
        return {"marks": out, "categories": [[k, label] for k, label, _ in CATEGORIES] + [["other", "Other"]]}
