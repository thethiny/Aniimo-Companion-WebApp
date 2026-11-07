# Area names, read-only from the game's Lua heap whenever the game has them loaded.
# The map UI keeps:
#   areaIndexTable           {smallAreaId (block id): mask index} for the scene the map shows
#   {blockId, areaName}      one record per labelled small area ("Beast Fang Ridge")
#   {areaId, areaName, ...}  one record per region ("Breezy Plains"), with an `order`
# Names are only resident while the map UI has them, so callers merge results over time.
import re

from . import lua

KEY_INDEX, KEY_NAME = "areaIndexTable", "areaName"
# Title-case words with small connectors; freed strings reused as random text don't match
NAME_OK = re.compile(r"^[A-Z][a-z']+(?: (?:of|the|and|[A-Z][a-z']+))*$")


def _valid(name):
    return isinstance(name, str) and len(name) <= 40 and bool(NAME_OK.match(name))


def collect(proc):
    """{"indexToBlock": {index: blockId}, "blocks": {blockId: name}, "regions": {areaId: {name, order}}}"""
    strs = lua.find_strings(proc, [KEY_INDEX, KEY_NAME])
    result = {"indexToBlock": {}, "blocks": {}, "regions": {}}
    for node in lua.tables_with_key(proc, strs[KEY_INDEX]):
        raw = proc.u64(node)
        if raw is not None and lua.itype(raw) == lua.TAB:
            table = lua.read_table(proc, raw & lua.PTR_MASK, depth=0) or {}
            for block, index in table.items():
                if isinstance(block, int) and isinstance(index, float) and index.is_integer():
                    result["indexToBlock"][int(index)] = block
    # Only nodes that hold a plausible name are worth the (slow) owner lookup
    nodes = [n for n in lua.tables_with_key(proc, strs[KEY_NAME]) if _valid(lua.value(proc, proc.u64(n) or 0, 0))]
    for table in set(lua.owner_tables(proc, nodes, max_index=64).values()):
        record = lua.read_table(proc, table, depth=0) or {}
        name = record.get(KEY_NAME)
        if not _valid(name):
            continue
        if isinstance(record.get("blockId"), float):
            result["blocks"][int(record["blockId"])] = name
        elif isinstance(record.get("areaId"), float):
            result["regions"][int(record["areaId"])] = {"name": name, "order": record.get("order", record["areaId"])}
    return result
