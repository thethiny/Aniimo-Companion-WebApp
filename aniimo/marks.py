# Map marks (waypoints, bosses, fights, chests, caves, quests, your own marks...), read-only from the
# game's Lua heap. The full mark list lives in the packed pmdata.bin, decoded natively by xlua.dll; but the
# big map builds one Lua record per mark it shows, with everything needed to draw it:
#   spawnerId, realSceneId, mapX / mapY (map pixels, Y up), markPos (world), markConfigId, markType,
#   markStatus, markLevel, commonIconUrl, weatherAreaId
# Config rows ({markConfigId, markIcon | icon}) give the icon for marks whose record has none.
# Records only exist while the map UI holds them, so callers merge results over time.
from . import lua

RECORD_KEY = "mapX"
CONFIG_KEY = "markConfigId"
FIELDS = ("markConfigId", "markType", "markStatus", "markLevel", "weatherAreaId")


def _num(v):
    return v if isinstance(v, float) else None


def _icon(v):
    return v[1:] if isinstance(v, str) and v.startswith("$") and v.endswith(".png") else None


def collect(proc):
    """{"marks": {spawnerId: {...}}, "configIcons": {markConfigId: icon}}"""
    strs = lua.find_strings(proc, [RECORD_KEY, CONFIG_KEY])
    numeric = lambda n: lua.itype(proc.u64(n) or lua.tag(lua.NIL, 0)) < 0x1FFF2
    nodes = [n for key in (RECORD_KEY, CONFIG_KEY) for n in lua.tables_with_key(proc, strs[key]) if numeric(n)]
    marks, config_icons = {}, {}
    for table in set(lua.owner_tables(proc, nodes, max_index=128).values()):
        rec = lua.read_table(proc, table, depth=1) or {}
        cfg = _num(rec.get("markConfigId"))
        icon = _icon(rec.get("commonIconUrl")) or _icon(rec.get("markIcon")) or _icon(rec.get("icon"))
        if cfg is not None and icon and "commonIconUrl" not in rec:
            config_icons[int(cfg)] = icon
        spawner, scene = _num(rec.get("spawnerId")), _num(rec.get("realSceneId"))
        mx, my = _num(rec.get("mapX")), _num(rec.get("mapY"))
        if spawner is None or scene is None or mx is None or my is None or cfg is None:
            continue
        pos = rec.get("markPos")
        world = [pos.get(1), pos.get(3)] if isinstance(pos, dict) and isinstance(pos.get(1), float) else None
        mark = {"scene": int(scene), "map": [mx, -my], "world": world, "icon": icon}
        mark.update({k: int(rec[k]) for k in FIELDS if isinstance(rec.get(k), float)})
        marks[int(spawner)] = mark
    return {"marks": marks, "configIcons": config_icons}
