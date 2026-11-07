# Exploration: resolve a class by name (any namespace), list its fields and live instances
# Usage: python explore_class.py <ClassName> [max instances to dump]
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from aniimo.memory import CLASS_SELF, CLASS_TYPEDEF, Il2Cpp, Process, find_pid, is_pointer  # noqa: E402

name = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 8
proc = Process(find_pid())
il = Il2Cpp(proc)

name_ptr = il.string_address(name)
for rec in proc.scan_qwords([name_ptr], private_only=False)[name_ptr]:
    ns_ptr, typedef = proc.u64(rec - 8), proc.u64(rec + 8)
    if not is_pointer(ns_ptr) or not is_pointer(typedef) or not il.is_metadata_string(ns_ptr):
        continue
    namespace = proc.cstr(ns_ptr)
    for ref in proc.scan_qwords([typedef], private_only=False)[typedef]:
        klass = ref - CLASS_TYPEDEF
        if proc.u64(klass + CLASS_SELF) != klass:
            continue
        fields, objects = il.class_members(klass)
        print(f"{namespace}.{name}: class {klass:#x}")
        for field, offset in sorted(fields.items(), key=lambda kv: kv[1]):
            print(f"  {offset:#06x} {field}")
        print(f"  {len(objects)} candidate instances")
        for o in objects[:limit]:
            raw = proc.read(o, 0x80) or b""
            print(f"   {o:#x}: " + " ".join(f"{v:#x}" for v in struct.unpack_from(f"<{len(raw) // 8}Q", raw)))
