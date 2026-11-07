# Your Aniimo handbook, read-only from the game's Lua heap (ClientPetHandbookComponent):
#   petHandbookMap                   {petPrototypeId: {stateMask, ...}}  seen / caught per form (STATE_* bits)
#   blockCatchedPetMap               {blockId: {petPrototypeId: {isCatched, weatherId}}}  caught in that small
#                                    area: the game's area completion is this count over the area's petList
#   activeDistributionPetTemplateId  the Aniimo whose spawn areas the map is showing ("tracking")
#   starTitle, level                 on your property table (the one holding your petHandbookMap); the
#                                    star title drives the level ranges the map shows for areas
# Synced objects keep their fields under "_properties" next to back references (_owner, _root) that lead
# all over the heap, so the reader walks one level at a time instead of reading deep.
# Other players' handbooks (and stale snapshots) are on the heap too. Each copy hangs off its entity's
# property table (_owner); yours has the most properties (your private fields are only synced to you), so
# the copy whose owner is biggest is yours.
from . import lua

KEY_HANDBOOK, KEY_BLOCKS, KEY_TRACKED = "petHandbookMap", "blockCatchedPetMap", "activeDistributionPetTemplateId"
STATE_KNOWN, STATE_CAUGHT = 1, 2      # PET_HBMSK_KNOWN / PET_HBMSK_CATCHED
MIN_ID = 10000                        # Aniimo and area ids; smaller keys are type descriptors, not data
_player = lua.Holder(lambda fields: isinstance(fields.get("starTitle"), float) and KEY_HANDBOOK in fields)


def _mine(proc, gcstr_addrs):
    """The copy stored under this key whose owner entity has the most properties (yours), or None."""
    best, best_size = None, -1
    for node in lua.tables_with_key(proc, gcstr_addrs):
        raw = proc.u64(node)
        if raw is None or lua.itype(raw) != lua.TAB:
            continue
        copy = raw & lua.PTR_MASK
        owner = lua.table_ref((lua.read_table(proc, copy, depth=0) or {}).get("_owner"))
        size = len(lua.read_table(proc, owner, depth=0) or {}) if owner else 0
        if size > best_size:
            best, best_size = copy, size
    return best


def collect(proc):
    """{"states": {protoId: stateMask}, "blockCaught": {blockId: [protoId]}, "tracked": protoId or None,
        "starTitle": n, "level": n}"""
    strs = lua.find_strings(proc, [KEY_HANDBOOK, KEY_BLOCKS, KEY_TRACKED])
    mine = _mine(proc, strs[KEY_HANDBOOK])
    handbook = lua.synced_children(proc, mine, MIN_ID)
    states = {proto: int(entry["stateMask"]) for proto, entry in handbook.items() if isinstance(entry.get("stateMask"), float)}
    block_caught = {}
    for block, pets in lua.synced_fields(proc, _mine(proc, strs[KEY_BLOCKS])).items():
        if isinstance(block, int) and block >= MIN_ID and lua.table_ref(pets):
            caught = lua.synced_children(proc, lua.table_ref(pets), MIN_ID)
            block_caught[block] = sorted(p for p, entry in caught.items() if entry.get("isCatched") is True)
    tracked = None
    for node in lua.tables_with_key(proc, strs[KEY_TRACKED]):
        raw = proc.u64(node)
        value = lua.value(proc, raw, 0) if raw is not None else None
        if isinstance(value, float) and value > 0:
            tracked = int(value)
    own_nodes = [n for n in lua.tables_with_key(proc, strs[KEY_HANDBOOK]) if mine and ((proc.u64(n) or 0) & lua.PTR_MASK) == mine]
    player = _player.fields(proc, own_nodes)
    return {"states": states, "blockCaught": block_caught, "tracked": tracked,
            "starTitle": int(player["starTitle"]) if "starTitle" in player else None,
            "level": int(player["level"]) if isinstance(player.get("level"), float) else None}
