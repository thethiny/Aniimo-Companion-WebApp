# Your player profile, read-only from your live property table (handbook.live_player):
#   playerName, level, exp                     exp counts toward the next level (player_level_data needExp)
#   starTitle                                  your rank ("Trailblazer I", player_title_data)
#   headIcon / headFrame                       picture and frame ids (player_head_icon_data / _frame_data)
#   serverArea                                 which server region you play on (open_server_time_data)
#   lastDayUpdateTs, lastWeekUpdateTs          when the server last ran its daily / weekly reset
#   commonEnergyNums / commonEnergyTimes /     per energy id: how much you have, when the next point comes,
#   energyAutoLimit                            and where natural recovery stops
#   activityDailyActive                        Daily Activity: dailyActiveScore and per task its state
#   leylineTreeInfoMap / commonMoneyNums       per region Branch: points infused; its Lumin Amber not used yet
#   skillNodeMap                               {talent id: {lv}}: the talents you've learned and their level
#   hatchSlotMap                               {slot: {status, endTs, item: {id}, speedUpTime}}: eggs hatching
#                                              (filled once you open the hatch screen; slot 1 is the middle)
#   interactRecord                             {placed object id: 1}: what you've picked up or used, world eggs
#                                              included (an egg's id is its mark id on the map)
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


def _daily_activity(proc, player):
    """{"score", "tasks": {task id: state}} (state 3 = claimed), or None"""
    daily = lua.synced_fields(proc, lua.table_ref(player.get("activityDailyActive")))
    base = lua.synced_fields(proc, lua.table_ref(daily.get("activityBase")))
    tasks = lua.synced_children(proc, lua.table_ref(base.get("activityTasks")))
    if not isinstance(daily.get("dailyActiveScore"), float):
        return None
    return {"score": int(daily["dailyActiveScore"]),
            "tasks": {task: _int(fields.get("state")) for task, fields in tasks.items()}}


def _branches(proc, player):
    """{Branch id: {"infused": points, "level"}} for the Branches you've started; the unused Lumin Amber
    count comes with the money (keyed by the amber's item id)"""
    trees = lua.synced_children(proc, lua.table_ref(player.get("leylineTreeInfoMap")), 1)
    # A Branch you haven't infused yet has no points stored: that's 0
    return {tree: {"infused": _int(info.get("leylineTreePoint")) or 0, "level": _int(info.get("leylineTreeLevel")) or 0}
            for tree, info in trees.items()}


def _eggs(proc, player):
    """[{"slot", "egg": item id, "endAt"}] for the hatch slots holding an egg (empty list if none), or None
    while the game hasn't filled the slots in this copy"""
    slots = lua.synced_children(proc, lua.table_ref(player.get("hatchSlotMap")), 1)
    if not slots:
        return None
    eggs = []
    for slot, fields in slots.items():
        egg = _int(lua.synced_fields(proc, lua.table_ref(fields.get("item"))).get("id"))
        if egg:
            eggs.append({"slot": slot, "egg": egg, "endAt": _number(fields.get("endTs"))})
    return sorted(eggs, key=lambda e: e["slot"])


def collect(proc):
    """{"name", "level", "exp", "rank", "headIcon", "headFrame", "serverArea", "dayReset", "weekReset",
        "primegy": {"value", "max", "nextAt"}, "daily": {"score", "tasks"}, "eggs": [...], "branches": {...},
        "talents": {talent id: level}, "money": {item id: count}, "interacted": [placed object id]}; times are
        Unix seconds"""
    player = handbook.live_player(proc)
    name = player.get("playerName")
    if not isinstance(name, str) or not name:
        return {}
    value, limit = _energy(proc, player, "commonEnergyNums"), _energy(proc, player, "energyAutoLimit")
    money = lua.synced_fields(proc, lua.table_ref(player.get("commonMoneyNums")))
    return {"name": name, "level": _int(player.get("level")), "exp": _int(player.get("exp")),
            "rank": _int(player.get("starTitle")),
            "headIcon": _int(player.get("headIcon")), "headFrame": _int(player.get("headFrame")),
            "serverArea": _int(player.get("serverArea")),
            "dayReset": _number(player.get("lastDayUpdateTs")), "weekReset": _number(player.get("lastWeekUpdateTs")),
            "primegy": {"value": int(value) if value is not None else None, "max": int(limit) if limit else None,
                        "nextAt": _energy(proc, player, "commonEnergyTimes")},
            "daily": handbook.newest(proc, lambda fields: _daily_activity(proc, fields)),
            "eggs": handbook.newest(proc, lambda fields: _eggs(proc, fields)),   # None until known
            "branches": _branches(proc, player),
            "talents": {talent: _int(fields.get("lv")) for talent, fields in
                        lua.synced_children(proc, lua.table_ref(player.get("skillNodeMap")), 1).items()},
            "money": {item: int(count) for item, count in money.items() if isinstance(item, int) and isinstance(count, float)},
            "interacted": sorted(int(k) for k in lua.synced_fields(proc, lua.table_ref(player.get("interactRecord")))
                                 if isinstance(k, (int, float)) and not isinstance(k, bool))}
