# Your Aniimo handbook, read-only from the game's Lua heap (ClientPetHandbookComponent):
#   petHandbookMap                   {petPrototypeId: {stateMask, ...}}  seen / caught per form (STATE_* bits)
#   blockCatchedPetMap               {blockId: {petPrototypeId: {isCatched, weatherId}}}  caught in that small
#                                    area: the game's area completion is this count over the area's petList
#   starTitle, level                 the star title drives the level ranges the map shows for areas
#   activeDistributionPetTemplateId  on the map system: the Aniimo whose spawn areas the map shows ("tracking")
# The first three live on your property table. Other players' copies (and stale snapshots) are on the heap
# too; each hangs off its entity (_owner) and yours has the most properties (your private fields are only
# synced to you). Synced objects keep their fields under "_properties" next to back references that lead
# all over the heap, so the reader walks one level at a time instead of reading deep.
# Finding these tables takes a heap search (~40 s); after that they're read directly (well under a second).
from . import lua

KEY_HANDBOOK, KEY_BLOCKS, KEY_TRACKED = "petHandbookMap", "blockCatchedPetMap", "activeDistributionPetTemplateId"
STATE_KNOWN, STATE_CAUGHT = 1, 2      # PET_HBMSK_KNOWN / PET_HBMSK_CATCHED
MIN_ID = 10000                        # Aniimo and area ids; smaller keys are type descriptors, not data
MAP_SYSTEM_FIELD = "sceneMarkPointData"   # marks the map system among tables that have a tracked Aniimo


def _is_player(fields):
    return isinstance(fields.get("starTitle"), float) and lua.table_ref(fields.get(KEY_HANDBOOK)) is not None


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


def _holder(proc, nodes, accept):
    """The table holding one of these key nodes whose fields pass `accept`, or None."""
    for table in set(lua.owner_tables(proc, nodes, max_index=1024).values()):
        if accept(lua.read_table(proc, table, depth=0) or {}):
            return table
    return None


def _locate(proc):
    """{"player": your property table, "mapSystem": the map system table or None}"""
    strs = lua.find_strings(proc, [KEY_HANDBOOK, KEY_TRACKED])
    mine = _mine(proc, strs[KEY_HANDBOOK])
    own_nodes = [n for n in lua.tables_with_key(proc, strs[KEY_HANDBOOK]) if mine and ((proc.u64(n) or 0) & lua.PTR_MASK) == mine]
    return {"player": _holder(proc, own_nodes, _is_player),
            "mapSystem": _holder(proc, lua.tables_with_key(proc, strs[KEY_TRACKED]), lambda f: MAP_SYSTEM_FIELD in f)}


def _still_valid(proc, found):
    return bool(found["player"]) and _is_player(lua.read_table(proc, found["player"], depth=0) or {})


_where = lua.Located(_locate, _still_valid)


def collect(proc):
    """{"states": {protoId: stateMask}, "blockCaught": {blockId: [protoId]}, "tracked": protoId or None,
        "starTitle": n, "level": n}"""
    found = _where.get(proc)
    player = (lua.read_table(proc, found["player"], depth=0) or {}) if found["player"] else {}
    handbook = lua.synced_children(proc, lua.table_ref(player.get(KEY_HANDBOOK)), MIN_ID)
    states = {proto: int(entry["stateMask"]) for proto, entry in handbook.items() if isinstance(entry.get("stateMask"), float)}
    block_caught = {}
    for block, pets in lua.synced_fields(proc, lua.table_ref(player.get(KEY_BLOCKS))).items():
        if isinstance(block, int) and block >= MIN_ID and lua.table_ref(pets):
            caught = lua.synced_children(proc, lua.table_ref(pets), MIN_ID)
            block_caught[block] = sorted(p for p, entry in caught.items() if entry.get("isCatched") is True)
    map_system = (lua.read_table(proc, found["mapSystem"], depth=0) or {}) if found["mapSystem"] else {}
    tracked = map_system.get(KEY_TRACKED)
    return {"states": states, "blockCaught": block_caught,
            "tracked": int(tracked) if isinstance(tracked, float) and tracked > 0 else None,
            "starTitle": int(player["starTitle"]) if isinstance(player.get("starTitle"), float) else None,
            "level": int(player["level"]) if isinstance(player.get("level"), float) else None}
