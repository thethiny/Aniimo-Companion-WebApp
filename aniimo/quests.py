# The quests the HUD tracks, read-only from your live property table (handbook.live_player):
#   curTraceStoryQuest / curTraceSecondQuest / curTraceQuest   tracked Lore / Quests / Journey quest
#                                                              (0 or -1 when that tab tracks nothing)
#   acceptedQuestMap   {questId: {state, objectives: {objectiveId: {currentCnt, isComplete}}}}: every
#                      quest and quest step you have going; which steps belong to a tracked quest, and
#                      their text, comes from the config (mapdata "quests")
from . import handbook, lua

TRACKED = {"lore": "curTraceStoryQuest", "quests": "curTraceSecondQuest", "journey": "curTraceQuest"}
IN_PROGRESS = 2   # quest state


def _active(proc, player):
    """{questId: {objectiveId: count}} for the quests in progress."""
    active = {}
    for quest, ref in lua.synced_fields(proc, lua.table_ref(player.get("acceptedQuestMap"))).items():
        fields = lua.synced_fields(proc, lua.table_ref(ref)) if isinstance(quest, int) else {}
        if fields.get("state") != IN_PROGRESS:
            continue
        objectives = lua.synced_fields(proc, lua.table_ref(fields.get("objectives")))
        active[quest] = {objective: int(count) for objective, count in (
            (o, lua.synced_fields(proc, lua.table_ref(ref)).get("currentCnt")) for o, ref in objectives.items()
            if isinstance(o, int)) if isinstance(count, float)}
    return active


def collect(proc):
    """{"tracked": {"lore" / "quests" / "journey": questId or None}, "active": {questId: {objectiveId: count}}}"""
    player = handbook.live_player(proc)
    if not player:
        return {}
    tracked = {tab: int(player[key]) if isinstance(player.get(key), float) and player[key] > 0 else None
               for tab, key in TRACKED.items()}
    return {"tracked": tracked, "active": _active(proc, player)}
