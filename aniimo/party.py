# Your party, read-only from the game's Lua heap:
#   loaded players (entities.py)   keyed by their uid (digits only), with __Properties__.playerName and a pose
#   <your entity>.teamInfo         leaderUid, membersInfo {uid: {playerName, level, starTitle, headIcon,
#                                  headFrame, online, enterTeamTime, ...}}
# The game only loads players near you: a member far away is still listed, without a position.
from . import entities, handbook, lua


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value):
    return int(value) if _number(value) is not None else None


def collect(proc):
    """{"inGame": True, "leader": uid, "members": [{uid, order, self, leader, name, level, rank, headIcon,
    headFrame, online, pose}]} with members in party order (the leader is 1P, then by when they joined);
    members [] when you're not in a party. {} while the game isn't loaded."""
    name = handbook.live_player(proc).get("playerName")
    if not isinstance(name, str) or not name:
        return {}
    players = entities.loaded(proc, str.isdigit)
    me = next((uid for uid, entity in players.items() if entities.sub(proc, entity, "__Properties__").get("playerName") == name), None)
    team = entities.sub(proc, players[me], "teamInfo") if me else {}
    leader = team.get("leaderUid") or None
    infos = {str(uid): lua.read_table(proc, lua.table_ref(ref), depth=0) or {}
             for uid, ref in entities.sub(proc, team, "membersInfo").items() if lua.table_ref(ref)}
    if not leader or len(infos) < 2:
        return {"inGame": True, "leader": None, "members": []}
    order = sorted(infos, key=lambda uid: (uid != leader, _number(infos[uid].get("enterTeamTime")) or 0))
    members = []
    for index, uid in enumerate(order, 1):
        info = infos[uid]
        members.append({"uid": uid, "order": index, "self": uid == me, "leader": uid == leader,
                        "name": info.get("playerName"), "level": _int(info.get("level")),
                        "rank": _int(info.get("starTitle")), "headIcon": _int(info.get("headIcon")),
                        "headFrame": _int(info.get("headFrame")), "online": info.get("online", True) is not False,
                        "pose": entities.pose(proc, players[uid]) if uid in players else None})
    return {"inGame": True, "leader": leader, "members": members}
