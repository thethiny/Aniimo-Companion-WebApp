# Friends you've opened the info card of, read-only from the game's Lua heap. The game only holds a friend's
# affinity while their card has been opened (it asks the server then), in the card's own table:
#   {friendshipValue, playerInfo: {uid, caughtNum, shinyCaughtNum, fashionScore, ...}}
# Only those numbers are taken: the card also holds private details (their address) that must never leave it.
# The game drops a card some time after it closes, so what was seen is remembered here (and in the store).
import struct

from . import lua

VALUE_KEY, INFO_KEY = "friendshipValue", "playerInfo"
CARD_FIELDS = {"caughtNum": "caught", "shinyCaughtNum": "sparkling", "rainbowCaughtNum": "prismana",
               "fashionScore": "style"}   # Collection Progress on the card (player_collect_data variables)
NEIGHBOURS = 32   # a card has few keys: playerInfo sits within this many nodes of friendshipValue

_seen = {}   # uid -> what the card showed last


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _locate(proc):
    return lua.find_strings(proc, [VALUE_KEY, INFO_KEY])


def _still_valid(proc, found):
    return bool(found) and all(lua.read_str(proc, a) == key for key, addrs in found.items() for a in addrs)


_strings = lua.Located(_locate, _still_valid)   # the key strings live as long as the game: found once


def _card(proc, node, info_keys):
    """(friendshipValue, playerInfo fields) of the card whose node array holds `node`, or None. Reads the
    nodes around it instead of searching for the table (that search takes over a minute)."""
    raw = proc.read(node, 8)
    value = _int(lua.value(proc, struct.unpack("<Q", raw)[0], 0)) if raw and len(raw) == 8 else None
    if value is None:
        return None
    for i in range(-NEIGHBOURS, NEIGHBOURS + 1):
        pair = proc.read(node + i * lua.NODE_SIZE, 16)
        if not pair or len(pair) < 16:
            continue
        val, key = struct.unpack("<QQ", pair)
        if key in info_keys and lua.itype(val) == lua.TAB:
            return value, lua.read_table(proc, val & lua.PTR_MASK, depth=0) or {}
    return None


def collect(proc):
    """{"friends": {uid: {affinity, caught, sparkling, prismana, style}}} for every friend whose card has
    been opened since the server started (the server's store keeps the ones from before)."""
    strings = _strings.get(proc) or {}
    info_keys = {lua.tag(lua.STR, a) for a in strings.get(INFO_KEY, [])}
    for node in lua.tables_with_key(proc, strings.get(VALUE_KEY, [])):
        card = _card(proc, node, info_keys)
        if not card:
            continue
        value, info = card
        uid = info.get("uid")
        if isinstance(uid, str) and uid.isdigit():
            _seen[uid] = dict({name: _int(info.get(key)) for key, name in CARD_FIELDS.items()}, affinity=value)
    return {"friends": dict(_seen)}
