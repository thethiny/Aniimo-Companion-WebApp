# Builds app/web/mapdata.json from the game's config data: every map mark of every scene (with its name,
# icon and Aniimo portrait), the mark types grouped like the game's map filters, and the small areas
# (name, level range, region, label position, the Aniimo living there) and every Aniimo's spawn areas (the
# yellow squares the game draws when you track one). Live state (locked / completed, your own marks, what
# you've seen and caught) comes from the server; this is the static layer. Also writes app/web/collection.json,
# the Aniilogs for the collection page.
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
EXTRA_GROUP = {10406: 1, 10407: 1, 33: 1, 10400: 2, 10415: 3, 1019: 3, 900001: 3}   # 900001: Pickups
HIDDEN_TYPES = {20}           # internal transporters clustered in one spot; the game doesn't draw them
# Event tracking types carry internal names; use what the player sees
TYPE_NAMES = {27: "Elite Training", 29: "Special Training", 30: "Ecological Investigation",
              31: "Aniimo Discovery", 35: "Carnival Park",
              10410: "Nurture Bloom"}   # also called "Bloom" in game, next to the teleport Blooms
OTHER_GROUP, EVENTS_GROUP = 6, "events"
# Aniimo eggs in the open world (Grassland Egg, Elite Egg, ...) are one-time chests placed in each map's entity
# table, not map marks; they're drawn with the game's own egg mark ("Aniimo Egg", also used in Egg Heist)
WORLD_EGG_TYPE = 1019
WORLD_EGG_MODELS = ("SoulEgg", "RareEgg")   # chest models of the world eggs
ENTITY_TABLE = re.compile(r"^Scene\.(\d+)\.scene_entity_data$")
SANDBOX_TABLE = "Scene.{}.scene_sandbox_data"   # an entity's placement group (sandboxId): its poiType says how it appears
# How an egg appears, by its placement group's poiType (poi_info_data); groups that are always there have no note
EGG_NOTES = {400: "Reward for the puzzle here", 405: "Reward for the puzzle or event here",
             504: "Reward for the puzzle here", 508: "Reward for the puzzle here",
             2001: "Part of a quest", 4001: "Only during a limited-time event", 301: "Reward from the Branch",
             501: "Only while its Aniimo are nesting here"}   # creature spots: the flock, nest and egg come and go together
OTHER_LEVEL = 1003   # "other level" groups: told apart by the words in their (internal, Chinese) names
OTHER_LEVEL_NOTES = (("流星", "Only when a meteor lands here"), ("雪", "Only in snowy weather"),
                     ("洞穴", "In a hidden cave"))
OTHER_LEVEL_NOTE = "Only at certain times"
# Small pickups placed in each map's entity table (not map marks), all under one "Pickups" filter button with an
# icon per kind. Told apart by their model; the kinds without an item icon in game use the page's own icons.
PICKUP_TYPE = 900001   # not a game mark type: the page's own
PICKUP_NAME = "Pickups"
PICKUP_KINDS = (       # (kind, name, model fragments, icon)
    ("aniipod", "Aniipod", ("NormalBallChest", "NormalBall_EnvObj"), "ui_item_110001.png"),
    ("aniipod-pro", "Aniipod Pro", ("GreatBallChest", "GreatBall_EnvObj"), "ui_item_110002.png"),
    ("starine", "Starine", ("CollectFruit_",), "ui_item_130112_big.png"),
    ("sparkle", "Sparkle", ("DistanceShiningStar",), "pickups/sparkle.svg"),
    ("pot", "Pot", ("PushBox_Chest",), "pickups/pot.svg"),
    ("dig", "Dig spot", ("Mound_Mix_Chest",), "pickups/dig.svg"),
)
PICKUP_ICONS = [icon for _, _, _, icon in PICKUP_KINDS if "/" not in icon]   # item icons build_icons extracts
BATTLE_ICON = re.compile(r"UI_Icon_POI_Battle_(\d+)\.png$")   # Alpha / Omega: the Aniimo you fight
SPAWN_DECIMALS = 1
# Per-area lists of Aniimo that only appear in some weather (the badge on their portrait in game)
CONDITIONS = ("snow", "rain", "thunder", "storm", "aurora")
TIER_KEYS = ("unlockReward1", "unlockReward2", "unlockReward3")   # bronze / silver / gold completion
PRIMEGY = 1009             # Primegy's item, currency and energy id
OFFICIAL_SERVERS = 2       # open_server_time_data row of the live servers (1 is the developers' test server)
DAILY_ACTIVITY = 10000024  # event activity id of Daily Activity
SKILL_POINT = 1000         # Report Card: the Skill Point talents are learned with
TALENT_TREES = {1: "active", 2: "passive"}   # player_skill_tree_data abilityType of the two talent tabs
QUEST_TABS = {1: "lore", 2: "quests", 3: "journey"}   # quest_catalog mainType of the HUD's quest tabs
RANK_NUMERALS = ("I", "II", "III")
DAILY_TASK, DAILY_CHEST = 121, 122   # event_task_data actTaskType: a task, a reward chest along the bar


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


def height(pos):
    """A placement's height (world y, as the game's map shows it), or None."""
    return round(pos[2]) if isinstance(pos.get(2), (int, float)) else None


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
        mark = {"id": spawner, "type": type_id, "world": world, "height": height(pos)}
        title = text_of(text, point.get("infoTitle"))
        if title and title != mark_type["name"]:
            mark["title"] = title
        portrait = BATTLE_ICON.search(point.get("POIIcon") or "")
        if portrait:
            mark["portrait"] = f"UI_PetHead_{portrait[1]}.png"
        marks.append(mark)
    return {"marks": marks, "areas": areas}


def egg_note(group):
    """How an egg in this placement group appears ("Only when a meteor lands here"), or None if it's always there."""
    poi, name = group.get("poiType"), group.get("name") or ""
    if poi == OTHER_LEVEL:
        return next((note for word, note in OTHER_LEVEL_NOTES if word in name), OTHER_LEVEL_NOTE)
    return EGG_NOTES.get(poi)


def placed(pm, wanted):
    """(scene id, entity id, entity) for every placement in the maps' entity tables whose template `wanted`
    accepts and that has a position."""
    for name in pm.object(pm.root):
        match = ENTITY_TABLE.match(name)
        if not match:
            continue
        for entity_id, entity in pm.table(name, depth=2).items():
            if wanted(entity.get("idInType")) and isinstance((entity.get("position") or {}).get(1), (int, float)):
                yield match[1], entity_id, entity


def placed_mark(entity_id, entity, type_id, **fields):
    pos = entity["position"]
    return dict({"id": entity_id, "type": type_id, "world": [round(pos[1], 2), round(pos[3], 2)], "height": height(pos)},
                **fields)


def build_world_eggs(pm, text):
    """{scene id: [marks]} for the Aniimo eggs placed in the open world, titled with the egg's name, with a note
    on how the ones that aren't always there appear."""
    eggs = {chest: text_of(text, row.get("name")) for chest, row in pm.table("chest_data").items()
            if any(model in str(row.get("model")) for model in WORLD_EGG_MODELS)}
    found, groups = {}, {}
    for scene, entity_id, entity in placed(pm, lambda template: template in eggs):
        if scene not in groups:
            groups[scene] = pm.table(SANDBOX_TABLE.format(scene), depth=1)
        mark = placed_mark(entity_id, entity, WORLD_EGG_TYPE, title=eggs[entity["idInType"]])
        note = egg_note(groups[scene].get(entity.get("sandboxId")) or {})
        if note:
            mark["note"] = note
        found.setdefault(scene, []).append(mark)
    return found


def pickup_type():
    """The "Pickups" mark type: its filter button shows the first kind's icon; marks carry their kind."""
    return {"name": PICKUP_NAME, "icon": PICKUP_KINDS[0][3], "group": EXTRA_GROUP[PICKUP_TYPE],
            "kinds": {kind: {"name": name, "icon": icon} for kind, name, _, icon in PICKUP_KINDS}}


def build_pickups(pm):
    """{scene id: [marks]} for the small pickups (PICKUP_KINDS) placed in the open world."""
    kind_of = {}
    for chest, row in pm.table("chest_data").items():
        model = str(row.get("model"))
        kind = next((kind for kind, _, fragments, _ in PICKUP_KINDS if any(f in model for f in fragments)), None)
        if kind:
            kind_of[chest] = kind
    found = {}
    for scene, entity_id, entity in placed(pm, lambda template: template in kind_of):
        found.setdefault(scene, []).append(placed_mark(entity_id, entity, PICKUP_TYPE, kind=kind_of[entity["idInType"]]))
    return found


def block_info(row, text, regions):
    pets = list((row.get("petList") or {}).values())
    conditions = {proto: name for name in CONDITIONS for proto in (row.get(name) or {}).values() if proto in pets}
    return {"name": text.get(row.get("areaName")), "minLevel": row.get("minLevel"), "maxLevel": row.get("maxLevel"),
            "region": regions.get(row.get("mapAreaId")), "regionId": row.get("mapAreaId"),
            "pets": pets, "conditions": conditions,
            "tiers": [row[k] for k in TIER_KEYS if isinstance(row.get(k), (int, float))]}


def build_pets(pm, text, wanted):
    """{prototype id: {name, form, icon, condition, find, prismana}} for the given Aniimo forms. condition is
    the game's short find condition, find its longer when / where text (both in the game's rich text);
    prismana: it's a Prismana form."""
    protos = pm.table("pet_prototype_data", depth=0)
    forms = {form_id: text.get(row.get("name")) for form_id, row in pm.table("pet_form_type_data").items()}
    pets = {}
    for proto in sorted(wanted):
        row = protos.get(proto) or {}
        pets[proto] = {"name": text.get(row.get("name")), "form": forms.get(row.get("formId")),
                       "icon": f"UI_PetHead_{row['iconName']}.png" if row.get("iconName") else None,
                       "condition": text_of(text, row.get("condition")), "find": text_of(text, row.get("weather1")),
                       "prismana": row.get("formId") == PRISMANA_FORM}
    return pets


def prismana_forms(pm):
    """Every Prismana form (they spawn as their own forms, so wild ones are told apart by their form)."""
    return {proto for proto, row in pm.table("pet_prototype_data", depth=0).items() if row.get("formId") == PRISMANA_FORM}


def tag_names(pm, text):
    """{tag: {name, desc, icon}} of the variants (Sparkling, Alpha, Prismana, Umbral) as the game shows them."""
    strings = pm.table("gamestring_config_data")
    return {tag: {"name": game_string(strings, text, name), "desc": game_string(strings, text, desc), "icon": icon}
            for tag, (name, desc, icon) in TAGS.items()}


def build_spawns(pm):
    """{prototype id: {scene: [[map x, map y], ...]}}: where each Aniimo spawns (map pixels)."""
    spawns = {}
    for proto, blocks in pm.table("map_block_pet_area_index_data", depth=4).items():
        for block in blocks.values():
            points = spawns.setdefault(proto, {}).setdefault(block["sceneId"], [])
            for x, ys in block["mapPosGroup"].items():
                points.extend([round(x, SPAWN_DECIMALS), round(-y, SPAWN_DECIMALS)] for y in ys)
    return spawns


def build_daily_activity(pm, text):
    """Daily Activity: its point cap, the tasks (title, description, points) in the game's order and how
    many reward chests sit along the bar (evenly spaced up to the cap)."""
    rows = {i: r for i, r in pm.table("event_task_data").items() if r.get("activityId") == DAILY_ACTIVITY}
    config = pm.table("event_daily_active_data").get(1) or {}
    tasks = [{"id": i, "title": text_of(text, r.get("eventTitle")), "desc": text_of(text, r.get("taskDes")),
              "points": r.get("award")}
             for i, r in sorted(rows.items(), key=lambda kv: (kv[1].get("groupId", 0), kv[0]))
             if r.get("actTaskType") == DAILY_TASK]
    chests = [i for i, r in sorted(rows.items(), key=lambda kv: kv[1].get("sort", 0)) if r.get("actTaskType") == DAILY_CHEST]
    return {"limit": config.get("scoreLimit"), "tasks": tasks, "chests": chests}


def build_eggs(pm, text):
    """{egg item id: {name, icon, minutes}}: every egg that hatches, and its base hatching time."""
    items = pm.table("item_data", depth=0)
    eggs = {}
    for egg, row in pm.table("pet_hatch_egg_data").items():
        item = items.get(egg) or {}
        eggs[egg] = {"name": text_of(text, item.get("itemName")), "icon": icon_name(item.get("icon")),
                     "quality": item.get("quality"), "minutes": row.get("times")}
    return eggs


def build_branches(pm, text):
    """{Branch id: {name, amber: item id, amberName, amberIcon, points: [points to reach each level]}}: the
    region trees you infuse with that region's Lumin Amber."""
    items = pm.table("item_data", depth=0)
    branches = {}
    for branch, levels in pm.table("leylinetree_data", depth=2).items():
        rows = [row for _, row in sorted(levels.items(), key=lambda kv: int(kv[0]))]
        amber = rows[0].get("stoneId")
        item = items.get(amber) or {}
        branches[branch] = {"name": text_of(text, rows[0].get("name")), "amber": amber,
                            "amberName": text_of(text, item.get("itemName")), "amberIcon": icon_name(item.get("icon")),
                            "points": [row.get("point", 0) for row in rows]}
    return branches


def text_ref(text, value):
    """Quest texts are text hashes stored as digit strings."""
    return text_of(text, value) if value not in (None, "", "0") else None


def build_quests(pm, text):
    """What the tracked quests card needs: {"names": {quest: {name, chapter, desc}}} for quests the HUD can
    track (story sections and Elite Training) with their HUD icon, {"steps": {quest: {parent, visible, desc, objectives:
    [[objective id, text, target]]}}} for every quest step that has text or a parent quest."""
    base = pm.table("Quest.quest_base", depth=4)
    chapters = pm.table("quest_chapter")
    names = {}
    sections = pm.table("quest_main_section_chapter_id")
    for chapter, rows in pm.table("quest_main", depth=2).items():
        for section, row in rows.items():
            quest = (sections.get(section) or {}).get("questGroupId")
            if quest:
                names[quest] = {"name": text_of(text, row.get("sectionName")),
                                "chapter": text_of(text, (chapters.get(chapter) or {}).get("chapterName")),
                                "desc": text_of(text, row.get("startDesc"))}
    entries = pm.table("special_train_entry_data", depth=2)
    for step, row in pm.table("quest_special_train_revert").items():
        parent = (base.get(step) or {}).get("parentQuest")
        if parent and parent not in names:
            entry = (entries.get(row.get("taskId")) or {}).get(row.get("stageId")) or {}
            names[parent] = {"name": text_of(text, row.get("taskName")), "chapter": "Elite Training",
                             "desc": text_of(text, entry.get("desc"))}
    steps = {}
    for quest, row in base.items():
        objectives = []
        for objective, info in sorted((row.get("objectives") or {}).items()):
            label = text_ref(text, info.get("desc"))
            target = (info.get("paramVals") or {}).get(5)
            if label:
                objectives.append([objective, label, target if isinstance(target, (int, float)) else None])
        desc = text_ref(text, row.get("desc"))
        if objectives or desc or row.get("parentQuest"):   # steps without text still link deeper ones up
            steps[quest] = {"parent": row.get("parentQuest"), "visible": row.get("visible", False), "desc": desc,
                            "objectives": objectives}
    icons = pm.table("quest_task_type")
    for quest, info in names.items():
        info["icon"] = icon_name((icons.get(quest) or {}).get("mapMarkPoint"))   # the HUD's quest icon
    tab_icons = {}
    for _, row in sorted(pm.table("quest_catalog").items()):   # each tab's own icon, for when it tracks nothing
        tab = QUEST_TABS.get(row.get("mainType"))
        if tab and tab not in tab_icons:
            tab_icons[tab] = icon_name(row.get("mapMarkPoint"))
    return {"names": names, "steps": steps, "tabIcons": tab_icons}


def build_talents(pm, text):
    """Talents: the Skill Point item (name, icon) and per talent its tab ("active" / "passive") and the
    Skill Points each level costs, so the page can add up what you've spent on each tab."""
    tree = pm.table("player_skill_tree_data")
    item = pm.table("item_data", depth=0).get(SKILL_POINT) or {}
    talents = {}
    for talent, levels in pm.table("player_skill_data", depth=3).items():
        tab = TALENT_TREES.get((tree.get(talent) or {}).get("abilityType"))
        if tab:
            costs = [(levels[level].get("needItem") or {}).get(SKILL_POINT, 0) for level in sorted(levels)]
            talents[talent] = {"tab": tab, "costs": costs}
    return {"point": SKILL_POINT, "pointName": text_of(text, item.get("itemName")), "pointIcon": icon_name(item.get("icon")),
            "talents": talents}


def build_ranks(pm, text):
    """{star title: {name, icon}}: your rank as the game writes it ("Trailblazer I") and its small badge."""
    ranks = {}
    for title, row in pm.table("player_title_data").items():
        first, last = text_of(text, row.get("nameFirst")), text_of(text, row.get("nameLast"))
        name = f"{last} {first}" if first in RANK_NUMERALS else " ".join(filter(None, (first, last)))
        ranks[title] = {"name": name, "icon": icon_name(row.get("iconSmall"))}
    return ranks


def build_profile(pm, text):
    """What the player profile needs besides your live data: picture and frame files by id, the XP to reach
    each level (from the one before), Primegy's name, icon and recovery, and per server region a moment its day
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
                        "gain": recovery.get("changeValue"),
                        "limit": (recovery.get("autoChangeRange") or {}).get(2)},   # where natural recovery stops
            "serverDayStart": servers.get("time", {}),
            "daily": build_daily_activity(pm, text), "eggs": build_eggs(pm, text),
            "branches": build_branches(pm, text), "quests": build_quests(pm, text),
            "talents": build_talents(pm, text), "ranks": build_ranks(pm, text), "rarities": rarity_names(pm, text),
            "tags": tag_names(pm, text)}


# ---- collection page (app/web/collection.json, loaded the first time the page opens) ----
SEASON_ANIILOG = 1000          # Season 1 Keepsakes: numbered "S1" in game, no fixed maximum
ANIILOG_ORDER = (300001, 300301, SEASON_ANIILOG)   # the game's Select Aniilog order
# Element icon per element_prop_data name (the game's HUD element icons)
ELEMENT_ICONS = {"Ice": "ice", "Water": "water", "Electric": "electricity", "Fire": "fire", "Grass": "grass",
                 "Rock": "earth", "Wind": "wind", "Dark": "shadow", "Holy": "light"}
RARITY_KEYS = {1: "INTERFACE_DISPLAY_RATING_1", 2: "INTERFACE_DISPLAY_RATING_2", 3: "INTERFACE_DISPLAY_RATING_3",
               4: "INTERFACE_DISPLAY_RATING_4"}   # propertyScoreStage -> its name
# Tags: name, tooltip and icon as the game shows them
TAGS = {"sparkling": ("PET_SHINY_LABEL_TITLE", "PET_SHINY_LABEL_TIPS", "UI_Img_PetReport_Flash.png"),
        "alpha": ("PET_LABEL_ELITE", "PET_ELITESIZE_LABEL_TIPS", "UI_Img_PetReport_Boss.png"),
        "prismana": ("FILTER_RAINBOW", None, "UI_Img_PetReport_RainBow.png"),
        "umbral": ("PET_DARK_LABEL_TITLE", "PET_DARK_LABEL_TIPS", "UI_Img_PetReport_Once.png")}
# Counters along the top: their names ("_SPECIES" variants are the Family Mode wording) and icons
COUNTERS = {"owned": ("PETMANUAL_COUNT_TXT_1", "UI_Img_PetReport_CatchQuantity.png"),
            "sparkling": ("PETMANUAL_COUNT_TXT_2", "UI_Img_PetReport_Flash.png"),
            "prismana": ("PETMANUAL_COUNT_TXT_3", "UI_Img_PetReport_RainBow.png"),
            "collection": ("PETMANUAL_COUNT_TXT_5", None)}
MODE_KEYS = {"form": "FORM", "family": "SPECIES"}
PRISMANA_FORM = 5              # pet_form_type_data id of "Prismana Form"
VARIANT_CHANCES = {"sparkling": "shiny_rate", "umbral": "dark_rate"}   # pet_prototype_data chance per variant


def game_string(strings, text, key):
    """A UI string by its gamestring_config_data key ("FORM" -> "Form Mode")."""
    return text_of(text, (strings.get(key) or {}).get("desc")) if key else None


def rarity_names(pm, text):
    """{stage: name} of the rarities (Innate Potential): 1 Common, 2 Good, 3 Elite, 4 Perfect."""
    strings = pm.table("gamestring_config_data")
    return {stage: game_string(strings, text, key) for stage, key in RARITY_KEYS.items()}


def collection_icons():
    """Icon files the collection page uses besides Aniimo heads: tags and elements."""
    return ({icon for _, _, icon in TAGS.values()} | {icon for _, icon in COUNTERS.values() if icon}
            | {f"UI_HUD_Element_Icon_{name}.png" for name in ELEMENT_ICONS.values()})


def build_collection(pm, text):
    """The Aniilogs as the game's collection shows them: per Aniilog its families in book order (each the
    forms it holds there) and the game's totals; every form's name, icon, element and find text; and the
    names of modes, counters, tags, rarities and personalities."""
    strings = pm.table("gamestring_config_data")
    countries = pm.table("country_area_data")
    form_lists = pm.table("pet_research_country_form_list", depth=2)
    family_lists = pm.table("pet_research_country_species_list", depth=2)
    config = pm.table("pet_handbook_config_data", depth=2)
    base_of = {form: base for base, forms in pm.table("pet_base_prototype_to_prototype_map", depth=2).items()
               for form in forms.values()}
    aniilogs, wanted = [], set()
    for aniilog in ANIILOG_ORDER:
        forms = list((form_lists.get(aniilog) or {}).values())
        families = [[base, [f for f in forms if base_of.get(f, f) == base]]
                    for base in (family_lists.get(aniilog) or {}).values()]
        wanted.update(forms)
        aniilogs.append({"id": aniilog, "name": text_of(text, (countries.get(aniilog) or {}).get("name")),
                         "season": aniilog == SEASON_ANIILOG, "families": families,
                         "maxForms": (config.get("formCollectMaxMap") or {}).get(aniilog, len(forms)),
                         "maxFamilies": (config.get("speciesCollectMaxMap") or {}).get(aniilog, len(families))})
    protos = pm.table("pet_prototype_data", depth=2)
    elements = pm.table("element_prop_data")
    forms = build_pets(pm, text, wanted)
    for form, info in forms.items():
        row = protos.get(form) or {}
        if not isinstance(row.get("elementType"), dict):   # a few form rows don't decode: use their base form's
            row = protos.get(base_of.get(form)) or {}
        info["elements"] = [name for name in (row.get("elementType") or {}).values() if name in ELEMENT_ICONS]
        # Which variants this form can come as (a chance above zero in its config)
        info["can"] = [tag for tag, key in VARIANT_CHANCES.items() if isinstance(row.get(key), (int, float)) and row[key] > 0]
    return {"aniilogs": aniilogs, "forms": forms,
            "modes": {mode: game_string(strings, text, key) for mode, key in MODE_KEYS.items()},
            "counters": {name: {"form": game_string(strings, text, key), "family": game_string(strings, text, key + "_SPECIES"),
                                "icon": icon} for name, (key, icon) in COUNTERS.items()},
            "tags": tag_names(pm, text),
            "elements": {name: {"name": text_of(text, row.get("name_ch")), "color": row.get("colourValue"),
                                "icon": f"UI_HUD_Element_Icon_{ELEMENT_ICONS[name]}.png"}
                         for row in elements.values() for name in [row.get("name")] if name in ELEMENT_ICONS},
            "rarities": rarity_names(pm, text),
            "personalities": {nature: text_of(text, row.get("name")) for nature, row in pm.table("pet_nature_data").items()},
            "personalityTitle": game_string(strings, text, "PET_INFO_PERSONALITY_TITLE")}


def main():
    pm, text = PmData(), Text()
    types, groups = build_types(pm, text)
    rows = pm.table("default_map_mark_data")
    regions = {area_id: text_of(text, row.get("areaName")) for area_id, row in pm.table("map_area_config_data").items()}
    blocks = {block_id: block_info(row, text, regions) for block_id, row in pm.table("map_block_config_data", depth=2).items()}
    spawns = build_spawns(pm)
    weathers = {weather_id: {"name": text_of(text, row.get("name")), "icon": icon_name(row.get("icon"))}
                for weather_id, row in pm.table("weather_data").items()}
    pets = build_pets(pm, text, {p for b in blocks.values() for p in b["pets"]} | set(spawns) | prismana_forms(pm))
    scenes = {}
    for name in pm.object(pm.root):
        match = SCENE_TABLE.match(name)
        if not match:
            continue
        typed = {t: dict(v, row=rows[t]) for t, v in types.items()}
        scene = build_scene(pm.table(name, depth=2), typed, blocks, text)
        if scene["marks"] or scene["areas"]:
            scenes[match[1]] = scene
    types[PICKUP_TYPE] = pickup_type()
    for found in (build_world_eggs(pm, text), build_pickups(pm)):
        for scene, marks in found.items():
            scenes.setdefault(scene, {"marks": [], "areas": []})["marks"].extend(marks)
    used = {m["type"] for s in scenes.values() for m in s["marks"]}
    out = {"types": {t: v for t, v in types.items() if t in used or v["group"] != OTHER_GROUP},
           "groups": groups, "blocks": blocks, "pets": pets, "spawns": spawns,
           "weathers": weathers, "scenes": scenes, "profile": build_profile(pm, text)}
    json.dump(out, open(os.path.join(WEB, "mapdata.json"), "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    collection = build_collection(pm, text)
    json.dump(collection, open(os.path.join(WEB, "collection.json"), "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"{len(collection['forms'])} Aniimo forms in {len(collection['aniilogs'])} Aniilogs -> app/web/collection.json")
    total = sum(len(s["marks"]) for s in scenes.values())
    print(f"{total} marks in {len(scenes)} scenes, {len(out['types'])} mark types, {len(pets)} Aniimo forms "
          f"({len(spawns)} with spawn areas) -> app/web/mapdata.json")
    for scene, data in sorted(scenes.items(), key=lambda kv: -len(kv[1]["marks"]))[:8]:
        print(f"  scene {scene}: {len(data['marks'])} marks, {len(data['areas'])} area labels")


if __name__ == "__main__":
    main()
