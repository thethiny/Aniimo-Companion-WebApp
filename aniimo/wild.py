# Wild Aniimo loaded near you (the game only loads the ones around you, roughly a hundred metres), read-only:
#   __Properties__ of a wild entity (entities.KIND_WILD):
#     petPrototypeId        the form
#     level, gender, label  label: the same tag bits as owned Aniimo (collection.LABELS); they stay on capture
# Rarity isn't sent: a wild Aniimo's stats (basePropertyList) are boosted, and the ones it keeps are set when
# you catch it (often lower), so they don't tell its rarity.
from . import collection, entities, pg


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def collect(proc):
    """{"inGame": True, "wild": [{id, form, level, gender, tags, x, y, z, heading}]} for the wild Aniimo loaded
    near you; {} while the game isn't loaded."""
    if not pg.fields(proc):
        return {}
    found = []
    for key, entity in entities.loaded(proc, lambda key: not key.isdigit()).items():
        props = entities.sub(proc, entity, "__Properties__")
        form = _int(props.get("petPrototypeId"))
        if props.get("clenUsrType") != entities.KIND_WILD or not form:
            continue
        where = entities.pose(proc, entity)
        if not where:
            continue
        label = _int(props.get("label")) or 0
        found.append(dict(where, id=key, form=form, level=_int(props.get("level")), gender=_int(props.get("gender")),
                          tags=[n for bit, n in collection.LABELS.items() if label & bit]))
    return {"inGame": True, "wild": found}
