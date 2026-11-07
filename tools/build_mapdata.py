# Builds app/web/mapdata.json from the game's config data: every map mark of every scene (with its name,
# icon and Aniimo portrait), the mark types grouped like the game's map filters, and the small areas
# (name, level range, region, label position, the Aniimo living there) and every Aniimo's spawn areas (the
# yellow squares the game draws when you track one). Live state (locked / completed, your own marks, what
# you've seen and caught) comes from the server; this is the static layer.
# Needs extracted/lua_bc (tools/extract_bundles.py).
# Usage: python build_mapdata.py
import json
import os
import re

from pmdata import PmData, Text

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "web")
SCENE_TABLE = re.compile(r"^Scene\.(\d+)\.scene_mark_point_data$")
AREA_LABEL_TYPES = (13, 14)   # "Area Name Display": label positions, one per small area (largeAreaId)
EVENT_FUNC_TYPES = (27, 31)   # event and survey tracking marks
# Types the game shows without listing them in its filter panel, and the group they belong with
EXTRA_GROUP = {10406: 1, 10407: 1, 33: 1, 10400: 2, 10415: 3}
HIDDEN_TYPES = {20}           # internal transporters clustered in one spot; the game doesn't draw them
# Event tracking types carry internal names; use what the player sees
TYPE_NAMES = {27: "Elite Training", 29: "Special Training", 30: "Ecological Investigation",
              31: "Aniimo Discovery", 35: "Carnival Park",
              10410: "Nurture Bloom"}   # also called "Bloom" in game, next to the teleport Blooms
OTHER_GROUP, EVENTS_GROUP = 6, "events"
BATTLE_ICON = re.compile(r"UI_Icon_POI_Battle_(\d+)\.png$")   # Alpha / Omega: the Aniimo you fight
SPAWN_DECIMALS = 1
# Per-area lists of Aniimo that only appear in some weather (the badge on their portrait in game)
CONDITIONS = ("snow", "rain", "thunder", "storm", "aurora")
TIER_KEYS = ("unlockReward1", "unlockReward2", "unlockReward3")   # bronze / silver / gold completion
PRIMEGY = 1009             # Primegy's item, currency and energy id
OFFICIAL_SERVERS = 2       # open_server_time_data row of the live servers (1 is the developers' test server)


def text_of(text, value):
    """Names are text hashes, stored as numbers or as digit strings."""
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return text.get(value) if isinstance(value, int) else (value or None)


def icon_name(path):
    if not (isinstance(path, str) and path.startswith("$") and path.endswith(".png")):
        return None
    return re.sub(r"(\.png)+$", ".png", path[1:])   # a few config rows say ".png.png"


def visible(point, mark_type):
    shown = point.get("ShoworNot", mark_type.get("ShoworNot"))
    return bool(shown) and mark_type.get("funcType") not in AREA_LABEL_TYPES


def build_types(pm, text):
    """({type id: {name, icon, group}}, [[group key, name, [type ids]]]) in the game's filter order."""
    rows = pm.table("default_map_mark_data")
    groups = [[key, text.get(row["name"]), list(row["categories"].values())]
              for key, row in pm.table("map_filtrate_config_data", depth=2).items()]
    group_of = {t: key for key, _, members in groups for t in members}
    group_of.update(EXTRA_GROUP)
    groups.insert(len(groups) - 1, [EVENTS_GROUP, "Events", []])
    types = {}
    for type_id, row in rows.items():
        if row.get("funcType") in EVENT_FUNC_TYPES:
            group = EVENTS_GROUP
        else:
            group = group_of.get(type_id, OTHER_GROUP)
        name = TYPE_NAMES.get(type_id) or text.get(row.get("typeName"))
        types[type_id] = {"name": name, "icon": icon_name(row.get("icon")), "group": group}
    return types, groups


def build_scene(points, types, blocks, text):
    marks, areas = [], []
    for spawner, point in points.items():
        type_id = point.get("markConfigId")
        mark_type = types.get(type_id)
        pos = point.get("markPosition") or {}
        if mark_type is None or not isinstance(pos.get(1), (int, float)):
            continue
        world = [round(pos[1], 2), round(pos[3], 2)]
        if type_id in AREA_LABEL_TYPES:
            if point.get("largeAreaId") in blocks:
                areas.append({"id": point["largeAreaId"], "world": world})
            continue
        if type_id in HIDDEN_TYPES or not visible(point, mark_type["row"]):
            continue
        mark = {"id": spawner, "type": type_id, "world": world}
        title = text_of(text, point.get("infoTitle"))
        if title and title != mark_type["name"]:
            mark["title"] = title
        portrait = BATTLE_ICON.search(point.get("POIIcon") or "")
        if portrait:
            mark["portrait"] = f"UI_PetHead_{portrait[1]}.png"
        marks.append(mark)
    return {"marks": marks, "areas": areas}


def block_info(row, text, regions):
    pets = list((row.get("petList") or {}).values())
    conditions = {proto: name for name in CONDITIONS for proto in (row.get(name) or {}).values() if proto in pets}
    return {"name": text.get(row.get("areaName")), "minLevel": row.get("minLevel"), "maxLevel": row.get("maxLevel"),
            "region": regions.get(row.get("mapAreaId")), "regionId": row.get("mapAreaId"),
            "pets": pets, "conditions": conditions,
            "tiers": [row[k] for k in TIER_KEYS if isinstance(row.get(k), (int, float))]}


def build_pets(pm, text, wanted):
    """{prototype id: {name, form, icon, condition, find}} for the given Aniimo forms. condition is the
    game's short find condition, find its longer when / where text (both in the game's rich text)."""
    protos = pm.table("pet_prototype_data", depth=0)
    forms = {form_id: text.get(row.get("name")) for form_id, row in pm.table("pet_form_type_data").items()}
    pets = {}
    for proto in sorted(wanted):
        row = protos.get(proto) or {}
        pets[proto] = {"name": text.get(row.get("name")), "form": forms.get(row.get("formId")),
                       "icon": f"UI_PetHead_{row['iconName']}.png" if row.get("iconName") else None,
                       "condition": text_of(text, row.get("condition")), "find": text_of(text, row.get("weather1"))}
    return pets


def build_spawns(pm):
    """{prototype id: {scene: [[map x, map y], ...]}}: where each Aniimo spawns (map pixels)."""
    spawns = {}
    for proto, blocks in pm.table("map_block_pet_area_index_data", depth=4).items():
        for block in blocks.values():
            points = spawns.setdefault(proto, {}).setdefault(block["sceneId"], [])
            for x, ys in block["mapPosGroup"].items():
                points.extend([round(x, SPAWN_DECIMALS), round(-y, SPAWN_DECIMALS)] for y in ys)
    return spawns


def build_profile(pm, text):
    """What the player profile needs besides your live data: picture and frame files by id, the XP each
    level needs to the next, Primegy's name, icon and recovery, and per server region a moment its day
    starts (the daily reset)."""
    heads = {i: icon_name(row.get("res")) for i, row in pm.table("player_head_icon_data").items()}
    frames = {i: icon_name(row.get("res")) for i, row in pm.table("player_head_frame_data").items()}
    item = pm.table("item_data", depth=0).get(PRIMEGY) or {}
    recovery = pm.table("currency_auto_change_data", depth=2).get(PRIMEGY) or {}
    servers = pm.table("open_server_time_data", depth=2).get(OFFICIAL_SERVERS) or {}
    level_exp = {level: row.get("needExp") for level, row in pm.table("player_level_data").items()}
    return {"headIcons": heads, "headFrames": frames, "levelExp": level_exp,
            "primegy": {"name": text_of(text, item.get("itemName")), "desc": text_of(text, item.get("itemDes")),
                        "icon": icon_name(item.get("icon")), "seconds": recovery.get("cycleValue"),
                        "gain": recovery.get("changeValue")},
            "serverDayStart": servers.get("time", {})}


def main():
    pm, text = PmData(), Text()
    types, groups = build_types(pm, text)
    rows = pm.table("default_map_mark_data")
    regions = {area_id: text_of(text, row.get("areaName")) for area_id, row in pm.table("map_area_config_data").items()}
    blocks = {block_id: block_info(row, text, regions) for block_id, row in pm.table("map_block_config_data", depth=2).items()}
    spawns = build_spawns(pm)
    weathers = {weather_id: {"name": text_of(text, row.get("name")), "icon": icon_name(row.get("icon"))}
                for weather_id, row in pm.table("weather_data").items()}
    pets = build_pets(pm, text, {p for b in blocks.values() for p in b["pets"]} | set(spawns))
    scenes = {}
    for name in pm.object(pm.root):
        match = SCENE_TABLE.match(name)
        if not match:
            continue
        typed = {t: dict(v, row=rows[t]) for t, v in types.items()}
        scene = build_scene(pm.table(name, depth=2), typed, blocks, text)
        if scene["marks"] or scene["areas"]:
            scenes[match[1]] = scene
    used = {m["type"] for s in scenes.values() for m in s["marks"]}
    out = {"types": {t: v for t, v in types.items() if t in used or v["group"] != OTHER_GROUP},
           "groups": groups, "blocks": blocks, "pets": pets, "spawns": spawns,
           "weathers": weathers, "scenes": scenes, "profile": build_profile(pm, text)}
    json.dump(out, open(os.path.join(WEB, "mapdata.json"), "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    total = sum(len(s["marks"]) for s in scenes.values())
    print(f"{total} marks in {len(scenes)} scenes, {len(out['types'])} mark types, {len(pets)} Aniimo forms "
          f"({len(spawns)} with spawn areas) -> app/web/mapdata.json")
    for scene, data in sorted(scenes.items(), key=lambda kv: -len(kv[1]["marks"]))[:8]:
        print(f"  scene {scene}: {len(data['marks'])} marks, {len(data['areas'])} area labels")


if __name__ == "__main__":
    main()
