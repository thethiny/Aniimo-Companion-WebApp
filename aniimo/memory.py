# Read-only access to the Aniimo process: memory reads and IL2CPP class/field lookup by name
import ctypes
import ctypes.wintypes as wt
import struct
import subprocess

import numpy as np

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
STILL_ACTIVE = 259
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
METADATA_MAGIC = 0xFAB11BAF
CHUNK = 1 << 24
USER_MAX = 0x7FFFFFFFFFFF

# FieldInfo: name, type, parent class, offset (int32), token
FIELD_SIZE, FIELD_PARENT, FIELD_OFFSET = 0x20, 0x10, 0x18
# The game's Il2CppClass keeps its typeDefinition at +0x40 and a pointer to itself at +0x38
CLASS_TYPEDEF, CLASS_SELF = 0x40, 0x38


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p), ("AllocationProtect", wt.DWORD),
                ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD), ("Protect", wt.DWORD), ("Type", wt.DWORD)]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.CloseHandle.argtypes = [wt.HANDLE]


def find_pid(exe="Aniimo.exe"):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    for line in out.splitlines():
        cols = line.strip('"').split('","')
        if len(cols) > 1 and cols[0].lower() == exe.lower():
            return int(cols[1])
    return None


def is_pointer(v):
    return v is not None and 0x10000 < v < USER_MAX


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.handle:
            raise OSError(f"OpenProcess failed: {ctypes.get_last_error()}")

    def alive(self):
        code = wt.DWORD()
        return bool(k32.GetExitCodeProcess(self.handle, ctypes.byref(code))) and code.value == STILL_ACTIVE

    def close(self):
        k32.CloseHandle(self.handle)

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def u64(self, addr):
        b = self.read(addr, 8)
        return struct.unpack("<Q", b)[0] if b and len(b) == 8 else None

    def i32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b and len(b) == 4 else None

    def floats(self, addr, n):
        b = self.read(addr, 4 * n)
        return struct.unpack(f"<{n}f", b) if b and len(b) == 4 * n else None

    def cstr(self, addr, limit=256):
        b = self.read(addr, limit)
        return b.split(b"\0", 1)[0].decode("utf-8", "replace") if b else None

    def mstring(self, addr):
        """System.String contents (length at +0x10, UTF-16 at +0x14)."""
        if not is_pointer(addr):
            return None
        n = self.i32(addr + 0x10)
        b = self.read(addr + 0x14, 2 * n) if n and 0 < n < 1024 else None
        return b.decode("utf-16le", "replace") if b else None

    def regions(self, private_only=False):
        addr, mbi = 0, MBI()
        while k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            readable = mbi.State == MEM_COMMIT and not mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)
            if readable and (not private_only or mbi.Type == MEM_PRIVATE):
                yield base, size
            addr = base + size

    def chunks(self, private_only=False):
        for base, size in self.regions(private_only):
            for off in range(0, size, CHUNK):
                data = self.read(base + off, min(CHUNK, size - off))
                if data:
                    yield base + off, data

    def scan_qwords(self, values, private_only=True):
        """{value: [8-aligned addresses holding it]}."""
        values = list(values)
        want = np.array(sorted(values), dtype=np.uint64)
        hits = {v: [] for v in values}
        for base, data in self.chunks(private_only):
            arr = np.frombuffer(data, dtype=np.uint64, count=len(data) // 8)
            for i in np.nonzero(np.isin(arr, want))[0]:
                hits[int(arr[i])].append(base + int(i) * 8)
        return hits


class Il2Cpp:
    """Resolves classes and fields through the decrypted metadata the game keeps in memory."""

    def __init__(self, proc):
        self.proc = proc
        self.metadata = self._find_metadata()
        self.strings_offset, self.strings_size = struct.unpack("<II", proc.read(self.metadata + 8 + 2 * 8, 8))
        self._strings = proc.read(self.metadata + self.strings_offset, self.strings_size)

    def _find_metadata(self):
        # Header: magic, total size where the version used to be, then the first section at 0x100
        magic = struct.pack("<I", METADATA_MAGIC)
        for base, data in self.proc.chunks(private_only=True):
            i = data.find(magic)
            while i != -1:
                if i + 16 <= len(data):
                    size, first = struct.unpack_from("<II", data, i + 4)
                    if first == 0x100 and 0x100000 < size < 0x10000000:
                        return base + i
                i = data.find(magic, i + 4)
        raise RuntimeError("Decrypted IL2CPP metadata not found in memory")

    def is_metadata_string(self, addr):
        lo = self.metadata + self.strings_offset
        return addr is not None and lo <= addr < lo + self.strings_size

    def string_address(self, name):
        i = self._strings.find(b"\0" + name.encode() + b"\0")
        if i == -1:
            raise KeyError(name)
        return self.metadata + self.strings_offset + i + 1

    def find_class(self, namespace, name):
        """Il2CppClass* via the runtime's (namespace, name, typeDefinition) lookup records."""
        name_ptr = self.string_address(name)
        for rec in self.proc.scan_qwords([name_ptr], private_only=False)[name_ptr]:
            ns, typedef = self.proc.u64(rec - 8), self.proc.u64(rec + 8)
            if not is_pointer(ns) or not is_pointer(typedef) or self.proc.cstr(ns) != namespace:
                continue
            for ref in self.proc.scan_qwords([typedef], private_only=False)[typedef]:
                klass = ref - CLASS_TYPEDEF
                if self.proc.u64(klass + CLASS_SELF) == klass:
                    return klass
        return None

    def field_offsets(self, names):
        """{field name: [(declaring class, offset)]} from the runtime FieldInfo records."""
        targets = {self.string_address(n): n for n in names}
        found = {n: [] for n in names}
        for value, refs in self.proc.scan_qwords(targets.keys(), private_only=False).items():
            for ref in refs:
                entry = self.proc.read(ref, FIELD_SIZE)
                if not entry or len(entry) < FIELD_SIZE:
                    continue
                _name, _type, parent, offset = struct.unpack_from("<QQQi", entry)
                if is_pointer(parent) and 0 <= offset < 0x10000:
                    found[targets[value]].append((parent, offset))
        return found

    def class_members(self, klass):
        """(own fields {name: offset}, candidate instance addresses) from every reference to klass."""
        fields, objects = {}, []
        for ref in self.proc.scan_qwords([klass], private_only=False)[klass]:
            name_ptr = self.proc.u64(ref - FIELD_PARENT)
            if self.is_metadata_string(name_ptr):
                fields[self.proc.cstr(name_ptr)] = self.proc.i32(ref - FIELD_PARENT + FIELD_OFFSET)
            elif ref != klass:
                objects.append(ref)
        return fields, objects

    def instances(self, klass):
        """Managed objects whose class pointer is klass (private heap only)."""
        return self.proc.scan_qwords([klass])[klass]
