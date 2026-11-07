# Current weather per small area and the time of day, read-only from the game's Lua heap:
#   spaceWeatherInfoMap  {blockId: {weatherId}}  weatherId -> weather_data (Sunny, Rainy, Snowy, ...)
#   pg.timePeriod        TIME_PERIODS; the map draws night versions of weather icons at Night
# The space keeps one live map; older ones linger until the GC frees them, so the fullest one wins.
from . import lua

KEY, KEY_TIME = "spaceWeatherInfoMap", "timePeriod"
MIN_ID = 10000   # small-area ids; smaller keys are type descriptors
TIME_PERIODS = {1: "Morning", 2: "Day", 3: "Dusk", 4: "Night"}   # Const TimePeriod
# The game's global table `pg` (it holds me / game / global); AI components have a timePeriod of their own
_pg = lua.Holder(lambda fields: "me" in fields and "global" in fields and isinstance(fields.get(KEY_TIME), float))


def collect(proc):
    """{"weather": {blockId: weatherId}, "timePeriod": 1..4 or None}"""
    strs = lua.find_strings(proc, [KEY, KEY_TIME])
    best = {}
    for node in lua.tables_with_key(proc, strs[KEY]):
        raw = proc.u64(node)
        if raw is None or lua.itype(raw) != lua.TAB:
            continue
        areas = lua.synced_children(proc, raw & lua.PTR_MASK, MIN_ID)
        found = {block: int(info["weatherId"]) for block, info in areas.items() if isinstance(info.get("weatherId"), float)}
        if len(found) > len(best):
            best = found
    period = _pg.fields(proc, lua.tables_with_key(proc, strs[KEY_TIME])).get(KEY_TIME)
    return {"weather": best, "timePeriod": int(period) if period in TIME_PERIODS else None}
