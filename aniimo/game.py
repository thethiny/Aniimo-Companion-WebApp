# Live, read-only view of the local player in Aniimo: position, facing, current map scene,
# the minimap's own map position (for calibration) and the route the game is guiding along.
# Everything is resolved by name at attach time, so offsets survive game updates.
#
# Two threads:
#   fast (10 Hz)  reads the pose of the already-found player, the minimap and nothing else
#   slow (1 Hz)   everything that scans memory: attach, (re)finding the player, scene, route, minimap
# so a scan never stalls position updates.
import logging
import math
import struct
import threading
import time

from .memory import Il2Cpp, Process, find_pid, is_pointer
from .minimap import MinimapReader
from .route import RouteReader
from .transform import world_pose

log = logging.getLogger("aniimo")

PLAYER = ("FunPlus.WorldX.Entities.Components", "MainPlayerComponent")
CHUNK = ("FunPlus.WorldX.GameApp.UIMap", "ChunkingLoad")
MAIN_FIELD, AGENT_FIELD, ACTIVE_FIELD = "<mainPlayerComponent>k__BackingField", "positionAgent", "<isActive>k__BackingField"
CHUNK_FIELDS = ["sceneId", "isFog", "mapId", "desiredMapSize", "minimapChunkSize", "_lastLoadTickMS"]
OWNER_SEARCH = range(0x10, 0x40, 8)   # where a component keeps its owning entity

FAST_HZ = 10
SLOW_SECONDS = 1.0
LOST_GRACE_SECONDS = 15.0     # keep the same player through loading screens before searching again
FROZEN_SECONDS = 3.0          # minimap moving but our position not: the player object went stale
FROZEN_REFIND_SECONDS = 30.0  # at most one frozen-triggered search this often
TELEPORT_DISTANCE = 300.0     # a jump this big means a teleport or a scene change
SCENE_RECHECK_SECONDS = 60.0
MINIMAP_LOCATE_SECONDS = 30.0
MOVE_EPSILON = 0.3            # metres between polls that count as moving


class GameReader:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"status": "waiting", "message": "Waiting for Aniimo.exe"}
        self._stop = threading.Event()
        self.proc = self.il = None
        self.classes, self.offsets = {}, {}
        self.entity = self.component = None
        self.scene = self.route = self.minimap = self.route_reader = None
        self.need_player = self.need_scene = True
        # fast-loop bookkeeping
        self._last_ok = 0.0
        self._last_pos = None
        self._last_moved = self._map_last_moved = 0.0
        self._prev_pair = None
        self._map_live = False
        self._last_scene_check = self._last_minimap_locate = self._last_refind = -1e9

    # ---- lifecycle ----

    def start(self):
        threading.Thread(target=self._fast_loop, name="GameReader.fast", daemon=True).start()
        threading.Thread(target=self._slow_loop, name="GameReader.slow", daemon=True).start()

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _set(self, **state):
        # The scene outlives short gaps (loading screens, teleports), so the page keeps its map
        state.setdefault("scene", self.scene)
        with self.lock:
            changed = (state.get("status"), state.get("message")) != (self.state.get("status"), self.state.get("message"))
            self.state = state
        if changed:
            log.info("%s: %s", state.get("status"), state.get("message", ""))

    # ---- slow loop: every memory scan happens here ----

    def _slow_loop(self):
        while not self._stop.is_set():
            try:
                if not self.proc or not self.proc.alive():
                    self._attach()
                    continue
                now = time.time()
                if self.need_player:
                    self._find_player()
                if self.need_scene or now - self._last_scene_check >= SCENE_RECHECK_SECONDS:
                    self._check_scene()
                self.route = self.route_reader.read(now) if self.route_reader else None
                if not (self.minimap and self.minimap.located()) and now - self._last_minimap_locate >= MINIMAP_LOCATE_SECONDS:
                    self._last_minimap_locate = now
                    minimap = MinimapReader(self.proc)
                    self.minimap = minimap if minimap.locate() else None
                    log.info("minimap %s", "found" if self.minimap else "not found yet")
            except Exception as e:
                log.exception("slow loop")
                self._set(status="error", message=f"{type(e).__name__}: {e}")
                self._stop.wait(3)
            self._stop.wait(SLOW_SECONDS)

    def _attach(self):
        self.proc = self.il = self.entity = self.minimap = self.route_reader = None
        pid = find_pid()
        if not pid:
            self._set(status="waiting", message="Waiting for Aniimo.exe")
            self._stop.wait(3)
            return
        self._set(status="attaching", message="Attaching to the game (about 30 seconds)")
        started = time.time()
        proc = Process(pid)
        il = Il2Cpp(proc)
        found = il.resolve([PLAYER, CHUNK], [MAIN_FIELD, AGENT_FIELD, ACTIVE_FIELD])
        player_class = found["classes"].get(PLAYER[1])
        if not player_class or not found["fields"][MAIN_FIELD]:
            raise RuntimeError("MainPlayerComponent not found (game version not supported?)")
        # Entity fields: take them from the class that declares <mainPlayerComponent>, not any class with the same name
        entity_class, main_off = found["fields"][MAIN_FIELD][0]
        offsets = {MAIN_FIELD: main_off}
        for name in (AGENT_FIELD, ACTIVE_FIELD):
            offsets[name] = next(off for cls, off in found["fields"][name] if cls == entity_class)
        chunk_class = found["classes"].get(CHUNK[1])
        if chunk_class:
            chunk_fields, _ = found["members"][chunk_class]
            offsets.update({f"chunk.{n}": chunk_fields[n] for n in CHUNK_FIELDS if n in chunk_fields})
        self.classes = {"player": player_class, "chunk": chunk_class}
        self.offsets = offsets
        self.proc, self.il = proc, il
        self.route_reader = RouteReader(proc)
        self.need_player = self.need_scene = True
        log.info("attached in %.0f s: %s", time.time() - started, {k: hex(v) for k, v in offsets.items()})
        # The pass that found the classes also found their instances: use them for the first search
        self._find_player(found["members"][player_class][1])
        if chunk_class:
            self._check_scene(found["members"][chunk_class][1])

    def _find_player(self, candidates=None):
        if candidates is None:
            candidates = self.il.instances(self.classes["player"])
        main_off, active_off = self.offsets[MAIN_FIELD], self.offsets[ACTIVE_FIELD]
        found = []
        for comp in candidates:
            for owner_off in OWNER_SEARCH:
                entity = self.proc.u64(comp + owner_off)
                if is_pointer(entity) and self.proc.u64(entity + main_off) == comp:
                    active = (self.proc.read(entity + active_off, 1) or b"\0")[0]
                    found.append((active, comp, entity))
        if not found:
            self._set(status="searching", message="Looking for your character (loading screen or teleport in progress)")
            return
        found.sort(reverse=True)   # active entities first
        _, self.component, self.entity = found[0]
        self.need_player = False
        self._last_ok = self._last_moved = self._map_last_moved = time.time()
        log.info("player entity %#x (%d candidate(s))", self.entity, len(found))

    def _check_scene(self, candidates=None):
        self._last_scene_check = time.time()
        self.need_scene = False
        chunk_class = self.classes.get("chunk")
        if not chunk_class:
            return
        if candidates is None:
            candidates = self.il.instances(chunk_class)
        o, best = self.offsets, None
        for obj in candidates:
            raw = self.proc.read(obj, 0x140)
            if not raw or len(raw) < 0x140:
                continue
            scene = struct.unpack_from("<i", raw, o["chunk.sceneId"])[0]
            chunk = struct.unpack_from("<i", raw, o["chunk.minimapChunkSize"])[0]
            tick = struct.unpack_from("<q", raw, o["chunk._lastLoadTickMS"])[0]
            map_id = self.proc.mstring(struct.unpack_from("<Q", raw, o["chunk.mapId"])[0])
            if raw[o["chunk.isFog"]] or not 0 < scene < 100000 or chunk <= 0 or not map_id or not map_id.isdigit():
                continue
            w, h = struct.unpack_from("<ff", raw, o["chunk.desiredMapSize"])
            if best is None or tick > best["tick"]:
                best = {"id": scene, "mapId": map_id, "size": [w, h], "tick": tick}
        if best and (not self.scene or best["mapId"] != self.scene["mapId"]):
            log.info("scene %s", best["mapId"])
        if best:
            self.scene = best

    # ---- fast loop: reads only ----

    def _fast_loop(self):
        while not self._stop.is_set():
            try:
                if self.proc and self.entity and not self.need_player:
                    self._tick()
            except Exception as e:
                log.warning("fast loop: %s", e)
            self._stop.wait(1.0 / FAST_HZ)

    def _player_pose(self):
        entity = self.entity
        if self.proc.u64(self.component) != self.classes["player"] or self.proc.u64(entity + self.offsets[MAIN_FIELD]) != self.component:
            return None
        agent = self.proc.u64(entity + self.offsets[AGENT_FIELD])
        return world_pose(self.proc, agent) if is_pointer(agent) else None

    def _tick(self):
        now = time.time()
        pose = self._player_pose()
        if not pose:
            # Loading screens and teleports make the player unreadable for a while: keep the same object
            if now - self._last_ok > LOST_GRACE_SECONDS:
                log.info("player unreadable for %.0f s, searching again", now - self._last_ok)
                self.need_player = True
            self._set(status="searching", message="Your character is loading (teleport or loading screen)")
            return
        self._last_ok = now
        (x, y, z), (fx, _, fz) = pose
        minimap = self.minimap
        map_pos = self._live_map_pos((x, z), minimap.read() if minimap else None, now)
        if self._last_pos and math.dist(self._last_pos, (x, z)) > MOVE_EPSILON:
            self._last_moved = now
        if self._last_pos and math.dist(self._last_pos, (x, z)) > TELEPORT_DISTANCE:
            log.info("teleport (%.0f m)", math.dist(self._last_pos, (x, z)))
            self.need_scene = True
        # Minimap still moving while our position is frozen: we are reading a stale object
        if (now - self._map_last_moved < 1.0 and now - self._last_moved > FROZEN_SECONDS
                and now - self._last_refind > FROZEN_REFIND_SECONDS):
            log.info("position frozen while the minimap moves, searching again")
            self._last_refind = now
            self.need_player = True
        self._last_pos = (x, z)
        self._set(status="ok", pos={"x": x, "y": y, "z": z}, heading=math.degrees(math.atan2(fx, fz)),
                  scene=self.scene, route=self.route, mapPos=map_pos, time=now)

    def _live_map_pos(self, world, map_pos, now):
        """The minimap's pixel only while it is updating: hidden minimaps (big map open, menus,
        cutscenes) keep a frozen value that must not be paired with the live world position."""
        prev, self._prev_pair = self._prev_pair, (world, map_pos) if map_pos else None
        if not map_pos or not prev:
            self._map_live = False
            return None
        prev_world, prev_map = prev
        if math.dist(prev_map, map_pos) > MOVE_EPSILON * 0.5:
            self._map_last_moved = now
        if math.dist(prev_world, world) > MOVE_EPSILON:
            self._map_live = math.dist(prev_map, map_pos) > MOVE_EPSILON * 0.5
        return map_pos if self._map_live else None
