# Reader for the game's packed config data (LuaScripts/Common/Data/pmdata.bin, decoded natively by xlua.dll)
# and its localized text (Data/I18N/NewTextMap_<lang>.json + Compress_<lang>.bin).
#
# pmdata.bin layout (reverse engineered; all references are absolute file offsets, little endian):
#   string pool       NUL-terminated UTF-8 strings from offset 0
#   key lists         u32, u8 width code, u32 count, u32 integer-key count, then the keys:
#                     u32 string refs when the integer-key count is 0, else numbers (see INT_WIDTH)
#   shapes            12 bytes: u32 key list, u32 type codes (1 byte per key), u32 offsets
#                     offsets: u8 width (1 = u8, 3 = u16, 5 = u32) then one offset per key
#   objects           u32 shape, then the fields at their offsets from the object start
#   Value codes: see FORMATS (5 is also used for text hashes, see Text). Code + 0x64: the value is shared
#   by every object of the shape and stored before its offsets table, at offsets + 1 + index - offset.
# The root object maps table names ("default_map_mark_data") to table objects keyed by row id.
import json
import os
import struct

LUA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extracted", "lua_bc", "xfs", "luascripts")
PMDATA = os.path.join(LUA, "Common", "Data", "pmdata.bin")
I18N = os.path.join(LUA, "Data", "I18N")

INT_WIDTH = {0: "d", 1: "B", 3: "H", 5: "I"}   # 0: doubles (negative or fractional keys)
# Value codes: struct format per code (strings and objects are u32 refs)
FORMATS = {0: "d", 1: "B", 2: "b", 3: "H", 4: "h", 5: "I", 6: "i", 7: "I", 8: "I", 9: "B"}
STRING, OBJECT, BOOL = 7, 8, 9
SHARED = 0x64   # code + 0x64: value shared by the whole shape

class PmData:
    def __init__(self, path=PMDATA):
        self.data = open(path, "rb").read()
        self.pool_end = self._pool_end()
        self.root = self._find_root()

    def _pool_end(self):
        i = 0
        while True:
            j = self.data.find(b"\0", i)
            try:
                self.data[i:j].decode("utf-8")
            except UnicodeDecodeError:
                return i
            i = j + 1

    def u32(self, at):
        return struct.unpack_from("<I", self.data, at)[0]

    def string(self, ref):
        if not 0 <= ref < self.pool_end or (ref and self.data[ref - 1] != 0):
            return None
        return self.data[ref:self.data.find(b"\0", ref)].decode("utf-8", "replace")

    def _refs(self, value):
        pattern, out = struct.pack("<I", value), []
        i = self.data.find(pattern, self.pool_end)
        while i != -1:
            out.append(i)
            i = self.data.find(pattern, i + 1)
        return out

    def _find_root(self):
        """The root is the object with the biggest key list that names a known table."""
        name = self.data.find(b"\0default_map_mark_data\0") + 1
        best = None
        for at in self._refs(name):
            # Walk back to the start of the key list that holds this reference
            start = at
            while self.u32(start - 4) and self.string(self.u32(start - 4)) is not None:
                start -= 4
            header = start - 13
            if self.data[header + 4] != 1:
                continue
            count = self.u32(header + 5)
            if best is None or count > best[0]:
                best = (count, header)
        shape = self._refs(best[1])[0]
        return self._refs(shape)[0]

    def keys(self, at):
        # u32 0 for string keys (else integer keys), u8 width code, u32 count, u32 count of integer keys
        width, count, int_count = self.data[at + 4], self.u32(at + 5), self.u32(at + 9)
        if not int_count:
            return [self.string(r) for r in struct.unpack_from(f"<{count}I", self.data, at + 13)]
        values = struct.unpack_from(f"<{count}{INT_WIDTH[width]}", self.data, at + 13)
        return [int(v) if isinstance(v, float) and v.is_integer() else v for v in values]

    def shape(self, at):
        key_list, types, offsets = struct.unpack_from("<3I", self.data, at)
        keys = self.keys(key_list)
        width = INT_WIDTH.get(self.data[offsets], "B")
        offs = struct.unpack_from(f"<{len(keys)}{width}", self.data, offsets + 1)
        return keys, self.data[types:types + len(keys)], offs, offsets

    def object(self, at, depth=0):
        """{key: value}; strings decoded, nested objects decoded `depth` levels deep (else left as refs)."""
        keys, types, offs, table = self.shape(self.u32(at))
        out = {}
        for i, (key, code, off) in enumerate(zip(keys, types, offs)):
            shared = code >= SHARED
            base = code - SHARED if shared else code
            where = table + 1 + i - off if shared else at + off
            fmt = FORMATS.get(base)
            value = struct.unpack_from("<" + fmt, self.data, where)[0] if fmt else None
            if base == STRING:
                value = self.string(value)
            elif base == BOOL:
                value = bool(value)
            elif base == OBJECT and depth > 0:
                value = self.nested(value, depth - 1)
            out[key] = value
        return out

    def nested(self, ref, depth):
        """A referenced object, or the raw reference when it doesn't decode as one (a few value codes
        are still unknown, so some fields read as object refs are really something else)."""
        try:
            return self.object(ref, depth)
        except (struct.error, KeyError, UnicodeDecodeError, IndexError):
            return ref

    def table(self, name, depth=1):
        """{row id: {field: value}} for a config table (None if the game has no such table)."""
        rows = self.object(self.root).get(name)
        return self.object(rows, depth + 1) if rows else None


class Text:
    """Localized strings by hash (the u32 the config tables store for names)."""

    def __init__(self, lang="en"):
        self.index = json.load(open(os.path.join(I18N, f"NewTextMap_{lang}.json"), encoding="utf-8"))
        self.blob = open(os.path.join(I18N, f"Compress_{lang}.bin"), "rb").read()

    def get(self, key):
        entry = self.index.get(str(key)) if key is not None else None
        return self.blob[entry[0]:entry[0] + entry[1]].decode("utf-8", "replace") if entry else None
