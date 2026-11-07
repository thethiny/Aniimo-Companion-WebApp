# Your party, read-only from the game's Lua heap:
#   pg.space._globalId2Entity      {entity id: entity} for everything loaded near you; players are keyed by their
#                                  uid (digits only), with __Properties__.playerName, posRef {1: x, 2: y, 3: z}
#                                  and rotRef (a quaternion: facing = 2·atan2(y, w))
#   <your entity>.teamInfo         leaderUid, membersInfo {uid: {playerName, level, starTitle, headIcon,
#                                  headFrame, online, enterTeamTime, ...}}
# The game only loads players near you: a member far away is still listed, without a position.
import math

from . import handbook, lua, pg


def _sub(proc, table, key):
    ref = lua.table_ref(table.get(key))
    return (lua.read_table(proc, ref, depth=0) or {}) if ref else {}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value):
    return int(value) if _number(value) is not None else None


def _players(proc):
    """{uid: entity fields} for the players loaded near you (yourself included)."""
    space = _sub(proc, pg.fields(proc), "space")
    entities = _sub(proc, space, "_globalId2Entity")
    players = {}
    for key, ref in entities.items():
        if str(key).isdigit() and lua.table_ref(ref):
            players[str(key)] = lua.read_table(proc, lua.table_ref(ref), depth=0) or {}
    return players


def _pose(proc, entity):
    """{"x", "y", "z", "heading"} of a loaded player (heading in degrees, like our own), or None."""
    pos = _sub(proc, entity, "posRef")
    if not all(isinstance(pos.get(i), float) for i in (1, 2, 3)):
        return None
    rot = _sub(proc, entity, "rotRef")
    heading = math.degrees(2 * math.atan2(rot[2], rot[4])) if isinstance(rot.get(2), float) and isinstance(rot.get(4), float) else None
    return {"x": pos[1], "y": pos[2], "z": pos[3], "heading": heading}


def collect(proc):
    """{"inGame": True, "leader": uid, "members": [{uid, order, self, leader, name, level, rank, headIcon,
    headFrame, online, pose}]} with members in party order (the leader is 1P, then by when they joined);
    members [] when you're not in a party. {} while the game isn't loaded."""
    name = handbook.live_player(proc).get("playerName")
    if not isinstance(name, str) or not name:
        return {}
    players = _players(proc)
    me = next((uid for uid, entity in players.items() if _sub(proc, entity, "__Properties__").get("playerName") == name), None)
    team = _sub(proc, players[me], "teamInfo") if me else {}
    leader = team.get("leaderUid") or None
    infos = {str(uid): lua.read_table(proc, lua.table_ref(ref), depth=0) or {}
             for uid, ref in _sub(proc, team, "membersInfo").items() if lua.table_ref(ref)}
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
                        "pose": _pose(proc, players[uid]) if uid in players else None})
    return {"inGame": True, "leader": leader, "members": members}
