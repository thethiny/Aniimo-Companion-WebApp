# Dumps constants (strings, numbers, tables) from LuaJIT 2.x bytecode, per function prototype.
# Opcode numbering in Aniimo is shuffled, so instructions are not decoded; constants don't depend on it.
# Usage: python luajit_dump.py <file.lua> [--json]
import json
import struct
import sys


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        out = self.data[self.pos:self.pos + n]
        if len(out) != n:
            raise ValueError("truncated")
        self.pos += n
        return out

    def uleb(self):
        value, shift = 0, 0
        while True:
            b = self.data[self.pos]
            self.pos += 1
            value |= (b & 127) << shift
            if b < 128:
                return value
            shift += 7

    def uleb33(self):
        # First byte carries a flag in its lowest bit (number constants)
        b = self.data[self.pos]
        is_double = b & 1
        value, shift = b >> 1, 6
        self.pos += 1
        if value >= 64:
            value &= 63
            while True:
                b = self.data[self.pos]
                self.pos += 1
                value |= (b & 127) << shift
                if b < 128:
                    break
                shift += 7
        return is_double, value


def table_value(r):
    tag = r.uleb()
    if tag == 0:
        return None
    if tag == 1:
        return False
    if tag == 2:
        return True
    if tag == 3:
        v = r.uleb()
        return v - (1 << 32) if v >= 1 << 31 else v
    if tag == 4:
        lo, hi = r.uleb(), r.uleb()
        return struct.unpack("<d", struct.pack("<II", lo, hi))[0]
    return r.take(tag - 5).decode("utf-8", "replace")


def parse(data):
    r = Reader(data)
    if r.take(4) != b"\x1bLJ\x02":
        raise ValueError("not LuaJIT 2 bytecode")
    flags = r.uleb()
    stripped = bool(flags & 2)
    chunk = r.take(r.uleb()).decode("utf-8", "replace") if not stripped else None
    protos = []
    while True:
        size = r.uleb()
        if not size:
            break
        end = r.pos + size
        _flags, params, frame, n_uv = r.take(4)
        n_gc, n_num, n_bc = r.uleb(), r.uleb(), r.uleb()
        if not stripped:
            n_debug = r.uleb()
            if n_debug:
                first_line, n_lines = r.uleb(), r.uleb()
        r.take(n_bc * 4 + n_uv * 2)
        gc = []
        for _ in range(n_gc):
            tag = r.uleb()
            if tag >= 5:
                gc.append(r.take(tag - 5).decode("utf-8", "replace"))
            elif tag == 0:
                gc.append("<child>")
            elif tag == 1:
                n_arr, n_hash = r.uleb(), r.uleb()
                arr = [table_value(r) for _ in range(n_arr)]
                hsh = {}
                for _ in range(n_hash):
                    k = table_value(r)
                    hsh[str(k)] = table_value(r)
                gc.append({"array": arr, "hash": hsh})
            else:
                # 64-bit integer / complex cdata
                gc.append(("cdata", [r.uleb() for _ in range(4 if tag == 4 else 2)]))
        nums = []
        for _ in range(n_num):
            is_double, lo = r.uleb33()
            if is_double:
                hi = r.uleb()
                nums.append(struct.unpack("<d", struct.pack("<II", lo & 0xFFFFFFFF, hi))[0])
            else:
                nums.append(lo - (1 << 32) if lo >= 1 << 31 else lo)
        r.pos = end
        protos.append({"params": params, "strings_and_tables": list(reversed(gc)), "numbers": nums})
    return chunk, protos


if __name__ == "__main__":
    chunk, protos = parse(open(sys.argv[1], "rb").read())
    if "--json" in sys.argv:
        print(json.dumps({"chunk": chunk, "protos": protos}, ensure_ascii=False, indent=1))
    else:
        print("chunk:", chunk)
        for i, p in enumerate(protos):
            print(f"-- proto {i} params={p['params']}")
            print("   k:", p["strings_and_tables"])
            print("   n:", p["numbers"])
