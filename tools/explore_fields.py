# Exploration: for each field name, find its FieldInfo, the declaring class and the values in live instances
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# Usage: python explore_fields.py <field> [<field>...]
import struct
import sys

from aniimo.memory import Il2Cpp, Process, find_pid

proc = Process(find_pid())
il = Il2Cpp(proc)
lo = il.metadata + il.strings_offset
hi = lo + il.strings_size


def mstring(addr):
    if not addr:
        return None
    n = proc.i32(addr + 0x10)
    b = proc.read(addr + 0x14, 2 * n) if n and 0 < n < 256 else None
    return b.decode("utf-16le", "replace") if b else None


targets = {il.string_address(n): n for n in sys.argv[1:]}
for value, hits in proc.scan_qwords(targets.keys(), private_only=False).items():
    for h in hits:
        _name, _type, parent, offset = struct.unpack("<QQQi", proc.read(h, 0x1C))
        if not (0x10000 < parent < 0x7FFFFFFFFFFF and 0 <= offset < 0x2000):
            continue
        print(f"{targets[value]}: FieldInfo {h:#x} class {parent:#x} offset {offset:#x}")
        objs = [o for o in proc.scan_qwords([parent])[parent] if not lo <= (proc.u64(o - 0x10) or 0) < hi]
        for o in objs[:12]:
            raw = proc.read(o + offset, 8)
            if not raw:
                continue
            i32, = struct.unpack_from("<i", raw)
            ptr, = struct.unpack_from("<Q", raw)
            print(f"   obj {o:#x}: i32={i32} ptr={ptr:#x} str={mstring(ptr) if 0x10000 < ptr < 0x7FFFFFFFFFFF else None!r}")
