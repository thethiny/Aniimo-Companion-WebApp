# Pickups standing near you right now (chests, Lumin Amber, world eggs...), read-only from the loaded entities:
# their staticId is their mark id on the map, and status 0 means not opened yet. Your pickup record
# (profile.py, interactRecord) keeps ids of things that have since come back; one standing here is available.
from . import entities, pg

KINDS = (6.0, 8.0)   # chests and pickups, other placed objects
NOT_OPENED = 0.0


def collect(proc):
    """{"inGame": True, "standing": [static id]} of the unopened pickups loaded near you; {} while the game
    isn't loaded."""
    if not pg.fields(proc):
        return {}
    standing = []
    for entity in entities.loaded(proc, lambda key: not key.isdigit()).values():
        props = entities.sub(proc, entity, "__Properties__")
        static = props.get("staticId")
        if props.get("clenUsrType") in KINDS and isinstance(static, float) and static and props.get("status", NOT_OPENED) == NOT_OPENED:
            standing.append(int(static))
    return {"inGame": True, "standing": standing}
