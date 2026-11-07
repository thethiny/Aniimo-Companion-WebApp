# Current weather per small area and the time of day, read-only from the game's Lua heap:
#   spaceWeatherInfoMap  {blockId: {weatherId}}  weatherId -> weather_data (Sunny, Rainy, Snowy, ...)
#   pg.timePeriod        TIME_PERIODS; the map draws night versions of weather icons at Night (see pg.py)
# Each respawn builds a new space with its own map and the old ones linger, frozen: the live one belongs to
# the space whose logic ran last (lastLogicTickTime). When even that one last ran before your character
# spawned (bornTime), you moved on to a newer space and the maps are searched again.
from . import handbook, lua, pg

KEY, KEY_TIME = "spaceWeatherInfoMap", "timePeriod"
MIN_ID = 10000   # small-area ids; smaller keys are type descriptors
TIME_PERIODS = {1: "Morning", 2: "Day", 3: "Dusk", 4: "Night"}   # Const TimePeriod


def _weather(proc, table):
    areas = lua.synced_children(proc, table, MIN_ID)
    return {block: int(info["weatherId"]) for block, info in areas.items() if isinstance(info.get("weatherId"), float)}


def _last_tick(proc, table):
    """When the space owning this weather map last ran its logic (Unix seconds), or 0."""
    owner = lua.table_ref((lua.read_table(proc, table, depth=0) or {}).get("_owner"))
    tick = (lua.read_table(proc, owner, depth=0) or {}).get("lastLogicTickTime") if owner else None
    return tick if isinstance(tick, float) else 0.0


def _locate(proc):
    """Every weather map that has areas in it"""
    strs = lua.find_strings(proc, [KEY])
    tables = {raw & lua.PTR_MASK for raw in map(proc.u64, lua.tables_with_key(proc, strs[KEY]))
              if raw is not None and lua.itype(raw) == lua.TAB}
    return [table for table in tables if _weather(proc, table)]


def _live(proc, tables):
    """The map of the space that ran last, if it ran since your character spawned; else None."""
    tick, table = max(((_last_tick(proc, table), table) for table in tables), default=(0.0, None))
    born = handbook.live_player(proc).get("bornTime")
    return table if table and tick >= (born if isinstance(born, float) else 0.0) else None


_maps = lua.Located(_locate, lambda proc, found: _live(proc, found) is not None)


def collect(proc):
    """{"weather": {blockId: weatherId}, "timePeriod": 1..4 or None}"""
    live = _live(proc, _maps.get(proc))
    period = pg.fields(proc).get(KEY_TIME)
    return {"weather": _weather(proc, live) if live else {},
            "timePeriod": int(period) if period in TIME_PERIODS else None}
