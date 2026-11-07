# The game's global Lua table `pg` (it holds me / game / world / space / timePeriod). It lives for the whole
# session, so it's found once (a heap search, ~30 s) and everything live is reached from it directly:
#   pg.me.__Properties__   your live property table (name, level, handbook, Primegy, ...). Teleports and
#                          loading screens rebuild it, but pg.me always points at the current one
#   pg.timePeriod          time of day (1 Morning, 2 Day, 3 Dusk, 4 Night)
#   pg.game.map.sceneId    the map you're on (3000 Idyll, 3003 Whisperwake Isles, 501 Astra, ...), also for
#                          the ones you walk into without a loading screen. Seamless sub-areas of a map
#                          (3003001, 5010001: scene_seamless_data) have no map of their own: mainSceneId
#                          names the map they belong to
from . import lua

MARK_KEY = "getLocalizationTimeYMD"   # a function only pg has: few tables to check, unlike "timePeriod"


def is_pg(fields):
    return "me" in fields and "global" in fields and isinstance(fields.get("timePeriod"), float)


def _locate(proc):
    strs = lua.find_strings(proc, [MARK_KEY])
    for table in set(lua.owner_tables(proc, lua.tables_with_key(proc, strs[MARK_KEY]), max_index=1024).values()):
        if is_pg(lua.read_table(proc, table, depth=0) or {}):
            return table
    return None


def _still_valid(proc, found):
    return bool(found) and is_pg(lua.read_table(proc, found, depth=0) or {})


_where = lua.Located(_locate, _still_valid, max_age=float("inf"))


def fields(proc):
    """pg's own fields, or {} while it isn't found."""
    found = _where.get(proc)
    table = (lua.read_table(proc, found, depth=0) or {}) if found else {}
    return table if is_pg(table) else {}


SUB_SCENE = 100000   # seamless sub-area ids are 7 digits (3003001); maps have up to 5 (3000, 501)


def scene(proc):
    """The id of the map you're on, or None."""
    game = lua.read_table(proc, lua.table_ref(fields(proc).get("game")), depth=0) or {}
    system = lua.read_table(proc, lua.table_ref(game.get("map")), depth=0) or {}
    scene_id, main = system.get("sceneId"), system.get("mainSceneId")
    if isinstance(scene_id, float) and scene_id >= SUB_SCENE and isinstance(main, float) and main > 0:
        scene_id = main
    return int(scene_id) if isinstance(scene_id, float) and scene_id > 0 else None


def me(proc):
    """Your live property table's address, or None."""
    entity = lua.read_table(proc, lua.table_ref(fields(proc).get("me")), depth=0) or {}
    return lua.table_ref(entity.get("__Properties__"))
