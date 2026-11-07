# The minimap's own idea of where the player is, in map pixels, read from its Lua component.
# MiniMapV2UIComponent keeps playerPosXInMap / playerPosYInMap (UI space, Y grows upwards) every frame.
# Paired with the world position this calibrates world -> map exactly, with no user input.
import struct

from . import lua

KEY_X, KEY_Y = "playerPosXInMap", "playerPosYInMap"
SAME_TABLE_BYTES = 0x4000   # both keys live in the same node array


class MinimapReader:
    def __init__(self, proc):
        self.proc = proc
        self.tags = {}
        self.nodes = None   # (node of X, node of Y)

    def located(self):
        return self.nodes is not None and all(
            self.proc.u64(node + 8) == self.tags[key] for key, node in zip((KEY_X, KEY_Y), self.nodes))

    def locate(self):
        """Slow (scans the Lua heap): find the minimap's two fields."""
        strs = lua.find_strings(self.proc, [KEY_X, KEY_Y])
        if not strs[KEY_X] or not strs[KEY_Y]:
            self.nodes = None
            return False
        self.tags = {k: lua.tag(lua.STR, v[0]) for k, v in strs.items()}
        is_number = lambda n: lua.itype(self.proc.u64(n) or lua.tag(lua.NIL, 0)) < 0x1FFF2
        xs = [n for n in lua.tables_with_key(self.proc, strs[KEY_X]) if is_number(n)]
        ys = [n for n in lua.tables_with_key(self.proc, strs[KEY_Y]) if is_number(n)]
        pairs = [(x, y) for x in xs for y in ys if abs(x - y) < SAME_TABLE_BYTES]
        self.nodes = pairs[0] if pairs else None
        return self.nodes is not None

    def read(self):
        """[px, py] in map image pixels (Y down), or None."""
        if not self.nodes:
            return None
        raw = [self.proc.read(n, 16) for n in self.nodes]
        if any(r is None or len(r) < 16 for r in raw):
            return None
        vals = []
        for (key, tag), r in zip(((KEY_X, self.tags[KEY_X]), (KEY_Y, self.tags[KEY_Y])), raw):
            val, k = struct.unpack("<QQ", r)
            if k != tag or lua.itype(val) >= 0x1FFF2:
                self.nodes = None
                return None
            vals.append(struct.unpack("<d", struct.pack("<Q", val))[0])
        return [vals[0], -vals[1]]
