# Everything the game has loaded near you (roughly the surrounding hundred metres), read-only from the Lua heap:
#   pg.space._globalId2Entity      {entity id: entity}; players are keyed by their uid (digits only)
#   <entity>.__Properties__        clenUsrType (KIND_*), templateId, staticId (its placement), ...
#   <entity>.posRef / rotRef       position {1: x, 2: y, 3: z} and facing (a quaternion: 2·atan2(y, w))
import math

from . import lua, pg

KIND_PLAYER, KIND_PLAYER_PET, KIND_WILD = 1, 2, 3   # clenUsrType; 5+ are NPCs, chests, nests and other objects


def sub(proc, table, key):
    """The table under `key`, read one level deep ({} if there is none)."""
    ref = lua.table_ref(table.get(key))
    return (lua.read_table(proc, ref, depth=0) or {}) if ref else {}


def loaded(proc, wanted=lambda key: True):
    """{entity id: entity fields} for everything loaded near you whose id `wanted` accepts."""
    entities = sub(proc, sub(proc, pg.fields(proc), "space"), "_globalId2Entity")
    return {str(key): lua.read_table(proc, lua.table_ref(ref), depth=0) or {}
            for key, ref in entities.items() if wanted(str(key)) and lua.table_ref(ref)}


def pose(proc, entity):
    """{"x", "y", "z", "heading"} of a loaded entity (heading in degrees, like our own), or None."""
    pos = sub(proc, entity, "posRef")
    if not all(isinstance(pos.get(i), float) for i in (1, 2, 3)):
        return None
    rot = sub(proc, entity, "rotRef")
    heading = math.degrees(2 * math.atan2(rot[2], rot[4])) if isinstance(rot.get(2), float) and isinstance(rot.get(4), float) else None
    return {"x": pos[1], "y": pos[2], "z": pos[3], "heading": heading}
