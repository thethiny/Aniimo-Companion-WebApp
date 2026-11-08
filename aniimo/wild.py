# Wild Aniimo loaded near you (the game only loads the ones around you, roughly a hundred metres), read-only:
#   __Properties__ of a wild entity (entities.KIND_WILD):
#     petPrototypeId        the form
#     level, gender, label  label: the same tag bits as owned Aniimo (collection.LABELS)
#     basePropertyList      six stats {indLv, ...}; rarity follows the sum of their indLv (RARITY_FROM_SUM)
# Rarity never changes for a spawned Aniimo, so it is read once per entity and remembered.
from . import collection, entities, lua, pg

# Lowest indLv sum for each rarity, as owned Aniimo show it (propertyScoreStage): 1 Common, 2 Good,
# 3 Elite, 4 Perfect. Measured on 300 owned Aniimo; the game's own rule isn't in its data files.
RARITY_FROM_SUM = ((32, 4), (24, 3), (16, 2), (0, 1))
STATS = 6
_rarity = {}   # entity id -> rarity, for the ones loaded now


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rarity_of(proc, props):
    stats = lua.synced_fields(proc, lua.table_ref(props.get("basePropertyList")))
    levels = [lua.synced_fields(proc, lua.table_ref(ref)).get("indLv") for ref in stats.values() if lua.table_ref(ref)]
    if len(levels) != STATS or not all(isinstance(v, float) for v in levels):
        return None
    total = sum(levels)
    return next(rarity for lowest, rarity in RARITY_FROM_SUM if total >= lowest)


def collect(proc):
    """{"inGame": True, "wild": [{id, form, level, gender, rarity, tags, x, y, z, heading}]} for the wild
    Aniimo loaded near you; {} while the game isn't loaded."""
    if not pg.fields(proc):
        return {}
    found, seen = [], set()
    for key, entity in entities.loaded(proc, lambda key: not key.isdigit()).items():
        props = entities.sub(proc, entity, "__Properties__")
        form = _int(props.get("petPrototypeId"))
        if props.get("clenUsrType") != entities.KIND_WILD or not form:
            continue
        where = entities.pose(proc, entity)
        if not where:
            continue
        seen.add(key)
        if _rarity.get(key) is None:
            _rarity[key] = _rarity_of(proc, props)
        label = _int(props.get("label")) or 0
        found.append(dict(where, id=key, form=form, level=_int(props.get("level")), gender=_int(props.get("gender")),
                          rarity=_rarity[key], tags=[n for bit, n in collection.LABELS.items() if label & bit]))
    for key in set(_rarity) - seen:   # gone (caught, defeated, or out of range)
        del _rarity[key]
    return {"inGame": True, "wild": found}
