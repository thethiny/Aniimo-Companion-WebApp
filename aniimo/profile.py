# Your player profile, read-only from your property table (the one handbook.py finds):
#   playerName, level, exp                     exp counts toward the next level (player_level_data needExp)
#   headIcon / headFrame                       picture and frame ids (player_head_icon_data / _frame_data)
#   serverArea                                 which server region you play on (open_server_time_data)
#   lastDayUpdateTs, lastWeekUpdateTs          when the server last ran its daily / weekly reset
#   commonEnergyNums / commonEnergyTimes /     per energy id: how much you have, when the next point comes,
#   energyAutoLimit                            and where natural recovery stops
# The server updates the count and the next time on every point, so between reads the page counts on
# from these by the recovery interval.
from . import handbook, lua

PRIMEGY = 1009   # common energy (and item) id of Primegy


def _number(value):
    return value if isinstance(value, float) else None


def _int(value):
    return int(value) if isinstance(value, float) else None


def _energy(proc, player, key):
    """This energy id's value in one of the player's per-energy maps, or None."""
    return _number(lua.synced_fields(proc, lua.table_ref(player.get(key))).get(PRIMEGY))


def collect(proc):
    """{"name", "level", "exp", "headIcon", "headFrame", "serverArea", "dayReset", "weekReset",
        "primegy": {"value", "max", "nextAt"}}; times are Unix seconds"""
    found = handbook.where(proc)
    player = (lua.read_table(proc, found["player"], depth=0) or {}) if found["player"] else {}
    name = player.get("playerName")
    if not isinstance(name, str) or not name:
        return {}
    value, limit = _energy(proc, player, "commonEnergyNums"), _energy(proc, player, "energyAutoLimit")
    return {"name": name, "level": _int(player.get("level")), "exp": _int(player.get("exp")),
            "headIcon": _int(player.get("headIcon")), "headFrame": _int(player.get("headFrame")),
            "serverArea": _int(player.get("serverArea")),
            "dayReset": _number(player.get("lastDayUpdateTs")), "weekReset": _number(player.get("lastWeekUpdateTs")),
            "primegy": {"value": int(value) if value is not None else None, "max": int(limit) if limit else None,
                        "nextAt": _energy(proc, player, "commonEnergyTimes")}}
