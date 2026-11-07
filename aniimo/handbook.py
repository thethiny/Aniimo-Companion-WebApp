# Your Aniimo handbook, read-only from the game's Lua heap (ClientPetHandbookComponent):
#   petHandbookMap                   {petPrototypeId: {stateMask, ...}}  seen / caught per form (STATE_* bits)
#   blockCatchedPetMap               {blockId: {petPrototypeId: {isCatched, weatherId}}}  caught in that small
#                                    area: the game's area completion is this count over the area's petList
#   starTitle, level                 the star title drives the level ranges the map shows for areas
#   activeDistributionPetTemplateId  on the map system: the Aniimo whose spawn areas the map shows ("tracking")
# The first three live on your property table, reached through pg (see pg.py). Every respawn (teleport,
# loading screen) builds a new copy of it and the old ones stay on the heap for a while, frozen at that
# moment. Some fields are filled only once their screen is opened in game (hatching): for those, newest()
# falls back to the old copy with the most progress (level, then XP) that has them. Synced objects keep
# their fields under "_properties" next to back references that lead all over the heap, so the reader
# walks one level at a time instead of reading deep.
# The map system and the old copies take a heap search to find (minutes); everything else is direct. The
# old copies are searched by find_copies() on a lane of its own, so live reads never wait for it.
from . import lua, pg

KEY_HANDBOOK, KEY_BLOCKS, KEY_TRACKED = "petHandbookMap", "blockCatchedPetMap", "activeDistributionPetTemplateId"
STATE_KNOWN, STATE_CAUGHT = 1, 2      # PET_HBMSK_KNOWN / PET_HBMSK_CATCHED
MIN_ID = 10000                        # Aniimo and area ids; smaller keys are type descriptors, not data
MAP_SYSTEM_FIELD = "sceneMarkPointData"   # marks the map system among tables that have a tracked Aniimo


def _is_player(fields):
    return isinstance(fields.get("starTitle"), float) and lua.table_ref(fields.get(KEY_HANDBOOK)) is not None


def _progress(fields):
    """Sort key of a player copy: the live one has the most progress, the newest spawn breaks ties."""
    return tuple(fields.get(k) if isinstance(fields.get(k), float) else -1.0 for k in ("level", "exp", "bornTime"))


def _player_copies(proc, nodes):
    """Your property tables among the copies holding these key nodes, live one first."""
    copies = []
    for table in set(lua.owner_tables(proc, nodes, max_index=1024).values()):
        fields = lua.read_table(proc, table, depth=0) or {}
        if _is_player(fields):
            copies.append((_progress(fields), table))
    return [table for _, table in sorted(copies, reverse=True)]


def live_player(proc):
    """Your live property table's fields, or {} while the game isn't loaded."""
    table = pg.me(proc)
    fields = (lua.read_table(proc, table, depth=0) or {}) if table else {}
    return fields if _is_player(fields) else {}


def newest(proc, read):
    """read(player fields) from the live copy, else from the old copy with the most progress where it isn't
    None (fields the game fills only once you open their screen; read returns None while they aren't)."""
    value = read(live_player(proc))
    if value is not None:
        return value
    copies = [lua.read_table(proc, table, depth=0) or {} for table in _copies]
    for fields in sorted((f for f in copies if _is_player(f)), key=_progress, reverse=True):
        value = read(fields)
        if value is not None:
            return value
    return None


def _holder(proc, nodes, accept):
    """The table holding one of these key nodes whose fields pass `accept`, or None."""
    for table in set(lua.owner_tables(proc, nodes, max_index=1024).values()):
        if accept(lua.read_table(proc, table, depth=0) or {}):
            return table
    return None


_copies = []   # old copies of your property table, as last found by find_copies()


def find_copies(proc):
    """Searches the heap for old copies of your property table for newest() (minutes; run it off the live
    lane). Returns {"copies": [...]} for the log."""
    global _copies
    strs = lua.find_strings(proc, [KEY_HANDBOOK])
    _copies = _player_copies(proc, lua.tables_with_key(proc, strs[KEY_HANDBOOK]))
    return {"copies": _copies}


def _locate_map_system(proc):
    strs = lua.find_strings(proc, [KEY_TRACKED])
    return _holder(proc, lua.tables_with_key(proc, strs[KEY_TRACKED]), lambda f: MAP_SYSTEM_FIELD in f)


_map_system = lua.Located(_locate_map_system,
                          lambda proc, found: bool(found) and MAP_SYSTEM_FIELD in (lua.read_table(proc, found, depth=0) or {}))


def collect(proc):
    """{"states": {protoId: stateMask}, "blockCaught": {blockId: [protoId]}, "tracked": protoId or None,
        "starTitle": n, "level": n}"""
    player = live_player(proc)
    handbook = lua.synced_children(proc, lua.table_ref(player.get(KEY_HANDBOOK)), MIN_ID)
    states = {proto: int(entry["stateMask"]) for proto, entry in handbook.items() if isinstance(entry.get("stateMask"), float)}
    block_caught = {}
    for block, pets in lua.synced_fields(proc, lua.table_ref(player.get(KEY_BLOCKS))).items():
        if isinstance(block, int) and block >= MIN_ID and lua.table_ref(pets):
            caught = lua.synced_children(proc, lua.table_ref(pets), MIN_ID)
            block_caught[block] = sorted(p for p, entry in caught.items() if entry.get("isCatched") is True)
    found = _map_system.get(proc)
    map_system = (lua.read_table(proc, found, depth=0) or {}) if found else {}
    tracked = map_system.get(KEY_TRACKED)
    return {"states": states, "blockCaught": block_caught,
            "tracked": int(tracked) if isinstance(tracked, float) and tracked > 0 else None,
            "starTitle": int(player["starTitle"]) if isinstance(player.get("starTitle"), float) else None,
            "level": int(player["level"]) if isinstance(player.get("level"), float) else None}
