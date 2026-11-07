# Current weather per small area and the time of day, read-only from the game's Lua heap:
#   spaceWeatherInfoMap  {blockId: {weatherId}}  weatherId -> weather_data (Sunny, Rainy, Snowy, ...)
#   pg.timePeriod        TIME_PERIODS; the map draws night versions of weather icons at Night
# The space keeps one live map; older ones linger until the GC frees them, so the fullest one wins.
# Finding the tables takes a heap search; after that they're read directly.
from . import lua

KEY, KEY_TIME = "spaceWeatherInfoMap", "timePeriod"
MIN_ID = 10000   # small-area ids; smaller keys are type descriptors
TIME_PERIODS = {1: "Morning", 2: "Day", 3: "Dusk", 4: "Night"}   # Const TimePeriod


def _is_pg(fields):
    """The game's global table `pg` (it holds me / game / global); AI components have a timePeriod too."""
    return "me" in fields and "global" in fields and isinstance(fields.get(KEY_TIME), float)


def _weather(proc, table):
    areas = lua.synced_children(proc, table, MIN_ID)
    return {block: int(info["weatherId"]) for block, info in areas.items() if isinstance(info.get("weatherId"), float)}


def _locate(proc):
    """{"weather": the fullest weather map, "pg": the global table or None}"""
    strs = lua.find_strings(proc, [KEY, KEY_TIME])
    best, best_size = None, 0
    for node in lua.tables_with_key(proc, strs[KEY]):
        raw = proc.u64(node)
        if raw is not None and lua.itype(raw) == lua.TAB:
            size = len(_weather(proc, raw & lua.PTR_MASK))
            if size > best_size:
                best, best_size = raw & lua.PTR_MASK, size
    pg = None
    for table in set(lua.owner_tables(proc, lua.tables_with_key(proc, strs[KEY_TIME]), max_index=1024).values()):
        if _is_pg(lua.read_table(proc, table, depth=0) or {}):
            pg = table
            break
    return {"weather": best, "pg": pg}


def _still_valid(proc, found):
    return bool(found["weather"]) and bool(_weather(proc, found["weather"]))


_where = lua.Located(_locate, _still_valid)


def collect(proc):
    """{"weather": {blockId: weatherId}, "timePeriod": 1..4 or None}"""
    found = _where.get(proc)
    weather = _weather(proc, found["weather"]) if found["weather"] else {}
    pg = (lua.read_table(proc, found["pg"], depth=0) or {}) if found["pg"] else {}
    period = pg.get(KEY_TIME) if _is_pg(pg) else None
    return {"weather": weather, "timePeriod": int(period) if period in TIME_PERIODS else None}
