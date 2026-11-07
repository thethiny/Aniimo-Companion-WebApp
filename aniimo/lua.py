# Read-only LuaJIT (GC64) heap access: interned strings, table nodes and table contents
import struct

GCSTR_SIZE = 0x18          # GCstr header, characters follow
GCSTR_LEN = 0x14
NODE_SIZE = 0x18           # TValue val, TValue key, next
TAB_ARRAY, TAB_NODE, TAB_ASIZE, TAB_HMASK = 0x10, 0x28, 0x30, 0x34
PTR_MASK = (1 << 47) - 1

# itype values (upper 17 bits of a GC64 TValue), see lj_obj.h
NIL, FALSE, TRUE = 0x1FFFF, 0x1FFFE, 0x1FFFD
STR, TAB = 0x1FFFB, 0x1FFF4   # ~4, ~11


def tag(itype, ptr):
    return (itype << 47) | ptr


def itype(raw):
    return raw >> 47


def find_strings(proc, names):
    """{name: [GCstr address]} for interned strings with these exact contents."""
    found = {n: [] for n in names}
    for base, data in proc.chunks(private_only=True):
        for n in names:
            needle = n.encode() + b"\0"
            i = data.find(needle)
            while i != -1:
                if i >= GCSTR_SIZE and struct.unpack_from("<I", data, i - GCSTR_SIZE + GCSTR_LEN)[0] == len(n.encode()):
                    found[n].append(base + i - GCSTR_SIZE)
                i = data.find(needle, i + 1)
    return found


def read_str(proc, gcstr):
    n = proc.i32(gcstr + GCSTR_LEN)
    if n is None or not 0 <= n < 4096:
        return None
    b = proc.read(gcstr + GCSTR_SIZE, n)
    return b.decode("utf-8", "replace") if b is not None else None


def value(proc, raw, depth):
    t = itype(raw)
    if t == NIL:
        return None
    if t == FALSE:
        return False
    if t == TRUE:
        return True
    if t == STR:
        return read_str(proc, raw & PTR_MASK)
    if t == TAB:
        return read_table(proc, raw & PTR_MASK, depth - 1) if depth > 0 else f"<table {raw & PTR_MASK:#x}>"
    if t < 0x1FFF2:   # plain double
        return struct.unpack("<d", struct.pack("<Q", raw))[0]
    return f"<itype {t:#x}>"


def read_table(proc, tab, depth=2):
    """Lua table as {key: value}; array part keyed by int index."""
    head = proc.read(tab, 0x38)
    if not head or len(head) < 0x38:
        return None
    array, node = struct.unpack_from("<Q", head, TAB_ARRAY)[0], struct.unpack_from("<Q", head, TAB_NODE)[0]
    asize, hmask = struct.unpack_from("<II", head, TAB_ASIZE)
    if asize > 1 << 16 or hmask > 1 << 16:
        return None
    out = {}
    if asize and array:
        raw = proc.read(array, asize * 8) or b""
        for i in range(len(raw) // 8):
            v = struct.unpack_from("<Q", raw, i * 8)[0]
            if itype(v) != NIL:
                out[i] = value(proc, v, depth)
    if node:
        raw = proc.read(node, (hmask + 1) * NODE_SIZE) or b""
        for i in range(len(raw) // NODE_SIZE):
            val, key = struct.unpack_from("<QQ", raw, i * NODE_SIZE)
            if itype(val) == NIL or itype(key) == NIL:
                continue
            k = value(proc, key, 0)
            out[k if not isinstance(k, float) or not k.is_integer() else int(k)] = value(proc, val, depth)
    return out


def owner_tables(proc, nodes, max_index=256):
    """{node: GCtab address} for table nodes: the table whose node array contains each node."""
    starts = {n - i * NODE_SIZE: n for n in nodes for i in range(max_index)}
    found = {}
    for start, refs in proc.scan_qwords(starts.keys()).items():
        node = starts[start]
        for ref in refs:
            tab = ref - TAB_NODE
            hmask = proc.i32(tab + TAB_HMASK)
            if hmask is not None and 0 <= hmask < 1 << 16 and node < start + (hmask + 1) * NODE_SIZE:
                found.setdefault(node, tab)
    return found


def tables_with_key(proc, gcstrs):
    """Node addresses (and owning node-array guesses) whose key is one of the given GCstr addresses."""
    keys = [tag(STR, a) for a in gcstrs]
    hits = proc.scan_qwords(keys)
    return [a - 8 for addrs in hits.values() for a in addrs]   # node starts at val, key is +8
