# Current weather per small area, read-only from the game's Lua heap (ClientSpaceWeatherComponent):
#   spaceWeatherInfoMap  {blockId: {weatherId}}  weatherId -> weather_data (Sunny, Rainy, Snowy, ...)
# The space keeps one live map; older ones linger until the GC frees them, so the fullest one wins.
from . import lua

KEY = "spaceWeatherInfoMap"
MIN_ID = 10000   # small-area ids; smaller keys are type descriptors


def collect(proc):
    """{"weather": {blockId: weatherId}}"""
    best = {}
    for node in lua.tables_with_key(proc, lua.find_strings(proc, [KEY])[KEY]):
        raw = proc.u64(node)
        if raw is None or lua.itype(raw) != lua.TAB:
            continue
        areas = lua.synced_children(proc, raw & lua.PTR_MASK, MIN_ID)
        found = {block: int(info["weatherId"]) for block, info in areas.items() if isinstance(info.get("weatherId"), float)}
        if len(found) > len(best):
            best = found
    return {"weather": best}
