# The minimap's own idea of where the player is, in map pixels, read from its Lua component.
# MiniMapV2UIComponent keeps playerPosXInMap / playerPosYInMap (UI space, Y grows upwards) every frame.
# Paired with the world position this calibrates world -> map exactly, with no user input.
#
# When a Lua table grows, its fields move to a new node array and the old array stays in memory with
# the same keys but frozen values. So the fields are only trusted inside a live table, and the reader
# re-locates as soon as that table's node array moves.
import math
import struct

from . import lua

KEY_X, KEY_Y = "playerPosXInMap", "playerPosYInMap"
SAME_TABLE_BYTES = 0x4000     # both keys live in the same node array
OWNER_SEARCH = 8192           # node index range to search for the owning table


class MinimapReader:
    def __init__(self, proc):
        self.proc = proc
        self.tags = {}
        self.nodes = None        # (node of X, node of Y)
        self.table = None        # the live GCtab holding them
        self.node_array = None   # its node array when located

    def located(self):
        if not self.nodes or not self.table:
            return False
        if self.proc.u64(self.table + lua.TAB_NODE) != self.node_array:
            return False   # the table grew: its fields moved
        return all(self.proc.u64(node + 8) == self.tags[key] for key, node in zip((KEY_X, KEY_Y), self.nodes))

    def locate(self):
        """Slow (scans the Lua heap): find the minimap's two fields in a live table."""
        self.nodes = self.table = None
        strs = lua.find_strings(self.proc, [KEY_X, KEY_Y])
        if not strs[KEY_X] or not strs[KEY_Y]:
            return False
        self.tags = {k: lua.tag(lua.STR, v[0]) for k, v in strs.items()}
        xs = lua.tables_with_key(self.proc, strs[KEY_X])
        ys = lua.tables_with_key(self.proc, strs[KEY_Y])
        pairs = [(x, y) for x in xs for y in ys if abs(x - y) < SAME_TABLE_BYTES]
        owners = lua.owner_tables(self.proc, [x for x, _ in pairs], max_index=OWNER_SEARCH)
        for x, y in pairs:
            table = owners.get(x)
            values = [self._number(n) for n in (x, y)]
            if table and all(v is not None and math.isfinite(v) for v in values):
                self.nodes, self.table = (x, y), table
                self.node_array = self.proc.u64(table + lua.TAB_NODE)
                return True
        return False

    def _number(self, node):
        raw = self.proc.u64(node)
        if raw is None or lua.itype(raw) >= 0x1FFF2:
            return None
        return struct.unpack("<d", struct.pack("<Q", raw))[0]

    def read(self):
        """[px, py] in map image pixels (Y down), or None."""
        if not self.located():
            return None
        x, y = (self._number(n) for n in self.nodes)
        if x is None or y is None:
            return None
        return [x, -y]
