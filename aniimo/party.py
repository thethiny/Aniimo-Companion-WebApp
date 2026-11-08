# Your party, read-only from the game's Lua heap:
#   loaded players (entities.py)   keyed by their uid (digits only), with __Properties__.playerName and a pose
#   <your entity>.teamInfo         leaderUid, membersInfo {uid: {playerName, level, starTitle, headIcon,
#                                  headFrame, online, enterTeamTime, playerCp (combat power),
#                                  petInfoList {i: {templateId, level, cp, label}} (their team),
#                                  exploreAbilityIds {i: ability id}, lineInfo.sceneId (their map), ...}}
#   companions                     loaded player Aniimo (entities.KIND_PLAYER_PET) out with a member:
#                                  __Properties__.authorityId is the member's uid, isSummon while out
# The game only loads players near you: a member far away is still listed, without a position.
from . import collection, entities, handbook, lua

SUB_SCENE = 100000   # seamless sub-area ids (3000001) belong to the map whose id is theirs // 1000 (pg.SUB_SCENE)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value):
    return int(value) if _number(value) is not None else None


def _tags(label):
    label = _int(label) or 0
    return [name for bit, name in collection.LABELS.items() if label & bit]


def _table(proc, value, depth=0):
    ref = lua.table_ref(value)
    return (lua.read_table(proc, ref, depth=depth) or {}) if ref else {}


def _scene(info, proc):
    scene = _int(_table(proc, info.get("lineInfo")).get("sceneId"))
    return (scene // 1000 if scene >= SUB_SCENE else scene) if scene else None


def _team(proc, info):
    """Their team in slot order: [{form, level, cp, tags}]."""
    slots = _table(proc, info.get("petInfoList"))
    team = []
    for slot in sorted(slots, key=lambda k: _number(k) or 0):
        pet = _table(proc, slots[slot])
        if _int(pet.get("templateId")):
            team.append({"form": _int(pet["templateId"]), "level": _int(pet.get("level")), "cp": _int(pet.get("cp")),
                         "tags": _tags(pet.get("label"))})
    return team


def _companions(proc):
    """{owner uid: [{form, level, gender, tags, pose}]} for the player Aniimo out near you."""
    found = {}
    for entity in entities.loaded(proc, lambda key: not key.isdigit()).values():
        props = entities.sub(proc, entity, "__Properties__")
        if props.get("clenUsrType") != entities.KIND_PLAYER_PET or not props.get("isSummon"):
            continue
        found.setdefault(str(props.get("authorityId")), []).append(
            {"form": _int(props.get("petPrototypeId")), "level": _int(props.get("level")),
             "gender": _int(props.get("gender")), "tags": _tags(props.get("label")), "pose": entities.pose(proc, entity)})
    return found


def collect(proc):
    """{"inGame": True, "leader": uid, "members": [{uid, order, self, leader, name, level, rank, headIcon,
    headFrame, online, pose, cp, team, abilities, scene, companions}]} with members in party order (the leader
    is 1P, then by when they joined); members [] when you're not in a party. {} while the game isn't loaded."""
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
    companions = _companions(proc)
    members = []
    for index, uid in enumerate(order, 1):
        info = infos[uid]
        members.append({"uid": uid, "order": index, "self": uid == me, "leader": uid == leader,
                        "name": info.get("playerName"), "level": _int(info.get("level")),
                        "rank": _int(info.get("starTitle")), "headIcon": _int(info.get("headIcon")),
                        "headFrame": _int(info.get("headFrame")), "online": info.get("online", True) is not False,
                        "pose": entities.pose(proc, players[uid]) if uid in players else None,
                        "cp": _int(info.get("playerCp")), "team": _team(proc, info),
                        "abilities": [_int(a) for _, a in sorted(_table(proc, info.get("exploreAbilityIds")).items(),
                                                                 key=lambda kv: _number(kv[0]) or 0) if _int(a)],
                        "scene": _scene(info, proc), "companions": companions.get(uid, [])})
    return {"inGame": True, "leader": leader, "members": members}
