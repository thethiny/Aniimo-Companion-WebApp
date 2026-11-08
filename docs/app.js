// Aniimo live map: polls /api/state and draws the player on the game's own map tiles.
const POLL_MS = 200;
const $ = (id) => document.getElementById(id);
// The static site (tools/build_offline.py, e.g. aniimo.thethiny.com) is the offline map: static files, no player
// data. "Connect to game" switches it to the game server running on this PC (LIVE_SERVER), whose live data it
// then reads like the server's own page does.
const STATIC_SITE = window.OFFLINE_MAP === true;
const LIVE_SERVER = STATIC_SITE ? localStorageGet("liveServer") || "" : "";
const OFFLINE = STATIC_SITE && !LIVE_SERVER;
const apiUrl = (path) => (LIVE_SERVER ? `${LIVE_SERVER}/${path}` : path);   // every server call goes through here
const dataUrl = (api, file) => (OFFLINE ? `offline/${file}` : apiUrl(api));
const liveInterval = (fn, ms) => { if (!OFFLINE) setInterval(fn, ms); };   // live re-reads; offline data never changes

const map = L.map("map", { crs: L.CRS.Simple, zoomControl: false, attributionControl: false, zoomSnap: 0.25 });

let maps = {};
let current = null;          // { id, info, layer }
let state = null;
let follow = true;
let picking = false;
const calibDots = L.layerGroup().addTo(map);

const arrow = L.divIcon({
  className: "player",
  iconSize: [34, 34],
  iconAnchor: [17, 17],
  html: '<svg viewBox="0 0 34 34"><circle cx="17" cy="17" r="15" fill="rgba(255,211,77,.25)"/>'
      + '<path d="M17 4 L26 27 L17 22 L8 27 Z" fill="#ffd34d" stroke="#1b1300" stroke-width="1.5"/></svg>',
});
const player = L.marker([0, 0], { icon: arrow, interactive: false, keyboard: false, zIndexOffset: 10000 });   // above every mark

// ---- map pixels <-> Leaflet ----
const toLatLng = (px, py) => map.unproject([px, py], current.info.maxZoom);
const toPixel = (latlng) => map.project(latlng, current.info.maxZoom);

function calibrated() {
  const c = state && state.calibration;
  return c && c.x && c.z;
}
const worldToPixel = (x, z) => [state.calibration.x[0] * x + state.calibration.x[1], state.calibration.z[0] * z + state.calibration.z[1]];
const pixelToWorld = (px, py) => [(px - state.calibration.x[1]) / state.calibration.x[0], (py - state.calibration.z[1]) / state.calibration.z[0]];

// ---- scene switching ----
function showScene(scene) {
  const id = scene.mapId;
  if (current && current.id === id) return;
  const keepFollowing = !!current && follow;   // moving to another map while following: keep following
  const info = maps[id];
  if (!info) { $("world").textContent = id; return; }
  if (current) map.removeLayer(current.layer);
  // Some places are drawn on another map's image (Whisperwake Isles on Idyll's): info.tiles names it
  const layer = L.tileLayer(`tiles/${info.tiles || id}/{z}/{x}_{y}.webp`, {
    tileSize: info.tileSize, minZoom: 0, maxNativeZoom: info.maxZoom, maxZoom: info.maxZoom + 2, noWrap: true,
    bounds: L.latLngBounds(map.unproject([0, info.height], info.maxZoom), map.unproject([info.width, 0], info.maxZoom)),
  });
  current = { id, info, layer };
  layer.addTo(map);
  setBorders(info);
  map.setMaxZoom(info.maxZoom + 2);
  limitZoomOut();
  const startZoom = Number(new URLSearchParams(location.search).get("zoom"));   // ?zoom=4 to start there
  if (startZoom) map.setView(toLatLng(info.width / 2, info.height / 2), startZoom);
  else fitView();
  if (keepFollowing) setFollow(true);
  $("world").textContent = info.name;
  loadAreas(id, info);
  loadMarks();
  drawCalibDots();
}

// ---- small-area borders, traced from the game's area mask (shown with the area names) ----
let borders = null;
function setBorders(info) {
  if (borders) map.removeLayer(borders);
  borders = info.areaBorders
    ? L.imageOverlay(info.areaBorders, L.latLngBounds(toLatLng(0, info.height), toLatLng(info.width, 0)),
      { className: "area-borders", interactive: false })
    : null;
}

// ---- location: which small area you're in, from the game's own area mask ----
let areaGrid = null;      // { scene, scale, width, data }
let areaNames = {};       // /api/areas for the current scene

function loadAreas(id, info) {
  areaGrid = null;
  areaNames = {};
  refreshAreaNames();
  if (!info.areaGrid) return;
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = img.width;
    canvas.height = img.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, img.width, img.height).data;
    areaGrid = { scene: id, scale: info.areaGrid.scale, width: img.width, height: img.height, data };
  };
  img.src = info.areaGrid.file;
}

async function refreshAreaNames() {
  try {
    areaNames = await (await fetch(dataUrl("api/areas", `areas/${current.id}.json`), { cache: "no-store" })).json();
  } catch (e) { /* keep what we have */ }
}
liveInterval(refreshAreaNames, 60000);

function areaIndexAt(px, py) {
  if (!areaGrid || !current || areaGrid.scene !== current.id) return 0;
  const gx = Math.floor(px / areaGrid.scale);
  const gy = Math.floor(py / areaGrid.scale);
  if (gx < 0 || gy < 0 || gx >= areaGrid.width || gy >= areaGrid.height) return 0;
  return areaGrid.data[(gy * areaGrid.width + gx) * 4];   // grayscale: red = index
}

function renderLocation(px, py) {
  const index = calibrated() ? areaIndexAt(px, py) : 0;
  const block = index && areaNames.indexToBlock ? areaNames.indexToBlock[index] : null;
  // Names and regions come from the game's config (mapdata), with what the live map showed as fallback
  const info = block ? (markData.blocks || {})[block] : null;
  const sub = (info && info.name) || (block && areaNames.blocks ? areaNames.blocks[block] : null);
  const region = (info && info.region) || (index && areaNames.mainRegion);
  $("region").textContent = region || (current ? current.info.name : "-");
  const percent = info ? regionCompletion(info.regionId) : null;
  $("region-percent").textContent = percent == null ? "" : `${percent}%`;
  $("subarea").textContent = sub || "";
  locationBlock = info ? block : null;
}
let locationBlock = null;   // small area you're in, opened by clicking its name
$("subarea").onclick = () => { if (locationBlock) openArea(locationBlock); };

// Coordinates as the game prints them: whole metres, four digits, sign kept ("-0626, 1693")
function gameCoord(value) {
  const metres = Math.round(value);
  return (metres < 0 ? "-" : "") + String(Math.abs(metres)).padStart(4, "0");
}

// Your position as the game writes it: counted from the world the scene is laid out in (Whisperwake Isles
// has its own origin, placed next to Idyll)
function gameCoords(pos) {
  const [offsetX, offsetZ] = (maps[state.scene && state.scene.mapId] || {}).worldOffset || [0, 0];
  return `${gameCoord(pos.x + offsetX)}, ${gameCoord(pos.z + offsetZ)}`;
}

$("copy").onclick = async () => {
  if (!state || !state.pos) return;
  const text = gameCoords(state.pos);
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    // Plain http on another device: clipboard API is unavailable, fall back to a hidden textarea
    const t = document.createElement("textarea");
    t.value = text;
    document.body.appendChild(t);
    t.select();
    document.execCommand("copy");
    t.remove();
  }
  $("copy").classList.add("done");
  setTimeout(() => $("copy").classList.remove("done"), 1200);
};

// ---- state polling ----
async function poll() {
  try {
    const res = await fetch(apiUrl("api/state"), { cache: "no-store" });
    state = await res.json();
    render();
  } catch (e) {
    setStatus("Server not reachable", false);
  }
  setTimeout(poll, POLL_MS);
}

function setStatus(text, ok) {
  $("status").textContent = text;
  $("status").classList.toggle("ok", !!ok);
}

function render() {
  if (state.status !== "ok") {
    setStatus(state.message || state.status, false);
    return;
  }
  if (state.scene) showScene(state.scene);
  const { x, y, z } = state.pos;
  $("coords").textContent = gameCoords(state.pos);
  $("height").textContent = heightText(y);
  if (!current) return;
  if (state.scene && !maps[state.scene.mapId]) {   // an instance or event place the game has no map for
    setStatus("No map for this place", false);
    if (map.hasLayer(player)) map.removeLayer(player);
    return;
  }
  const auto = state.calibration && state.calibration.source === "auto";
  $("calibrate").style.display = auto ? "none" : "";
  if (!calibrated()) {
    setStatus(state.mapPos
      ? "Calibrating from the game's minimap: walk about 25 m"
      : "Waiting for the game's minimap to calibrate (or use Calibrate)", false);
    if (map.hasLayer(player)) map.removeLayer(player);
    return;
  }
  const source = state.calibration.source;
  setStatus(source === "manual" ? "Live (manual calibration)" : source === "config" ? "Live (game map data)" : "Live", true);
  if (marksWaitingForPosition && !markData.fit) drawMarks();   // marks placed by the calibration
  const [px, py] = worldToPixel(x, z);
  renderLocation(px, py);
  playerGlide.to(current.id, px, py, screenAngle(state.heading));
  if (!map.hasLayer(player)) player.setLatLng(toLatLng(px, py)).addTo(map);
  renderRoute();
}

// Facing in degrees on screen: the world forward vector (heading in degrees, as the game reports it) through
// the calibration
function screenAngle(heading) {
  const h = (heading * Math.PI) / 180;
  const dx = state.calibration.x[0] * Math.sin(h);
  const dy = state.calibration.z[0] * Math.cos(h);
  return (Math.atan2(dx, -dy) * 180) / Math.PI;
}

// ---- arrow glide: each reading starts a slide from where the arrow is drawn now to the new spot, lasting
// about one reading interval, so arrows move every frame instead of jumping at each reading ----
const SNAP_PIXELS = 150;     // a jump this far in one reading (teleport, new map) moves straight there
let mapPaused = false;       // the collection covers the map: no arrow or map moves until it closes

// The arrow's angle keeps counting past 360 so a turn always takes the short way round
// (359 to 1 turns 2 degrees on, not 358 back)
const shortTurn = (from, deg) => from + ((((deg - from) % 360) + 540) % 360) - 180;

class Glider {
  constructor(maxMs) {
    this.maxMs = maxMs;      // longest slide: about the slowest reading interval
    this.slide = null;       // { mapId, from: [px, py, angle], to: [px, py, angle], start, ms }
    this.last = 0;
  }

  // [px, py, angle] drawn at `now` on map `mapId`, or null (nothing read on that map yet)
  at(now, mapId) {
    const g = this.slide;
    if (!g || g.mapId !== mapId) return null;
    const t = Math.min(1, (now - g.start) / g.ms);
    return g.from.map((v, i) => v + (g.to[i] - v) * t);
  }

  to(mapId, px, py, deg) {
    const now = performance.now();
    const ms = Math.min(this.maxMs, Math.max(100, now - this.last));   // the time readings actually take
    this.last = now;
    const drawn = this.at(now, mapId);
    const far = !drawn || Math.hypot(px - drawn[0], py - drawn[1]) > SNAP_PIXELS;
    const angle = drawn ? shortTurn(drawn[2], deg) : deg;
    this.slide = { mapId, from: far ? [px, py, angle] : drawn, to: [px, py, angle], start: now, ms };
  }
}

// Moves a marker to its glider's spot and turns its arrow (the marker's svg.arrow, else its svg)
function drawGlide(marker, glider, now) {
  const at = current && glider.at(now, current.id);
  if (!at || !map.hasLayer(marker)) return null;
  const ll = toLatLng(at[0], at[1]);
  marker.setLatLng(ll);
  const element = marker.getElement();
  const svg = element && (element.querySelector("svg.arrow") || element.querySelector("svg"));
  if (svg) svg.style.transform = `rotate(${at[2]}deg)`;
  return ll;
}

const playerGlide = new Glider(500);   // your position comes 5 times a second
function drawArrow(now) {
  requestAnimationFrame(drawArrow);
  if (mapPaused) return;
  const ll = drawGlide(player, playerGlide, now);
  if (ll && follow && !picking && !map._animatingZoom) map.panTo(ll, { animate: false, noMoveStart: true });
}
requestAnimationFrame(drawArrow);

// ---- zoom bar on the left, like the game's map ----
function syncZoomBar() {
  const range = $("zoom-range");
  range.min = map.getMinZoom();
  range.max = map.getMaxZoom();
  range.value = map.getZoom();
}
map.on("zoom zoomlevelschange", syncZoomBar);
$("zoom-range").oninput = (e) => map.setZoom(Number(e.target.value), { animate: false });
$("zoom-in").onclick = () => map.zoomIn(0.5);
$("zoom-out").onclick = () => map.zoomOut(0.5);

// ---- follow (Google Maps style: dragging stops it, the button resumes it at the current zoom) ----
function setFollow(on) {
  follow = on;
  $("center").classList.toggle("on", on);
  if (on && map.hasLayer(player)) map.panTo(player.getLatLng(), { animate: true });
}
$("center").onclick = () => setFollow(true);
map.on("dragstart", () => setFollow(false));

// ---- fit: the landmass you're on (or the main one) fills the screen ----
// Fit: the landmass you're on (or the main one) fills the screen. Zooming out stops at that view
const FIT_PADDING = [12, 12];
function fitBounds() {
  const regions = current.info.regions || [];
  let box = [0, 0, current.info.width, current.info.height];
  if (regions.length) {
    box = regions[0];
    if (calibrated() && state.pos) {
      const [px, py] = worldToPixel(state.pos.x, state.pos.z);
      box = regions.find((r) => px >= r[0] && px <= r[2] && py >= r[1] && py <= r[3]) || box;
    }
  }
  return L.latLngBounds(toLatLng(box[0], box[3]), toLatLng(box[2], box[1]));
}

function limitZoomOut() {
  if (!current) return;
  map.setMinZoom(map.getBoundsZoom(fitBounds(), false, L.point(FIT_PADDING).multiplyBy(2)));
}
map.on("resize", limitZoomOut);

$("fit").onclick = () => fitView();

function fitView() {
  if (!current) return;
  setFollow(false);
  limitZoomOut();
  map.fitBounds(fitBounds(), { padding: FIT_PADDING });
}

// ---- route mirrored from the game's guide line ----
const routeLine = L.polyline([], { color: "#4da3ff", weight: 5, opacity: 0.9, className: "route-line" }).addTo(map);
const routeDone = L.polyline([], { color: "#93a4b8", weight: 4, opacity: 0.5, dashArray: "4 8" }).addTo(map);

const OFF_ROUTE_METRES = 40;   // further than this from the path: you still have to get to it
const routeMarks = L.layerGroup().addTo(map);
let routeKey = null;

function routeIcon(cls, html) {
  return L.divIcon({ className: `route-mark ${cls}`, iconSize: [28, 28], iconAnchor: [14, 14], html });
}

// Closest point on the path to (x, z): segment index, projected point and distance
function nearestOnPath(points, x, z) {
  let best = { i: 0, p: points[0], d: Infinity };
  for (let i = 0; i < points.length - 1; i++) {
    const [ax, az] = points[i];
    const [bx, bz] = points[i + 1];
    const len2 = (bx - ax) ** 2 + (bz - az) ** 2 || 1;
    const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (z - az) * (bz - az)) / len2));
    const p = [ax + t * (bx - ax), az + t * (bz - az)];
    const d = Math.hypot(x - p[0], z - p[1]);
    if (d < best.d) best = { i, p, d };
  }
  return best;
}

function pathLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  return total;
}

function renderRoute() {
  const route = state.route;
  if (!route || !route.points || route.points.length < 2 || !calibrated()) {
    routeLine.setLatLngs([]);
    routeDone.setLatLngs([]);
    routeMarks.clearLayers();
    routeKey = null;
    $("route").textContent = "";
    return;
  }
  const ll = (p) => toLatLng(...worldToPixel(p[0], p[1]));
  const { x, z } = state.pos;
  const near = nearestOnPath(route.points, x, z);
  const onRoute = near.d <= OFF_ROUTE_METRES;

  if (onRoute) {
    // Walked part greyed out, remaining part from your position
    routeDone.setLatLngs([...route.points.slice(0, near.i + 1), near.p].map(ll));
    routeLine.setLatLngs([player.getLatLng(), ll(near.p), ...route.points.slice(near.i + 1).map(ll)]);
    const left = pathLength([near.p, ...route.points.slice(near.i + 1)]);
    $("route").textContent = `Route  ${left.toFixed(0)} m left`;
  } else {
    routeDone.setLatLngs([]);
    routeLine.setLatLngs(route.points.map(ll));
    const walk = pathLength(route.points).toFixed(0);
    $("route").textContent = route.start && route.start.teleport
      ? `Teleport to the marked waypoint, then ${walk} m`
      : `Head to the route start, then ${walk} m`;
  }

  // Start (teleport waypoint) and end markers, redrawn only when the route changes
  const key = `${route.trackId}|${route.points.length}|${route.end}`;
  if (key !== routeKey) {
    routeKey = key;
    routeMarks.clearLayers();
    if (route.start && route.start.teleport) {
      L.marker(ll(route.start.pos), { icon: routeIcon("start", "&#x2726;"), title: "Teleport here" }).addTo(routeMarks);
    }
    L.marker(ll(route.end), { icon: routeIcon("end", "&#x2691;"), title: "Target" }).addTo(routeMarks);
  }
}

// ---- cursor readout ----
map.on("mousemove", (e) => {
  if (!current || !calibrated()) return;
  const p = toPixel(e.latlng);
  const [wx, wz] = pixelToWorld(p.x, p.y);
  $("cursor").textContent = `Cursor  X ${wx.toFixed(0)}  Z ${wz.toFixed(0)}`;
  if (OFFLINE) {   // no player: the location panel follows the cursor instead
    renderLocation(p.x, p.y);
    $("coords").textContent = gameCoords({ x: wx, z: wz });
  }
});

// ---- calibration ----
function calibText() {
  const c = state && state.calibration;
  const n = (c && c.points.length) || 0;
  // rms error is in map pixels; the scale (px per metre) turns it into metres
  const accuracy = c && c.error != null && c.x ? ` Accuracy about ±${(c.error / Math.abs(c.x[0])).toFixed(0)} m.` : "";
  $("calib-text").textContent =
    `Open the in-game map, zoom in on your arrow, then click the same spot here. `
    + `Use places far apart. Points on this map: ${n}.${accuracy}`;
}

function drawCalibDots() {
  calibDots.clearLayers();
  if (!current || !state || !state.calibration) return;
  for (const p of state.calibration.points) {
    L.marker(toLatLng(p.map[0], p.map[1]), {
      icon: L.divIcon({ className: "calib-dot", iconSize: [12, 12] }),
      interactive: false,
    }).addTo(calibDots);
  }
}

async function calibPost(action, body) {
  const res = await fetch(apiUrl(`api/calibration/${action}`), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const result = await res.json();
  if (result.error) { alert(result.error); return; }
  state.calibration = result;
  drawCalibDots();
  calibText();
}

$("calibrate").onclick = () => {
  picking = true;
  document.body.classList.add("picking");
  $("calib").classList.remove("hidden");
  calibText();
};
$("calib-done").onclick = () => {
  picking = false;
  document.body.classList.remove("picking");
  $("calib").classList.add("hidden");
};
$("calib-undo").onclick = () => calibPost("undo");
$("calib-clear").onclick = () => { if (confirm("Clear all calibration points for this map?")) calibPost("clear"); };
map.on("click", (e) => {
  if (!picking || !current) return;
  const p = toPixel(e.latlng);
  calibPost("add", { map: [p.x, p.y] });
});

// ---- marks: every mark of the scene from the game's config, with live status from the game's map ----
const TYPE_BLOOM = 10406, TYPE_ALPHA = 10404, TYPE_OMEGA = 10304, TYPE_PATHFINDER = 10510, TYPE_CUSTOM = 101;
const FAR_TYPES = new Set([TYPE_BLOOM, TYPE_ALPHA, TYPE_OMEGA, 2000, 10407, 33]);   // shown at any zoom
const BIG_TYPES = new Set([TYPE_BLOOM, TYPE_ALPHA, TYPE_OMEGA]);
const BOSS_FRAMES = { [TYPE_ALPHA]: "alpha", [TYPE_OMEGA]: "omega" };   // portrait in a horned diamond
const AREAS = "areas";                 // filter key of the area labels and borders
const DETAIL_ZOOM_FROM_MAX = 3;        // every other mark shows from maxZoom - 3
const AREA_LABEL_ZOOM_FROM_MAX = 3.5;  // area names overlap further out
const STATUS_LOCKED = 1, STATUS_DONE = 3;
// Icons the game swaps for an "_Unknown" version while the mark is locked
const HAS_UNKNOWN_ICON = /MarkPoint_(Transmit|Temple|CampingCar|Camp|LeylineTree|LeylineTree_Water|Challenge_TimeLimited|ThemeDecryption|RuneDecryption|VersatileDecryption)\.png$/;
const markLayers = {};                 // type id (or AREAS) -> L.layerGroup
let markData = { marks: [], types: {}, groups: [], blocks: {}, areas: [] };
let marksWaitingForPosition = false;   // some marks need the calibration, which isn't there yet
const DEFAULT_HIDDEN = ["10506"];      // chests: hundreds of them, off until you ask for them
let hiddenTypes = new Set(JSON.parse(localStorageGet("hiddenTypes") || JSON.stringify(DEFAULT_HIDDEN)));
let showCompleted = localStorageGet("showCompleted") !== "false";   // like the game's "Show Completed", on by default

function localStorageGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function localStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
}

async function loadMarks() {
  try {
    markData = await (await fetch(dataUrl("api/marks", `marks/${current.id}.json`), { cache: "no-store" })).json();
  } catch (e) {
    return;
  }
  if (OFFLINE) {   // the profile's config came with the marks
    account = maxedAccount(markData.profile || {});
    renderProfile();
  }
  drawMarks();
  renderAreaPanel();
  renderTracking();
}
liveInterval(loadMarks, 60000);

// Map pixel of a mark: the game's own map position, else its world position through the fitted transform
function markPixel(mark) {
  if (mark.map) return mark.map;
  if (!mark.world) return null;
  const fit = markData.fit;
  if (fit) return [fit.x[0] * mark.world[0] + fit.x[1], fit.z[0] * mark.world[1] + fit.z[1]];
  return calibrated() ? worldToPixel(mark.world[0], mark.world[1]) : null;
}

function typeOf(mark) {
  return markData.types[mark.type] || {};
}

// Your own marks use the icon picked in game: UI_Icon_Mark01..11, markIconIndex counts from 0
const customMarkIcon = (index) => `UI_Icon_Mark${String(index + 1).padStart(2, "0")}.png`;

function iconFor(mark) {
  const icon = mark.type === TYPE_CUSTOM && mark.iconIndex != null ? customMarkIcon(mark.iconIndex) : typeOf(mark).icon;
  if (!icon) return null;
  if (mark.status === STATUS_LOCKED && HAS_UNKNOWN_ICON.test(icon)) return icon.replace(".png", "_Unknown.png");
  // Pathfinder fights: pink star while open, plain once beaten
  if (mark.type === TYPE_PATHFINDER && mark.status !== STATUS_DONE) return icon.replace("_Normal.png", "_Twice.png");
  return icon;
}

function markClasses(mark) {
  return ["mark", BIG_TYPES.has(mark.type) ? "big" : "", mark.status === STATUS_LOCKED ? "locked" : "",
    mark.status === STATUS_DONE ? "done" : "", mark.inactive ? "inactive" : "",
    mark.note ? "conditional" : ""].join(" ");   // only there sometimes (an egg after a meteor, a puzzle's reward...)
}

// 44 x 44 box: diamond (22,11)-(37,26)-(22,41)-(7,26), horns rising from its upper edges
const BOSS_FRAME_SVG = '<svg class="frame" viewBox="0 0 44 44">'
  + '<path class="horns" d="M15.5 18.5 C11 15 10 10 11.5 4 C13 10 15.5 12.5 19 14.5 M28.5 18.5 C33 15 34 10 32.5 4 C31 10 28.5 12.5 25 14.5"/>'
  + '<path class="diamond" d="M22 11 L37 26 L22 41 L7 26 Z"/></svg>';
const DOT = "<i></i>";

function markHtml(mark) {
  const icon = iconFor(mark);
  if (BOSS_FRAMES[mark.type] && mark.portrait) {
    return `<div class="boss ${BOSS_FRAMES[mark.type]}"><img class="portrait" src="icons/${mark.portrait}" alt=""`
      + ` onerror="this.src='icons/${icon}'">${BOSS_FRAME_SVG}</div>`;
  }
  return icon ? `<img src="icons/${icon}" alt="" onerror="this.outerHTML='${DOT}'">` : DOT;
}

// Size and centring come from CSS so icons can grow with the zoom (see scaleMarks)
function markIcon(mark) {
  return L.divIcon({ className: markClasses(mark), iconSize: null, html: markHtml(mark) });
}

// "Height 147", like the location panel; "" when unknown
const heightText = (y) => (y == null ? "" : `Height ${Number(y).toFixed(0)}`);

function markTooltip(mark) {
  const type = typeOf(mark).name || "Mark";
  const status = mark.status === STATUS_LOCKED ? "locked" : mark.status === STATUS_DONE ? "completed"
    : mark.inactive ? "not active today" : "";
  const where = mark.world ? `${mark.world[0].toFixed(0)}, ${mark.world[1].toFixed(0)}` : "";
  const head = mark.title ? `<b>${mark.title}</b> · ${type}` : `<b>${type}</b>`;
  return [head, status, mark.note, where, heightText(mark.height)].filter(Boolean).join(" · ");
}

// Icons grow as you zoom in (like the game's map), from the zoom where all marks show
const MARK_SCALE_PER_ZOOM = 1.3, MARK_SCALE_MIN = 0.8, MARK_SCALE_MAX = 2.2;
function scaleMarks() {
  if (!current) return;
  const steps = map.getZoom() - (current.info.maxZoom - DETAIL_ZOOM_FROM_MAX);
  const scale = Math.min(MARK_SCALE_MAX, Math.max(MARK_SCALE_MIN, MARK_SCALE_PER_ZOOM ** steps));
  map.getContainer().style.setProperty("--mark-scale", scale.toFixed(3));
}
map.on("zoom", scaleMarks);

// Name, level range and (once read from the game) completion, like the game's map labels
function areaLabelHtml(area) {
  const block = markData.blocks[area.id] || {};
  const level = levelRange(block);
  const completion = areaCompletion(area.id);
  const details = [level, completion != null ? `${completion}%` : ""].filter(Boolean).join(" · ");
  return `<div>${weatherIcon(area.id, "weather")}<b>${block.name || ""}</b>${exclusiveIcon(area.id)}`
    + `${details ? `<span>${details}</span>` : ""}</div>`;
}

// The game scales Breezy Plains' level ranges with your star title (formula DYNAMIC_LV_CALC_FUNC6):
// shown = max(L, round(w L + (1 - w) T)) + 1. w and T per star title are fitted to the game's own labels
// (star title 8 matches every Breezy Plains label exactly); unmeasured star titles show the base ranges.
const LEVEL_SCALING = { region: 300001, weight: 0.18, targetByStarTitle: { 7: 49, 8: 54 } };

function shownLevel(block, level) {
  const target = LEVEL_SCALING.targetByStarTitle[handbook.starTitle];
  if (block.regionId !== LEVEL_SCALING.region || target == null || level == null) return level;
  return Math.max(level, Math.round(LEVEL_SCALING.weight * level + (1 - LEVEL_SCALING.weight) * target)) + 1;
}

function levelRange(block, prefix = "Lv.") {   // the area panel writes "Lv. ", map labels "Lv."
  const min = shownLevel(block, block.minLevel), max = shownLevel(block, block.maxLevel);
  return min ? `${prefix}${min}${max && max !== min ? `~${max}` : ""}` : "";
}

function layerFor(key) {
  if (!markLayers[key]) markLayers[key] = L.layerGroup();
  return markLayers[key];
}

function drawMarks() {
  for (const layer of Object.values(markLayers)) layer.clearLayers();
  if (!current) return;
  const counts = {};
  marksWaitingForPosition = false;
  for (const mark of markData.marks) {
    if (!showCompleted && mark.status === STATUS_DONE) continue;
    const px = markPixel(mark);
    if (!px) { marksWaitingForPosition = true; continue; }
    const marker = L.marker(toLatLng(px[0], px[1]), { icon: markIcon(mark), keyboard: false });
    marker.bindTooltip(markTooltip(mark), { direction: "top", offset: [0, -12] });
    marker.addTo(layerFor(mark.type));
    counts[mark.type] = (counts[mark.type] || 0) + 1;
  }
  for (const area of markData.areas || []) {
    const px = markPixel(area);
    if (!px || !(markData.blocks[area.id] || {}).name) continue;
    L.marker(toLatLng(px[0], px[1]), {
      icon: L.divIcon({ className: "area-label", html: areaLabelHtml(area), iconSize: null }), keyboard: false,
    }).on("click", () => openArea(area.id)).addTo(layerFor(AREAS));
    counts[AREAS] = (counts[AREAS] || 0) + 1;
  }
  renderFilters(counts);
  updateMarkVisibility();
}

// ---- filters: like the game's map filter, one toggle button per mark type in its group ----
function swatch(icon) {
  return icon ? `<img src="icons/${icon}" alt="" onerror="this.outerHTML='${DOT}'">` : DOT;
}

// A type's button: icon and name, then the count in its own segment
function filterButton(key, label, icon, count) {
  return `<button class="filter-button" data-types="${key}" data-name="${label.toLowerCase()}">`
    + `<span class="filter-main"><span class="swatch">${swatch(icon)}</span><span class="filter-name">${label}</span></span>`
    + `<span class="filter-count">${count}</span></button>`;
}

// A group's heading: round check for the whole group, name, total
function filterHeading(key, label, count) {
  return `<label class="filter-heading round-check" data-types="${key}"><input type="checkbox" data-types="${key}"><i></i>`
    + `<span>${label}</span><span class="filter-count">${count}</span></label>`;
}

// Types of a filter group present in this scene, in the game's order
function groupTypes(group, counts) {
  const [key, , order] = group;
  const rank = (id) => (order.indexOf(+id) + 1) || 999;
  return Object.keys(markData.types)
    .filter((id) => String(markData.types[id].group) === String(key) && counts[id])
    .sort((a, b) => rank(a) - rank(b));
}

function renderFilters(counts) {
  const sample = {};
  for (const m of markData.marks) if (!sample[m.type] && iconFor(m)) sample[m.type] = iconFor(m);
  let html = "";
  for (const group of markData.groups || []) {
    const ids = groupTypes(group, counts);
    if (!ids.length) continue;
    const total = ids.reduce((n, id) => n + counts[id], 0);
    html += '<div class="filter-group">' + filterHeading(ids.join(","), group[1], total) + '<div class="filter-grid">'
      + ids.map((id) => filterButton(id, markData.types[id].name, sample[id] || markData.types[id].icon, counts[id])).join("")
      + "</div></div>";
  }
  $("filter-list").innerHTML = html || '<p class="filter-empty">No map data yet: run tools/build_mapdata.py.</p>';
  // Areas stay pinned above the scrolling list
  $("filter-fixed").innerHTML = counts[AREAS] ? `<div class="filter-grid">${filterButton(AREAS, "Areas", null, counts[AREAS])}</div>` : "";
  for (const box of $("filters").querySelectorAll(".filter-heading input")) {
    box.onchange = () => setTypesShown(box.dataset.types.split(","), box.checked);
  }
  for (const button of $("filters").querySelectorAll(".filter-button")) {
    button.onclick = () => setTypesShown([button.dataset.types], hiddenTypes.has(button.dataset.types));
  }
  syncFilterBoxes();
  applySearch();
}

// Keyword search: keeps the matching buttons and the groups that still have some
function applySearch() {
  const words = $("filter-search").value.trim().toLowerCase();
  for (const group of $("filter-list").querySelectorAll(".filter-group")) {
    let any = false;
    for (const button of group.querySelectorAll(".filter-button")) {
      const match = !words || button.dataset.name.includes(words);
      button.hidden = !match;
      any = any || match;
    }
    group.hidden = !any;
  }
}
$("filter-search").oninput = applySearch;

$("show-completed").checked = showCompleted;
$("show-completed").onchange = (e) => {
  showCompleted = e.target.checked;
  localStorageSet("showCompleted", String(showCompleted));
  drawMarks();
};

$("filters-reset").onclick = () => {
  hiddenTypes = new Set(DEFAULT_HIDDEN);
  localStorageSet("hiddenTypes", JSON.stringify([...hiddenTypes]));
  $("filter-search").value = "";
  $("show-completed").checked = showCompleted = true;
  localStorageSet("showCompleted", "true");
  drawMarks();
};

// Buttons look pressed while their type is shown; a group's check is on when all its types are shown,
// half-on when only some are
function syncFilterBoxes() {
  for (const button of $("filters").querySelectorAll(".filter-button")) {
    button.classList.toggle("on", !hiddenTypes.has(button.dataset.types));
  }
  for (const box of $("filters").querySelectorAll(".filter-heading input")) {
    const ids = box.dataset.types.split(",");
    const shown = ids.filter((id) => !hiddenTypes.has(id)).length;
    box.checked = shown === ids.length;
    box.indeterminate = shown > 0 && shown < ids.length;
  }
}

function setTypesShown(ids, shown) {
  for (const id of ids) shown ? hiddenTypes.delete(id) : hiddenTypes.add(id);
  localStorageSet("hiddenTypes", JSON.stringify([...hiddenTypes]));
  syncFilterBoxes();
  updateMarkVisibility();
}

function setAllShown(shown) {
  setTypesShown([...$("filters").querySelectorAll(".filter-button")].map((b) => b.dataset.types), shown);
}
$("filters-all").onclick = () => setAllShown(true);
$("filters-none").onclick = () => setAllShown(false);

function updateMarkVisibility() {
  if (!current) return;
  const detailed = map.getZoom() >= current.info.maxZoom - DETAIL_ZOOM_FROM_MAX;
  for (const [key, layer] of Object.entries(markLayers)) {
    const near = key === AREAS ? map.getZoom() >= current.info.maxZoom - AREA_LABEL_ZOOM_FROM_MAX : detailed || FAR_TYPES.has(+key);
    const show = !hiddenTypes.has(key) && near;
    if (show && !map.hasLayer(layer)) layer.addTo(map);
    if (!show && map.hasLayer(layer)) map.removeLayer(layer);
  }
  if (borders) {
    const show = !hiddenTypes.has(AREAS);
    if (show && !map.hasLayer(borders)) borders.addTo(map);
    if (!show && map.hasLayer(borders)) map.removeLayer(borders);
  }
  scaleMarks();
}
map.on("zoomend", updateMarkVisibility);
$("filters-toggle").onclick = () => $("filters").classList.toggle("collapsed");

// ---- Aniimo: area completion, who lives where (seen / caught), and tracking one like the game does ----
const STATE_KNOWN = 1, STATE_CAUGHT = 2;     // handbook stateMask bits
const HANDBOOK_POLL_MS = 5000;              // the server re-reads the handbook every 5 s
const SPAWN_CELL = 40;                        // map pixels; spawn points are drawn as one square per cell
const TIER_NAMES = ["bronze", "silver", "gold"];   // area completion tiers (thresholds per area, from the game)
// Weather an Aniimo needs in an area: the badge on its portrait. Aurora stays a "?" until you've seen it.
const CONDITION_ICONS = { snow: "UI_Img_Weather_Snow.png", rain: "UI_Img_Weather_Rainy.png",
  thunder: "UI_Img_Weather_Thunderstorm.png", storm: "UI_Img_Weather_Windstorm.png", aurora: "UI_Img_Weather_Rainbow.png" };
let handbook = { states: {}, blockCaught: {}, tracked: null };
let handbookRead = false;                      // the first /api/handbook has arrived
let weather = {};                             // block id -> weather id, live from the game
let timePeriod = null;                        // 1 Morning, 2 Day, 3 Dusk, 4 Night
const TIME_PERIODS = { 1: "Morning", 2: "Day", 3: "Dusk", 4: "Night" };
const NIGHT = 4, SUNNY = 1;
// Weather that brings out an area's weather-only Aniimo (its condition in the area's list)
const CONDITION_WEATHERS = { rain: [2, 12], thunder: [4], snow: [3, 13], storm: [5] };
const EXCLUSIVE_ICON = "UI_Map_Icon_Weather5.png";   // the map's blue wink

// The game winks when a weather-exclusive Aniimo is out: here, when the area's current weather is one
// that one of its Aniimo needs
function exclusiveOut(blockId) {
  const conditions = Object.values((markData.blocks[blockId] || {}).conditions || {});
  return conditions.some((c) => weatherMatches(c, blockId));
}

// Is the area's current weather the one this condition needs? (Aurora isn't read yet: never matches)
function weatherMatches(condition, blockId) {
  return (CONDITION_WEATHERS[condition] || []).includes(+weather[blockId]);
}
let pickedTrack = new URLSearchParams(location.search).get("track");   // Aniimo picked here (?track=1017100); else the game's pick
let trackClosed = null;                       // the game's pick you dismissed here
let wildNearby = [];                           // /api/wild (wild.js): the wild Aniimo loaded around you
let openAreaId = new URLSearchParams(location.search).get("area");   // ?area=20800 opens that area's panel
const spawnLayer = L.layerGroup().addTo(map);

async function getJson(url) {
  return (await fetch(url, { cache: "no-store" })).json();
}

async function loadHandbook() {
  try {
    const [book, sky, me, quests] = await Promise.all(["api/handbook", "api/weather", "api/profile", "api/quests"]
      .map((path) => getJson(apiUrl(path))));
    trackedQuests = quests;
    // A new pick in the game shows again if you had dismissed the game's pick here; one you picked here stays
    if (handbookRead && book.tracked && String(book.tracked) !== String(handbook.tracked)) trackClosed = null;
    handbookRead = true;
    handbook = Object.assign({ states: {}, blockCaught: {}, tracked: null }, book);
    account = me;
    weather = sky.weather || {};
    timePeriod = sky.timePeriod || null;
  } catch (e) {
    return;
  }
  if (markData.marks.length) drawMarks();   // area labels show completion
  renderAreaPanel();
  renderTracking();
  renderTimeOfDay();
  renderProfile();
  renderQuests();
}
liveInterval(loadHandbook, HANDBOOK_POLL_MS);

// ---- player profile (top left): picture, name, level, Primegy and the server's daily reset ----
let account = {};                              // /api/profile, live from the game
const NO_NEXT_LEVEL_EXP = 99999999;           // player_level_data's XP for the level after the last one

// The offline map's player, as an overview of the game's maximums: top rank and level, full Primegy, every
// talent and Branch maxed, three empty hatch slots, with the game's own picture (no Daily Activity: it's
// about today)
function maxedAccount(profile) {
  const levels = Object.entries(profile.levelExp || {}).filter(([, exp]) => exp < NO_NEXT_LEVEL_EXP).map(([l]) => +l);
  const branches = Object.fromEntries(Object.entries(profile.branches || {}).map(([id, b]) =>
    [id, { infused: (b.points || []).slice(-1)[0] || 0, level: (b.points || []).length }]));
  const talents = Object.fromEntries(Object.entries((profile.talents || {}).talents || {}).map(([id, t]) => [id, t.costs.length]));
  const limit = (profile.primegy || {}).limit;
  return { name: OFFLINE_PLAYER, level: levels.length ? Math.max(...levels) : null, exp: 0,
           rank: Math.max(...Object.keys(profile.ranks || { 0: 1 }).map(Number)),
           headIcon: Math.min(...Object.keys(profile.headIcons || { 0: 1 }).map(Number)), headFrame: null,
           primegy: { value: limit, max: limit, nextAt: null }, eggs: [], branches, talents, money: {} };
}
const OFFLINE_PLAYER = "Pathfinder";          // what the game calls the player
const DAY = 86400, WEEK = 7 * DAY;            // seconds
const RESET_HOUR = 4;                         // servers reset at 4 AM their own time
// Server regions by their time zone (hours from UTC), as the game's server list groups them
const SERVER_REGIONS = { 8: "Asia", 0: "Europe", "-4": "Americas", "-5": "Americas" };

// The first moment after now that is `start` plus a whole number of periods
function nextAfter(start, period, now) {
  return start + Math.max(1, Math.ceil((now - start) / period)) * period;
}

// The server's next daily reset: from the last one it ran (live), else from its region's day start (config)
function nextDailyReset(now) {
  const start = account.dayReset || (markData.profile || {}).serverDayStart?.[account.serverArea];
  return start ? nextAfter(start, DAY, now) : null;
}

// The server region and its time zone, from when its day starts (its 4 AM): {name, zone}
function serverRegion() {
  const start = (markData.profile || {}).serverDayStart?.[account.serverArea];
  if (!start) return null;
  let hours = RESET_HOUR - (((start % DAY) + DAY) % DAY) / 3600;
  if (hours <= -12) hours += 24;
  const zone = `UTC${hours >= 0 ? "+" : "−"}${Math.abs(hours)}`;
  return { name: SERVER_REGIONS[hours] || zone, zone };
}

// Primegy now: the game sends the count and when the next point comes; count on from there until full
function primegyNow(now) {
  const energy = account.primegy || {};
  const info = (markData.profile || {}).primegy || {};
  let { value, nextAt } = energy;
  if (value == null) return null;
  const seconds = info.seconds || 360, gain = info.gain || 1, max = energy.max || Infinity;
  if (nextAt && value < max && now >= nextAt) {
    const steps = 1 + Math.floor((now - nextAt) / seconds);
    value = Math.min(max, value + steps * gain);
    nextAt += steps * seconds;
  }
  const full = value >= max;
  return { value, max: energy.max, nextAt: full ? null : nextAt,
           fullAt: full || !nextAt ? null : nextAt + (Math.ceil((max - value) / gain) - 1) * seconds };
}

function duration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const d = Math.floor(seconds / DAY), h = Math.floor((seconds % DAY) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}:${String(seconds % 60).padStart(2, "0")}`;
}

function clockTime(unix) {
  return new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function setIcon(img, file) {
  if (file) img.src = `icons/${file}`;
  else img.removeAttribute("src");
}

function renderProfile() {
  const profile = markData.profile || {};
  $("profile").classList.toggle("hidden", !account.name);
  if (!account.name) return;
  setIcon($("profile-icon"), (profile.headIcons || {})[account.headIcon]);
  setIcon($("profile-frame"), (profile.headFrames || {})[account.headFrame]);
  $("profile-name").textContent = account.name;
  $("profile-level").textContent = account.level ? `Lv. ${account.level}` : "";
  const rank = (profile.ranks || {})[account.rank];
  $("profile-rank").classList.toggle("hidden", !rank);
  if (rank) {
    setIcon($("rank-icon"), rank.icon);
    $("rank-name").textContent = rank.name;
  }
  const needed = (profile.levelExp || {})[account.level + 1];   // a level's row holds the XP to reach it
  const maxed = needed >= NO_NEXT_LEVEL_EXP;
  const showExp = needed && account.exp != null && !maxed;
  $("profile-exp").textContent = maxed ? "Max level" : showExp ? `${account.exp.toLocaleString()} / ${needed.toLocaleString()} XP` : "";
  $("exp-fill").style.width = maxed ? "100%" : showExp ? `${Math.min(100, (100 * account.exp) / needed)}%` : "0";
  const primegy = profile.primegy || {};
  setIcon($("primegy-icon"), primegy.icon);
  $("primegy").title = [primegy.name, primegy.desc].filter(Boolean).join("\n");
  tickProfile();
}

// Countdowns move every second between reads
function tickProfile() {
  if (!account.name) return;
  const now = Date.now() / 1000;
  const energy = primegyNow(now);
  $("primegy").classList.toggle("hidden", !energy);
  if (energy) {
    $("primegy-value").textContent = energy.value;
    $("primegy-max").textContent = energy.max ? `/${energy.max}` : "";
    $("primegy").classList.toggle("full", !energy.nextAt);
    $("primegy-next").textContent = energy.nextAt ? `+1 in ${duration(energy.nextAt - now)}` : "Full";
    $("primegy-full").textContent = energy.fullAt
      ? `Full in ${duration(energy.fullAt - now)} (${clockTime(energy.fullAt)})` : "";
  }
  const reset = nextDailyReset(now);
  $("reset").classList.toggle("hidden", !reset);
  if (reset) {
    const region = serverRegion();
    $("reset-in").textContent = `in ${duration(reset - now)}`;
    $("reset-at").textContent = [clockTime(reset), region && region.name].filter(Boolean).join(" · ");
    const weekly = account.weekReset ? nextAfter(account.weekReset, WEEK, now) : null;
    $("reset").title = (region ? `${region.name} server (${region.zone}), resets at ${RESET_HOUR} AM its time\n` : "")
      + `Daily reset ${new Date(reset * 1000).toLocaleString()}`
      + (weekly ? `\nWeekly reset ${new Date(weekly * 1000).toLocaleString()}` : "");
  }
  renderDaily(now, reset);
  renderTalents();
  renderBranches();
  renderEggs(now);
}

setInterval(tickProfile, 1000);

// ---- Daily Activity (a profile row; clicking it opens today's checklist) ----
const TASK_CLAIMED = 3;                      // a task's state once its points are collected
let dailyOpen = new URLSearchParams(location.search).has("daily");   // ?daily opens the checklist

function renderDaily(now, reset) {
  const daily = account.daily;
  const config = (markData.profile || {}).daily || {};
  $("daily").classList.toggle("hidden", !daily);
  if (!daily) return;
  const limit = config.limit || 0;
  const full = limit && daily.score >= limit;
  $("daily").classList.toggle("full", !!full);
  $("daily-score").textContent = limit ? `${daily.score} / ${limit}` : `${daily.score}`;
  $("daily-note").textContent = full ? "Today's points are full" : "";
  $("daily-card").classList.toggle("hidden", !dailyOpen);
  $("profile").classList.toggle("hidden", dailyOpen || !account.name);
  if (!dailyOpen) return;
  $("daily-left").textContent = reset ? `Time remaining: ${duration(reset - now)}` : "";
  renderDailyBar(daily, config);
  // Today's tasks are the ones the game handed out; open ones first, like the game
  const tasks = (config.tasks || []).filter((t) => t.id in daily.tasks)
    .sort((a, b) => (daily.tasks[a.id] === TASK_CLAIMED) - (daily.tasks[b.id] === TASK_CLAIMED));
  setHtml($("daily-tasks"), tasks.map((t) => {
    const claimed = daily.tasks[t.id] === TASK_CLAIMED;
    return `<div class="daily-task ${claimed ? "claimed" : ""}"><b>${t.title || ""}</b>`
      + `<span class="points">+${t.points}</span><span class="desc">${t.desc || ""}</span></div>`;
  }).join("") + (full ? '<p class="daily-full">Today\'s Activity Points are full.</p>' : ""));
}

// The points bar: chests evenly spaced up to the cap, ticked once claimed
function renderDailyBar(daily, config) {
  const limit = config.limit || 1, chests = config.chests || [];
  const fill = Math.min(100, (100 * daily.score) / limit);
  setHtml($("daily-bar"), `<i class="track"></i><i class="fill" style="width:${fill}%"></i>`
    + chests.map((id, i) => {
      const at = Math.round((limit * (i + 1)) / chests.length);
      const state = daily.tasks[id] === TASK_CLAIMED ? "claimed" : daily.score >= at ? "reached" : "";
      return `<i class="chest ${state}" style="left:${(100 * at) / limit}%"><span>${at}</span></i>`;
    }).join("") + `<b class="score">${daily.score}</b>`);
}

// Rewrites an element only when its content changed (these run every second)
function setHtml(element, html) {
  if (element.dataset.html !== html) element.innerHTML = element.dataset.html = html;
}

$("daily").onclick = () => { dailyOpen = true; tickProfile(); };
$("daily-close").onclick = () => { dailyOpen = false; tickProfile(); };

// ---- Skill Points: unspent (Report Cards) and spent per talent tab, like the game's Talents screen ----
function renderTalents() {
  const config = (markData.profile || {}).talents || {};
  const learned = account.talents;
  $("talents").classList.toggle("hidden", !learned || !config.point);
  if (!learned || !config.point) return;
  const spent = { active: 0, passive: 0 };
  for (const [id, level] of Object.entries(learned)) {
    const talent = (config.talents || {})[id];
    if (talent) spent[talent.tab] += talent.costs.slice(0, level).reduce((a, b) => a + b, 0);
  }
  setIcon($("talents-icon"), config.pointIcon);
  // Offline: the Skill Points every talent takes (all learned), split by tab
  $("talents-points").textContent = OFFLINE ? spent.active + spent.passive : (account.money || {})[config.point] || 0;
  $("talents-spent").textContent = `Active ${spent.active} · Passive ${spent.passive}`;
  $("talents").title = OFFLINE ? `${config.pointName || "Skill Points"} it takes to learn every Active Skill and Passive Talent`
    : `${config.pointName || "Skill Points"} not spent yet; spent on Active Skills and Passive Talents`;
}

// ---- Branches: Lumin Amber not infused yet, and points infused toward the Branch's next level ----
function renderBranches() {
  const config = (markData.profile || {}).branches || {};
  setHtml($("branches"), Object.entries(account.branches || {}).map(([id, branch]) => {
    const info = config[id] || {};
    const points = info.points || [];
    const next = points.find((p) => p > branch.infused);
    // Offline: the Lumin Amber the Branch takes in all, nothing about yours
    const amber = OFFLINE ? branch.infused : (account.money || {})[info.amber] || 0;
    const title = OFFLINE ? `${info.name || "Branch"}: ${info.amberName || "Lumin Amber"} it takes to infuse it fully`
      : `${info.name || "Branch"}: ${info.amberName || "Lumin Amber"} not used yet`;
    return `<div class="profile-row" title="${title}">`
      + `<img src="icons/${info.amberIcon}" alt=""><span class="profile-value"><b>${amber}</b><span>Lumin Amber</span></span>`
      + `<span class="profile-note">${OFFLINE ? "Fully infused" : `${branch.infused}${next ? ` / ${next}` : ""} infused`}</span>`
      + `<span class="profile-note">${info.name || ""}</span></div>`;
  }).join(""));
}

// ---- tracked quests: one row per HUD tab with the quest's name; a row opens to its current steps ----
let trackedQuests = {};                       // /api/quests: {tracked: {lore, quests, journey}, active}
const QUEST_TABS = [["lore", "Lore"], ["quests", "Quests"], ["journey", "Journey"]];
const openQuests = new Set();

// Steps of this quest in progress, deepest first (steps can hold steps), with something to show
function questSteps(quest, info, active) {
  const steps = info.steps || {};
  const below = (id) => {
    for (let step = steps[id]; step; step = steps[step.parent]) if (step.parent === quest) return true;
    return false;
  };
  return Object.keys(active || {}).filter((id) => +id !== quest && below(id) && steps[id].visible)
    .map((id) => ({ id, ...steps[id] })).filter((s) => s.objectives.length || s.desc);
}

function questBody(quest, info, active) {
  const name = (info.names || {})[quest] || {};
  const lines = questSteps(quest, info, active).map((step) => {
    const counts = active[step.id] || {};
    const items = step.objectives.map(([id, text, target]) => {
      const done = target != null && (counts[id] || 0) >= target;
      return `<li class="${done ? "done" : ""}">${richText(text)}</li>`;
    }).join("");
    return (step.desc ? `<div class="quest-step">${richText(step.desc)}</div>` : "")
      + (items ? `<ul class="quest-objectives">${items}</ul>` : "");
  }).join("");
  return (name.chapter ? `<div class="quest-chapter">${richText(name.chapter)}</div>` : "") + lines
    + (name.desc ? `<div class="quest-desc">${richText(name.desc)}</div>` : "");
}

function renderQuests() {
  const tracked = trackedQuests.tracked;
  const info = (markData.profile || {}).quests || {};
  $("quests-card").classList.toggle("hidden", !tracked);
  if (!tracked) return;
  setHtml($("quests-card"), QUEST_TABS.map(([tab, label]) => {
    const quest = tracked[tab];
    if (!quest) {
      const icon = (info.tabIcons || {})[tab];
      return `<div class="quest none ${tab}"><div class="quest-head">${icon ? `<img src="icons/${icon}" alt="">` : ""}`
        + `<b>${label}: not tracking</b></div></div>`;
    }
    const name = (info.names || {})[quest] || {};
    return `<div class="quest ${tab} ${openQuests.has(tab) ? "open" : ""}" data-tab="${tab}">`
      + `<button class="quest-head" title="${label}">${name.icon ? `<img src="icons/${name.icon}" alt="">` : ""}`
      + `<b>${richText(name.name || `${label} quest`)}</b><i>${label}</i></button>`
      + `<div class="quest-body">${questBody(quest, info, trackedQuests.active)}</div></div>`;
  }).join(""));
}

$("quests-card").onclick = (e) => {
  const row = e.target.closest(".quest-head") && e.target.closest(".quest[data-tab]");
  if (!row) return;
  const tab = row.dataset.tab;
  if (!openQuests.delete(tab)) openQuests.add(tab);
  row.classList.toggle("open", openQuests.has(tab));
};

// ---- eggs hatching: three slots like the game, the middle one for Perfect eggs only, the sides any egg ----
const MIDDLE_SLOT = 1;

function renderEggs(now) {
  const eggs = account.eggs;
  const info = (markData.profile || {}).eggs || {};
  $("eggs").classList.toggle("hidden", !eggs);
  if (!eggs) return;
  const middle = eggs.find((e) => e.slot === MIDDLE_SLOT), sides = eggs.filter((e) => e.slot !== MIDDLE_SLOT);
  const slots = [sides[0], middle, sides[1]];   // left, middle, right; undefined = empty
  // Built once per set of eggs (rebuilding every second would restart the pictures); the times tick below
  setHtml($("egg-slots"), slots.map((e, i) => {
    const kind = i === 1 ? "perfect" : "";
    if (!e) return `<span class="egg empty ${kind}" title="${i === 1 ? "Perfect eggs only" : "Any egg"}"><i></i><em>Empty</em></span>`;
    const egg = info[e.egg] || {};
    return `<span class="egg ${kind}" title="${egg.name || "Egg"}">`
      + `<img src="icons/${egg.icon}" alt="" onerror="this.style.visibility='hidden'"><em></em></span>`;
  }).join(""));
  slots.forEach((e, i) => {
    if (!e) return;
    const left = e.endAt ? e.endAt - now : null;
    const ready = left != null && left <= 0;
    const cell = $("egg-slots").children[i];
    cell.classList.toggle("ready", ready);
    cell.querySelector("em").textContent = ready ? "Ready" : left != null ? duration(left) : "";
  });
  const next = Math.min(...eggs.map((e) => e.endAt || Infinity));
  $("eggs-next").textContent = next === Infinity ? "" : next <= now ? "Ready to collect" : `next in ${duration(next - now)}`;
}

// The map's weather icons follow the time of day: clear nights show the moon, other weather gets a small moon
function weatherIconFile(weatherId) {
  const info = (markData.weathers || {})[weatherId];
  if (!info) return null;
  return timePeriod === NIGHT && +weatherId === SUNNY ? "UI_Img_Time_Night.png" : info.icon;
}

function renderTimeOfDay() {
  const name = TIME_PERIODS[timePeriod];
  $("daytime").textContent = name || "";
  $("daytime").className = `daytime ${name ? name.toLowerCase() : ""}`;
}

// Caught in this area (what the game's completion counts), else seen anywhere, else unseen
// Caught in this area, else seen anywhere, else unseen
function petState(proto, blockId) {
  if (OFFLINE) return "caught";   // the offline map shows every Aniimo as you'd see it once caught
  if ((handbook.blockCaught[blockId] || []).includes(+proto)) return "caught";
  return (handbook.states[proto] || 0) & STATE_KNOWN ? "seen" : "unseen";
}

// A region's completion as the game shows it: caught over all Aniimo of its small areas together
function regionCompletion(regionId) {
  let caught = 0, total = 0;
  for (const [id, block] of Object.entries(markData.blocks || {})) {
    if (block.regionId !== regionId || !(block.pets || []).length || !handbook.blockCaught[id]) continue;
    total += block.pets.length;
    caught += block.pets.filter((p) => petState(p, id) === "caught").length;
  }
  return total ? Math.round((100 * caught) / total) : null;
}

function areaCompletion(blockId) {
  const pets = (markData.blocks[blockId] || {}).pets || [];
  if (!pets.length || !handbook.blockCaught[blockId]) return null;
  const caught = pets.filter((p) => petState(p, blockId) === "caught").length;
  return Math.round((100 * caught) / pets.length);
}

function weatherIcon(blockId, cls) {
  const id = weather[blockId], file = weatherIconFile(id);
  if (!file) return "";
  const info = markData.weathers[id];
  const moon = timePeriod === NIGHT && +id !== SUNNY ? '<img class="moon" src="icons/UI_Img_moon_small.png" alt="">' : "";
  return `<span class="${cls}" title="${info.name || ""}${timePeriod === NIGHT ? " (night)" : ""}"><img src="icons/${file}" alt="">${moon}</span>`;
}

// An Aniimo form's name, form and icon: from the map data, else from the collection's data (forms that live
// on no map, added when the collection page loads)
const extraPets = {};
const petInfo = (proto) => (markData.pets || {})[proto] || extraPets[proto] || {};

function petLabel(proto) {
  const pet = petInfo(proto);
  return [pet.name || `Aniimo ${proto}`, pet.form].filter(Boolean).join(" · ");
}

function petIcon(proto, cls) {
  const pet = petInfo(proto);
  return pet.icon ? `<img class="${cls}" src="icons/${pet.icon}" alt="" onerror="this.style.visibility='hidden'">` : "";
}

// The wink after an area's name, like the game's map
function exclusiveIcon(blockId) {
  return exclusiveOut(blockId)
    ? `<img class="exclusive" src="icons/${EXCLUSIVE_ICON}" alt="" title="A weather-exclusive Aniimo is out">` : "";
}

// ---- area panel (right side, like the game's) ----
function openArea(blockId) {
  openAreaId = blockId;
  renderAreaPanel();
}
$("area-close").onclick = () => { openAreaId = null; selectedPet = null; renderAreaPanel(); };

function renderAreaPanel() {
  const block = openAreaId && markData.blocks[openAreaId];
  $("area").classList.toggle("hidden", !block);
  if (!block) return;
  const completion = areaCompletion(openAreaId);
  $("area-name").innerHTML = `${block.name}${exclusiveIcon(openAreaId)}`;
  const sky = (markData.weathers || {})[weather[openAreaId]];
  $("area-weather").innerHTML = sky ? `${weatherIcon(openAreaId, "weather")}<span>${sky.name || ""}</span>` : "";
  $("area-level").textContent = levelRange(block, "Lv. ");
  $("area-percent").textContent = completion == null ? "-" : `${completion}%`;
  renderTiers(block.tiers || [], completion || 0);
  const cells = (block.pets || []).map((proto) => {
    const state = petState(proto, openAreaId);
    const tracked = String(proto) === String(currentTrack());
    const condition = (block.conditions || {})[proto];
    // Like the game: solid once caught here, faded if only seen
    const faded = state === "seen" ? "faded" : "";
    return `<button class="pet ${state} ${faded} ${tracked ? "tracked" : ""}" data-proto="${proto}" title="${petLabel(proto)} (${state}${condition ? `, ${condition}` : ""})">`
      + `<span class="hex">${petIcon(proto, "head")}</span>${conditionBadge(condition, state, weatherMatches(condition, openAreaId))}`
      + `${nearPill(proto)}</button>`;
  });
  $("area-pets").innerHTML = honeycombHtml(honeycombRows(cells));
  for (const button of $("area-pets").querySelectorAll(".pet")) {
    button.onclick = () => { selectedPet = button.dataset.proto; setTrack(button.dataset.proto); };
    button.onmouseenter = () => renderPetDetail(button.dataset.proto);
    button.onmouseleave = () => renderPetDetail(selectedPet);
  }
  renderPetDetail(selectedPet);
}

// ---- the card's bottom section: how to find the Aniimo you point at (or picked) ----
let selectedPet = new URLSearchParams(location.search).get("pet");   // ?pet=1033301 preselects one
const STATE_TEXT = { caught: "Caught here", seen: "Seen", unseen: "Not seen yet" };

// The game's rich text: <sprite name="..."> icons, <color=#..> and <style=..> spans
function richText(text) {
  const escaped = String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped
    .replace(/&lt;sprite name="([\w.]+)"&gt;/g, (_, name) => `<img class="inline-icon" src="icons/${name}.png" alt="">`)
    .replace(/&lt;color=(#[0-9a-fA-F]{6,8})&gt;/g, '<span style="color:$1">')
    .replace(/&lt;\/color&gt;/g, "</span>")
    .replace(/&lt;style=[^&]*&gt;/g, "<b>")
    .replace(/&lt;\/style&gt;/g, "</b>")
    .replace(/&lt;u( [^&]*)?&gt;/g, "<u>")
    .replace(/&lt;\/u&gt;/g, "</u>");
}

function renderPetDetail(proto) {
  const detail = $("area-detail");
  const pet = proto && (markData.pets || {})[proto];
  if (!pet || !openAreaId) {
    detail.innerHTML = '<p class="detail-hint">Point at an Aniimo to see how to find it.</p>';
    return;
  }
  const state = petState(proto, openAreaId);
  const lines = [];
  if (pet.condition) lines.push(`<p class="detail-condition">${richText(pet.condition)}</p>`);
  if (pet.find && pet.find !== pet.condition) lines.push(`<p class="detail-find">${richText(pet.find)}</p>`);
  if (!lines.length) lines.push('<p class="detail-find">No special condition: it roams this area.</p>');
  detail.innerHTML = `<div class="detail-head">${petIcon(proto, `detail-head-icon ${state}`)}`
    + `<div><b>${pet.name || ""}</b><span>${pet.form || ""}</span></div>`
    + `<em class="detail-state ${state}">${STATE_TEXT[state]}</em></div>${lines.join("")}`;
}

// The weather badge is solid while the area has that weather, faded otherwise
function conditionBadge(condition, state, active) {
  if (!condition) return "";
  const cls = `badge ${active ? "" : "inactive"}`;
  if (condition === "aurora" && state === "unseen") return `<i class="${cls}">?</i>`;
  return `<img class="${cls}" src="icons/${CONDITION_ICONS[condition]}" alt="${condition}">`;
}

// Progress bar coloured by the tier reached, with a marker at each tier's threshold
// Honeycomb like the game's panel: rows of 3 and 4 in turn, a short last row centred
const HONEYCOMB_ROWS = [3, 4];
function honeycombRows(cells) {
  const rows = [];
  for (let i = 0, r = 0; i < cells.length; r++) {
    const size = HONEYCOMB_ROWS[r % HONEYCOMB_ROWS.length];
    rows.push(cells.slice(i, i + size));
    i += size;
  }
  return rows;
}

// Rows interlock when neighbours differ in odd/even size; a centred short row of the same parity as the
// row above would sit right under it, so it gets a full gap instead of the overlap
function honeycombHtml(rows) {
  return rows.map((row, r) => {
    const stacked = r > 0 && row.length % 2 === rows[r - 1].length % 2;
    return `<div class="hex-row ${stacked ? "stacked" : ""}">${row.join("")}</div>`;
  }).join("");
}

// Progress like the game's: the % pill and the fill take the colour of the tier reached, the fill runs to
// the %, the rest of the section being filled previews the next tier's colour and later sections stay
// dark; the bar is split at each threshold, the crown sits on the last threshold reached and the ones
// still ahead are labelled
const CROWN_ICON = '<img src="icons/UI_Img_Map_PetArea_Gather_Schedule_Icon.png" alt="">';   // the game's crown
function renderTiers(tiers, completion) {
  const reached = tiers.filter((t) => completion >= t * 100).length;
  const tier = reached ? TIER_NAMES[Math.min(reached, TIER_NAMES.length) - 1] : "none";
  $("area-pill").className = `area-pill ${tier}`;
  const bar = $("area-bar");
  bar.className = `area-bar ${tier}`;
  const nextTier = TIER_NAMES[Math.min(reached, TIER_NAMES.length - 1)];
  let start = 0, html = "";
  tiers.forEach((t, i) => {
    const fill = Math.max(0, Math.min(1, (completion / 100 - start) / (t - start)));
    const current = i === reached ? `current ${nextTier}` : "";   // the section being filled
    html += `<span class="seg ${current}" style="flex:${t - start}"><i style="width:${fill * 100}%"></i></span>`;
    start = t;
  });
  html += tiers.filter((t) => completion < t * 100)
    .map((t) => `<em class="tier-label" style="left:${t * 100}%">${Math.round(t * 100)}%</em>`).join("");
  if (reached) html += `<b class="crown" style="left:${tiers[reached - 1] * 100}%">${CROWN_ICON}</b>`;
  bar.innerHTML = html;
}

// ---- tracking: the yellow squares where the tracked Aniimo spawns ----
function currentTrack() {
  if (pickedTrack) return pickedTrack;
  return handbook.tracked && String(handbook.tracked) !== String(trackClosed) ? handbook.tracked : null;
}

// Picking the Aniimo already shown stops showing it, unless `toggle` is off (the collection's "Show on map")
function setTrack(proto, toggle = true) {
  pickedTrack = toggle && String(proto) === String(currentTrack()) ? null : proto;
  if (!pickedTrack && handbook.tracked) trackClosed = handbook.tracked;   // un-tracking the game's pick
  renderTracking();
  renderAreaPanel();
}
$("track-close").onclick = () => {
  if (handbook.tracked) trackClosed = handbook.tracked;
  pickedTrack = null;
  renderTracking();
  renderAreaPanel();
};

function renderTracking() {
  spawnLayer.clearLayers();
  const proto = currentTrack();
  $("track").classList.toggle("hidden", !proto);
  if (!proto || !current) return;
  $("track-icon").innerHTML = petIcon(proto, "head");
  const pet = petInfo(proto);
  $("track-name").textContent = pet.name || `Aniimo ${proto}`;
  $("track-form").textContent = pet.form || "";
  const cells = new Set();
  for (const [x, y] of (markData.spawns || {})[proto] || []) cells.add(`${Math.floor(x / SPAWN_CELL)},${Math.floor(y / SPAWN_CELL)}`);
  for (const cell of cells) {
    const [cx, cy] = cell.split(",").map(Number);
    L.rectangle([toLatLng(cx * SPAWN_CELL, (cy + 1) * SPAWN_CELL), toLatLng((cx + 1) * SPAWN_CELL, cy * SPAWN_CELL)],
      { className: "spawn-cell", interactive: false }).addTo(spawnLayer);
  }
  $("track-where").dataset.cells = cells.size;
  renderTrackCounts();
}

// ---- wild Aniimo near you (wild.js reads them): counts on the tracking chip and the area panel's hexes ----
const nearCount = (proto) => wildNearby.filter((w) => String(w.form) === String(proto)).length;
const spawnSpots = (proto) => ((markData.spawns || {})[proto] || []).length;   // spawn points on this map

// "2 / 41": how many are out near you, of the places on this map where they spawn
function nearPill(proto) {
  const near = nearCount(proto);
  return near ? `<i class="near" title="${near} near you, of ${spawnSpots(proto)} spawn points on this map">${near}<small>/${spawnSpots(proto)}</small></i>` : "";
}

function renderTrackCounts() {
  const proto = currentTrack(), where = $("track-where");
  if (!proto) return;
  if (!Number(where.dataset.cells)) { where.textContent = "Not found on this map"; return; }
  const near = nearCount(proto);
  where.textContent = `${near ? `${near} near you` : "None near you"} · ${spawnSpots(proto)} spawn points`;
}

// Zoom so every spawn cell of the tracked Aniimo is on screen (the collection's "Show on map")
const TRACK_PADDING = [60, 60];
const TRACK_ZOOM_OUT = 1;              // one step further out than a tight fit, to see what's around
const TRACK_MAX_ZOOM_FROM_MAX = 2;     // never closer than maxZoom - 2, even for a single cell
function zoomToTrack() {
  const layers = spawnLayer.getLayers();
  if (!layers.length) return;
  setFollow(false);
  const bounds = L.featureGroup(layers).getBounds();
  const fit = map.getBoundsZoom(bounds, false, L.point(TRACK_PADDING).multiplyBy(2));
  const zoom = Math.max(map.getMinZoom(), Math.min(fit - TRACK_ZOOM_OUT, map.getMaxZoom() - TRACK_MAX_ZOOM_FROM_MAX));
  map.setView(bounds.getCenter(), zoom);
}

// ---- offline map: pick a map instead of following you; positions use the game's own calibration ----
function showOfflineMap(mapId) {
  const info = maps[mapId];
  state = { status: "offline", scene: { id: Number(mapId), mapId: String(mapId) }, pos: null,
            calibration: { source: "config", points: [], x: info.calibration.x, z: info.calibration.z } };
  showScene(state.scene);
  setFollow(false);
  renderLocation(-1, -1);   // the map's name until the cursor is over an area
  $("map-pick").value = mapId;
  localStorageSet("offlineMap", mapId);
}

async function startOffline() {
  document.body.classList.add("offline");
  setStatus("Offline map", true);
  const offered = await getJson("offline/maps.json");
  const picker = document.createElement("select");
  picker.id = "map-pick";
  picker.title = "Show another map";
  picker.innerHTML = offered.map((m) => `<option value="${m.id}">${m.name}</option>`).join("");
  picker.onchange = () => showOfflineMap(picker.value);
  document.querySelector("#location .place").prepend(picker);
  // ?map=3003 opens that map (a link to share), else the one you last looked at
  const wanted = [new URLSearchParams(location.search).get("map"), localStorageGet("offlineMap")];
  picker.value = wanted.find((id) => offered.some((m) => m.id === id)) || offered[0].id;
  showOfflineMap(picker.value);
  if (new URLSearchParams(location.search).has("connect")) connectToGame();   // ?connect: a bookmark that connects
}

// ---- "Connect to game": the static site switching to the game server on this PC, and back ----
const LOCAL_SERVER = "http://localhost:8777";
const CONNECT_TIMEOUT_MS = 5000;

async function connectToGame() {
  const button = $("connect");
  button.disabled = true;
  setStatus("Connecting to the game server on this PC...", false);
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), CONNECT_TIMEOUT_MS);
  try {
    const res = await fetch(`${LOCAL_SERVER}/api/state`, { cache: "no-store", signal: abort.signal });
    if (!res.ok) throw new Error(`The game server answered with an error (${res.status})`);
    await res.json();
    localStorageSet("liveServer", LOCAL_SERVER);
    location.reload();   // start over in live mode
  } catch (e) {
    setStatus(e.name === "AbortError"
      ? "No answer in 5 s: make sure the game server is running on this PC (python app/server.py)"
      : e.message.startsWith("The game server") ? e.message
      : "Couldn't connect: make sure the game server is running on this PC, and allow local network access if your browser asks", false);
    button.disabled = false;
  } finally {
    clearTimeout(timer);
  }
}

function disconnectFromGame() {
  localStorageSet("liveServer", "");
  location.reload();
}

if (STATIC_SITE) {
  $("connect").classList.remove("hidden");
  $("connect").textContent = LIVE_SERVER ? "Disconnect" : "Connect to game";
  $("connect").title = LIVE_SERVER ? "Back to the offline map" : "Show your live game from the server running on this PC";
  $("connect").onclick = LIVE_SERVER ? disconnectFromGame : connectToGame;
}

// ---- start ----
fetch("maps.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((m) => {
    maps = m;
    if (OFFLINE) startOffline();
    else { poll(); loadHandbook(); }
  });
