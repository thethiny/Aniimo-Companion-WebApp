# The route the game is guiding the player along, mirrored read-only from the Lua NavEffectSystem.
# NavEffectSystem keeps:
#   guidingLineData  nil when nothing is tracked, else the guide line state (end position, ...)
#   recordPath       {sceneId, trackId, path = server navmesh path corners, overrideStartPosInfo}
# overrideStartPosInfo.startSpawnerId is set when the game routes from a teleport point instead of the player.
from . import lua

KEY_ACTIVE, KEY_RECORD = "guidingLineData", "recordPath"
RESCAN_SECONDS = 30.0


def _xz(v):
    """[x, z] from a Lua vector ({x, y, z} or {1, 2, 3})."""
    if not isinstance(v, dict):
        return None
    x, z = v.get("x", v.get(1)), v.get("z", v.get(3))
    return [x, z] if isinstance(x, float) and isinstance(z, float) else None


def _array(t):
    return [t[i] for i in sorted(k for k in t if isinstance(k, int))] if isinstance(t, dict) else []


class RouteReader:
    def __init__(self, proc):
        self.proc = proc
        self.tags = {}           # key name -> tagged GCstr
        self.nodes = {}          # key name -> table node holding it
        self.last_scan = -RESCAN_SECONDS

    def _scan(self, now):
        self.last_scan = now
        strs = lua.find_strings(self.proc, [KEY_ACTIVE, KEY_RECORD])
        for key, addrs in strs.items():
            if not addrs:
                continue
            self.tags[key] = lua.tag(lua.STR, addrs[0])
            nodes = lua.tables_with_key(self.proc, addrs)
            # Prefer a node that currently holds a table (an active route)
            nodes.sort(key=lambda n: lua.itype(self.proc.u64(n) or 0) != lua.TAB)
            if nodes:
                self.nodes[key] = nodes[0]

    def _value(self, key, depth):
        node = self.nodes.get(key)
        if not node or self.proc.u64(node + 8) != self.tags.get(key):
            return None
        raw = self.proc.u64(node)
        if raw is None or lua.itype(raw) != lua.TAB:
            return None
        return lua.read_table(self.proc, raw & lua.PTR_MASK, depth=depth)

    def read(self, now):
        """{points: [[x, z]...], end: [x, z], start: {...} | None, scene, trackId} or None when nothing is tracked."""
        nodes_ok = all(self.nodes.get(k) and self.proc.u64(self.nodes[k] + 8) == self.tags.get(k)
                       for k in (KEY_ACTIVE, KEY_RECORD))
        if not nodes_ok and now - self.last_scan >= RESCAN_SECONDS:
            self._scan(now)
        active = self._value(KEY_ACTIVE, depth=2)
        if active is None:
            # Nothing tracked. Tables rehash as they grow, so re-locate the keys now and then
            if now - self.last_scan >= RESCAN_SECONDS:
                self._scan(now)
            return None
        record = self._value(KEY_RECORD, depth=3) or {}
        if record.get("trackId") != active.get("id"):
            return None   # the recorded path belongs to an older request
        points = [p for p in (_xz(v) for v in _array(record.get("path"))) if p]
        if len(points) < 2:
            return None
        start_info = record.get("overrideStartPosInfo")
        start = None
        if isinstance(start_info, dict) and _xz(start_info.get("startPos")):
            start = {"pos": _xz(start_info["startPos"]),
                     "teleport": isinstance(start_info.get("startSpawnerId"), float)}
        return {"points": points, "end": _xz(active.get("endPos")) or points[-1], "start": start,
                "scene": record.get("sceneId"), "trackId": active.get("id")}
