# The Aniimo you own right now (your storage), read-only from your live property table's `pets` map:
#   {pet id: {petPrototypeId, level, propertyScoreStage, nature, label, gender, ...}}
#   petPrototypeId       the form
#   propertyScoreStage   rarity (Innate Potential): 1 Common, 2 Good, 3 Elite, 4 Perfect
#   nature               personality (pet_nature_data)
#   label                tag bits: Sparkling, Alpha, Umbral (see LABELS)
# The game unpacks a pet only once something shows it; until then its fields sit packed (lua.synced_fields
# decodes those). The handbook (handbook.py) says what you've ever caught; this says what you still have.
from . import handbook, lua

LABELS = {1: "sparkling", 4: "alpha", 16: "umbral"}   # label bits, checked against owned Aniimo in game


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pet(fields):
    """One owned Aniimo as the page shows it, or None if it doesn't read as one."""
    form = _int(fields.get("petPrototypeId"))
    if not form:
        return None
    label = _int(fields.get("label")) or 0
    return {"form": form, "level": _int(fields.get("level")), "rarity": _int(fields.get("propertyScoreStage")),
            "personality": _int(fields.get("nature")), "gender": _int(fields.get("gender")),
            "tags": [name for bit, name in LABELS.items() if label & bit]}


def collect(proc):
    """{"owned": [{form, level, rarity, personality, gender, tags}]}: every Aniimo you own, or {} while the
    game isn't loaded."""
    player = handbook.live_player(proc)
    pets = lua.synced_fields(proc, lua.table_ref(player.get("pets"))) if player else {}
    owned = []
    for key, ref in pets.items():
        if isinstance(key, str) and not key.startswith("_") and lua.table_ref(ref):
            pet = _pet(lua.synced_fields(proc, lua.table_ref(ref)))
            if pet:
                owned.append(pet)
    return {"owned": owned} if owned else {}
