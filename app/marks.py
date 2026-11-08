# Map marks per scene: the full static list from the game's config data (web/mapdata.json, built by
# tools/build_mapdata.py, which also carries the small areas and where each Aniimo spawns) overlaid with what the game's map has shown us live (locked / completed status,
# your own marks, shared marks, quests). Live records are merged over time and kept in data/marks.json.
import json
import math
import os
import threading
import time

from paths import DATA

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(DATA, "marks.json")
MAPDATA = os.path.join(HERE, "web", "mapdata.json")
LIVE_FIELDS = {"markStatus": "status", "markLevel": "level", "markIconIndex": "iconIndex",
               "map": "map"}   # live record field -> field sent to the page
FIT_MIN_POINTS = 3
FIT_MAX_ERROR = 4.0      # map pixels; live records with a worse fit (stale or bogus world positions) are dropped
# The config places some Alpha / Omega Aniimo twice, a few metres apart; the game draws one of them
SINGLE_TYPES = {10404, 10304}   # Alpha, Omega
SAME_SPOT = 15.0                # metres
# The config lists every place these can appear; only a few are active each day (often none), and the game's
# big map builds records for the active ones only. Once the big map has been read on a scene today, the
# places it didn't show today are sent with "inactive" (drawn faded); before that, none are.
ROTATING_TYPES = {31, 121}      # Aniimo Discovery, Vein Abundance
DAY = 86400


def fit_axis(pairs):
    """Least-squares [scale, offset] for value -> pixel, or None."""
    n = len(pairs)
    if n < 2:
        return None
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    var = sum((a - mx) ** 2 for a, _ in pairs)
    if var < 1e-6:
        return None
    scale = sum((a - mx) * (b - my) for a, b in pairs) / var
    return [scale, my - scale * mx]


def fit_world_to_map(records):
    """{"x": [s, o], "z": [s, o]} from live records that carry both a world and a map position."""
    points = [(r["world"], r["map"]) for r in records if r.get("world") and r.get("map")]
    fx = fz = None
    for _ in range(2):   # fit, drop outliers, refit
        if len(points) < FIT_MIN_POINTS:
            return None
        fx = fit_axis([(w[0], m[0]) for w, m in points])
        fz = fit_axis([(w[1], m[1]) for w, m in points])
        if not fx or not fz:
            return None
        points = [(w, m) for w, m in points
                  if abs(fx[0] * w[0] + fx[1] - m[0]) <= FIT_MAX_ERROR and abs(fz[0] * w[1] + fz[1] - m[1]) <= FIT_MAX_ERROR]
    return {"x": fx, "z": fz}


def drop_duplicates(marks):
    """Marks of SINGLE_TYPES with the same type and name within SAME_SPOT of each other count once: the one
    the game's live map knows (it has a status) wins, else the first."""
    kept = []
    ranked = sorted(marks, key=lambda m: m.get("status") is None)   # stable: live ones first
    for mark in ranked:
        if mark.get("type") in SINGLE_TYPES and mark.get("world") and any(
                other.get("type") == mark["type"] and other.get("title") == mark.get("title") and other.get("world")
                and math.dist(other["world"], mark["world"]) < SAME_SPOT for other in kept):
            continue
        kept.append(mark)
    order = {id(m): i for i, m in enumerate(marks)}
    return sorted(kept, key=lambda m: order[id(m)])


def day_start(start, now):
    """The latest daily reset at or before now, from any earlier one (`start`)."""
    return start + math.floor((now - start) / DAY) * DAY


def mark_inactive(marks, live, since):
    """Flags ROTATING_TYPES marks no live read has shown since `since` (today's reset) as "inactive", once
    any read of the scene's big map has happened since then."""
    today = {k for k, r in live.items() if since is not None and r.get("seen", 0) >= since}
    if today:
        for m in marks:
            if m.get("type") in ROTATING_TYPES and str(m["id"]) not in today:
                m["inactive"] = True
    return marks


class StaticMapData:
    """web/mapdata.json, reloaded when the build tool rewrites it."""

    def __init__(self, path=MAPDATA):
        self.path, self.mtime, self.data = path, None, {}

    def get(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return {}
        if mtime != self.mtime:
            self.data, self.mtime = json.load(open(self.path, encoding="utf-8")), mtime
        return self.data


class MarkStore:
    def __init__(self, path=PATH):
        self.path = path
        self.lock = threading.Lock()
        self.static = StaticMapData()
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

    def get(self, scene, since=None):
        """Everything the page needs to draw a scene's marks and area labels. `since`: today's daily reset."""
        static = self.static.get()
        types = static.get("types", {})
        scene_static = static.get("scenes", {}).get(str(scene), {})
        with self.lock:
            live = {k: dict(v) for k, v in self.scenes.get(str(scene), {}).items()}
        marks = {str(m["id"]): dict(m) for m in scene_static.get("marks", [])}
        for spawner, record in live.items():
            fields = {sent: record[k] for k, sent in LIVE_FIELDS.items() if record.get(k) is not None}
            if spawner in marks:
                marks[spawner].update(fields)
            elif str(record.get("markConfigId")) in types:   # only the live map knows these: your marks, quests...
                marks[spawner] = dict(fields, id=spawner, type=record["markConfigId"], world=record.get("world"))
        spawns = {proto: scenes[str(scene)] for proto, scenes in static.get("spawns", {}).items() if str(scene) in scenes}
        return {"types": types, "groups": static.get("groups", []), "blocks": static.get("blocks", {}),
                "pets": static.get("pets", {}), "spawns": spawns, "weathers": static.get("weathers", {}),
                "profile": static.get("profile", {}), "marks": drop_duplicates(mark_inactive(list(marks.values()), live, since)), "areas": scene_static.get("areas", []),
                "fit": fit_world_to_map(live.values())}
