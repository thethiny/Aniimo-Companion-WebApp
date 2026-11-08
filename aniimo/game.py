# Live, read-only view of the local player in Aniimo: position, facing, current map scene,
# the minimap's own map position (for calibration) and the route the game is guiding along.
# Everything is resolved by name at attach time, so offsets survive game updates.
#
# Threads, so a scan never stalls the live readings:
#   fast (10 Hz)   reads the pose of the already-found player, the minimap and nothing else
#   scene (1 Hz)   the map you're on, from the game's Lua map system (pg.game.map.sceneId), which also
#                  follows the maps you walk into without a loading screen
#   slow (1 Hz)    everything that scans memory: attach, (re)finding the player, route, the minimap loaders
#                  (the scene's fallback while the Lua map system isn't found yet)
#   minimap        the search for the minimap, which takes minutes where there's none to find
import logging
import math
import struct
import threading
import time

from . import entities, pg
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
TELEPORT_REFIND_SECONDS = 10.0  # after a teleport, search again once the new world is loaded: joining a
                                # friend's world builds a new player, and the old one still reads
# Every so often, a player in the game's Lua state (pg) must stand where we read you: if none does, we're
# reading a stale copy (of you or of pg, e.g. after joining a friend's world). First search for you again,
# then, still off, for pg too
PG_CHECK_SECONDS = 10.0
PG_NEAR = 50.0                # metres
PG_SETTLE_SECONDS = 20.0      # no check this soon after a teleport (the new world still loading)
PG_FORGET_SECONDS = 120.0     # at most one new search for pg this often
SCENE_RECHECK_SECONDS = 60.0
MINIMAP_LOCATE_SECONDS = 30.0
MOVE_EPSILON = 0.3            # metres between polls that count as moving
DEFAULT_LANE = "scan"         # collectors without a lane of their own
COLLECT_TICK = 1.0            # seconds between checks for due collectors
LOG_SLOWER_THAN = 3.0         # seconds; collections faster than this aren't logged


class GameReader:
    def __init__(self, collectors=()):
        # [(name, seconds, collect(proc) -> result, on_result(scene mapId, result)[, lane])]: periodic heap
        # collections. Each lane runs in its own thread, so quick live reads (handbook, weather) never wait
        # behind scans that take minutes (area names, marks); collectors without a lane share "scan"
        self.collectors = list(collectors)
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
        self._minimap_search = None
        self._lua_scene_ok = False
        self._refind_at = None
        self._last_teleport = self._last_pg_check = self._last_pg_forget = -1e9
        self._pg_strikes = 0

    # ---- lifecycle ----

    def start(self):
        threading.Thread(target=self._fast_loop, name="GameReader.fast", daemon=True).start()
        threading.Thread(target=self._slow_loop, name="GameReader.slow", daemon=True).start()
        threading.Thread(target=self._scene_loop, name="GameReader.scene", daemon=True).start()
        lanes = {}
        for collector in self.collectors:
            lanes.setdefault(collector[4] if len(collector) > 4 else DEFAULT_LANE, []).append(collector[:4])
        for lane, collectors in lanes.items():
            threading.Thread(target=self._collect_loop, args=(collectors,), name=f"GameReader.{lane}", daemon=True).start()

    def _collect_loop(self, collectors):
        last = {name: -1e9 for name, *_ in collectors}
        while not self._stop.is_set():
            for name, seconds, collect, on_result in collectors:
                proc, scene = self.proc, self.scene
                if not proc or not scene or time.time() - last[name] < seconds:
                    continue
                last[name] = time.time()
                try:
                    started = time.time()
                    result = collect(proc)
                    on_result(scene["mapId"], result)
                    took = time.time() - started
                    if took >= LOG_SLOWER_THAN:   # quick live reads run every few seconds: keep the log quiet
                        log.info("collected %s in %.0f s: %s", name, took,
                                 {k: len(v) for k, v in result.items() if isinstance(v, dict)})
                except Exception as e:
                    log.warning("collect %s: %s", name, e)
            self._stop.wait(COLLECT_TICK)

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
                if self._refind_at and now >= self._refind_at:
                    log.info("searching again after the teleport")
                    self._refind_at, self.need_player = None, True
                if self.need_player:
                    self._find_player()
                elif now - self._last_pg_check >= PG_CHECK_SECONDS and now - self._last_teleport >= PG_SETTLE_SECONDS:
                    self._check_pg(now)
                if not self._lua_scene_ok and (self.need_scene or now - self._last_scene_check >= SCENE_RECHECK_SECONDS):
                    self._check_scene()
                self.route = self.route_reader.read(now) if self.route_reader else None
                if not (self.minimap and self.minimap.located()) and now - self._last_minimap_locate >= MINIMAP_LOCATE_SECONDS:
                    self._locate_minimap()
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

    def _check_pg(self, now):
        """Whether a player in pg stands where we read you; if not, search for you, then for pg, again."""
        self._last_pg_check = now
        state = self.snapshot()
        pos = state.get("pos") if state.get("status") == "ok" else None
        if not pos or not pg.known() or not pg.fields(self.proc):   # the collectors search for pg, not this loop
            return
        players = [entities.pose(self.proc, e) for e in entities.loaded(self.proc, str.isdigit).values()
                   if entities.sub(self.proc, e, "__Properties__").get("clenUsrType") == entities.KIND_PLAYER]
        if any(p and math.dist((p["x"], p["z"]), (pos["x"], pos["z"])) < PG_NEAR for p in players):
            self._pg_strikes = 0
            return
        self._pg_strikes += 1
        if self._pg_strikes == 1:
            log.info("no player in the game's Lua state where we read you: searching for you again")
            self.need_player = True
        elif now - self._last_pg_forget >= PG_FORGET_SECONDS:
            log.info("still no player in the game's Lua state where we read you: searching for it again")
            self._pg_strikes, self._last_pg_forget = 0, now
            pg.forget()

    def _locate_minimap(self):
        """Searches for the minimap on a thread of its own: the search takes minutes on maps where the
        minimap can't be found (Whisperwake Isles), and must not hold up the scene and route checks."""
        if self._minimap_search and self._minimap_search.is_alive():
            return

        def search(proc):
            minimap = MinimapReader(proc)
            self.minimap = minimap if minimap.locate() else None
            self._last_minimap_locate = time.time()
            log.info("minimap %s", "found" if self.minimap else "not found yet")

        self._minimap_search = threading.Thread(target=search, args=(self.proc,), name="GameReader.minimap", daemon=True)
        self._minimap_search.start()

    def _scene_loop(self):
        while not self._stop.is_set():
            try:
                self._lua_scene_ok = bool(self.proc and self.proc.alive() and self._lua_scene())
            except Exception:
                log.exception("scene loop")
                self._lua_scene_ok = False
            self._stop.wait(SLOW_SECONDS)

    def _lua_scene(self):
        """Takes the scene from the game's map system; False while that can't be read."""
        scene_id = pg.scene(self.proc)
        if not scene_id:
            return False
        if not self.scene or self.scene["mapId"] != str(scene_id):
            log.info("scene %s", scene_id)
            self.scene = {"id": scene_id, "mapId": str(scene_id)}
        self.need_scene = False
        return True

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
                # Keeps reading the current player while a new search runs: a frozen-position search often
                # finds the same one (the minimap can move while you stand still, e.g. in a fight)
                if self.proc and self.entity:
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
            self._last_teleport, self._refind_at = now, now + TELEPORT_REFIND_SECONDS
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
