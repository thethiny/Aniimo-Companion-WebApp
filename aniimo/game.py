# Live, read-only view of the local player in Aniimo: position, facing and current map scene.
# Everything is resolved by name at attach time, so offsets survive game updates.
import math
import struct
import threading
import time

from .memory import Il2Cpp, Process, find_pid, is_pointer
from .route import RouteReader
from .transform import world_pose

PLAYER_NAMESPACE, PLAYER_CLASS = "FunPlus.WorldX.Entities.Components", "MainPlayerComponent"
CHUNK_NAMESPACE, CHUNK_CLASS = "FunPlus.WorldX.GameApp.UIMap", "ChunkingLoad"
ENTITY_FIELDS = ["<mainPlayerComponent>k__BackingField", "positionAgent"]
CHUNK_FIELDS = ["sceneId", "isFog", "mapId", "desiredMapSize", "row", "column", "minimapChunkSize", "_lastLoadTickMS"]
OWNER_SEARCH = range(0x10, 0x40, 8)   # where a component keeps its owning entity
TELEPORT_DISTANCE = 300.0             # a jump this big means a teleport or a scene change
SCENE_RECHECK_SECONDS = 60.0
ROUTE_POLL_SECONDS = 1.0


class GameReader:
    def __init__(self, poll_hz=10):
        self.poll_interval = 1.0 / poll_hz
        self.lock = threading.Lock()
        self.state = {"status": "waiting", "message": "Waiting for Aniimo.exe"}
        self._stop = threading.Event()
        self.proc = self.il = None
        self.player_class = self.chunk_class = None
        self.offsets = {}
        self.entity = self.component = None
        self.scene = None
        self._last_pos = None
        self._last_scene_check = 0.0
        self.route = None

    # ---- lifecycle ----

    def start(self):
        threading.Thread(target=self._run, name="GameReader", daemon=True).start()
        threading.Thread(target=self._run_route, name="RouteReader", daemon=True).start()

    def _run_route(self):
        # Separate thread: locating the route in the Lua heap can take seconds and must not stall position updates
        reader, proc = None, None
        while not self._stop.is_set():
            try:
                if self.proc is not proc:
                    proc = self.proc
                    reader = RouteReader(proc) if proc else None
                self.route = reader.read(time.time()) if reader else None
            except Exception:
                self.route = None
            self._stop.wait(ROUTE_POLL_SECONDS)

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _set(self, **state):
        # The scene outlives short gaps (loading screens, player re-spawn), so the page keeps its map
        state.setdefault("scene", self.scene)
        with self.lock:
            self.state = state

    def _run(self):
        while not self._stop.is_set():
            try:
                if not self.proc or not self.proc.alive():
                    self._attach()
                    continue
                if not self.entity and not self._find_player():
                    self._set(status="searching", message="Looking for your character (loading screen or teleport in progress)")
                    self._stop.wait(3)
                    continue
                self._tick()
                self._stop.wait(self.poll_interval)
            except Exception as e:  # keep serving; the next loop re-resolves
                self._set(status="error", message=f"{type(e).__name__}: {e}")
                self.entity = None
                self._stop.wait(3)

    # ---- attach and resolve ----

    def _attach(self):
        self.proc = self.il = self.entity = None
        pid = find_pid()
        if not pid:
            self._set(status="waiting", message="Waiting for Aniimo.exe")
            self._stop.wait(3)
            return
        self._set(status="attaching", message="Attaching to the game (takes 1-2 minutes)")
        proc = Process(pid)
        il = Il2Cpp(proc)
        self.player_class = il.find_class(PLAYER_NAMESPACE, PLAYER_CLASS)
        self.chunk_class = il.find_class(CHUNK_NAMESPACE, CHUNK_CLASS)
        if not self.player_class:
            raise RuntimeError("MainPlayerComponent class not found")
        entity = il.field_offsets(ENTITY_FIELDS)
        self.offsets = {name: hits[0][1] for name, hits in entity.items() if hits}
        if self.chunk_class:
            chunk_fields, _ = il.class_members(self.chunk_class)
            self.offsets.update({f"chunk.{n}": chunk_fields[n] for n in CHUNK_FIELDS if n in chunk_fields})
        self.proc, self.il = proc, il

    def _find_player(self):
        main_off = self.offsets["<mainPlayerComponent>k__BackingField"]
        for comp in self.il.instances(self.player_class):
            for owner_off in OWNER_SEARCH:
                entity = self.proc.u64(comp + owner_off)
                if is_pointer(entity) and self.proc.u64(entity + main_off) == comp:
                    self.entity, self.component = entity, comp
                    self._check_scene(force=True)
                    return True
        return False

    def _player_valid(self):
        main_off = self.offsets["<mainPlayerComponent>k__BackingField"]
        return (self.proc.u64(self.component) == self.player_class
                and self.proc.u64(self.entity + main_off) == self.component)

    # ---- scene ----

    def _check_scene(self, force=False):
        now = time.time()
        if not self.chunk_class or (not force and now - self._last_scene_check < SCENE_RECHECK_SECONDS):
            return
        self._last_scene_check = now
        o = self.offsets
        best = None
        for obj in self.il.instances(self.chunk_class):
            raw = self.proc.read(obj, 0x140)
            if not raw or len(raw) < 0x140:
                continue
            scene = struct.unpack_from("<i", raw, o["chunk.sceneId"])[0]
            is_fog = raw[o["chunk.isFog"]]
            chunk = struct.unpack_from("<i", raw, o["chunk.minimapChunkSize"])[0]
            tick = struct.unpack_from("<q", raw, o["chunk._lastLoadTickMS"])[0]
            map_id = self.proc.mstring(struct.unpack_from("<Q", raw, o["chunk.mapId"])[0])
            if is_fog or not 0 < scene < 100000 or chunk <= 0 or not map_id or not map_id.isdigit():
                continue
            w, h = struct.unpack_from("<ff", raw, o["chunk.desiredMapSize"])
            if best is None or tick > best["tick"]:
                best = {"id": scene, "mapId": map_id, "size": [w, h], "tick": tick}
        if best:
            self.scene = best

    # ---- polling ----

    def _tick(self):
        if not self._player_valid():
            self.entity = None
            return
        agent = self.proc.u64(self.entity + self.offsets["positionAgent"])
        pose = world_pose(self.proc, agent) if is_pointer(agent) else None
        if not pose:
            self._set(status="searching", message="Your character is being reloaded (teleport or loading screen)")
            return
        (x, y, z), (fx, _, fz) = pose
        if self._last_pos and math.dist(self._last_pos, (x, z)) > TELEPORT_DISTANCE:
            self._check_scene(force=True)
        else:
            self._check_scene()
        self._last_pos = (x, z)
        self._set(status="ok", pos={"x": x, "y": y, "z": z}, heading=math.degrees(math.atan2(fx, fz)),
                  scene=self.scene, route=self.route, time=time.time())
