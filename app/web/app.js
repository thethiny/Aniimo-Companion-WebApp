// Aniimo live map: polls /api/state and draws the player on the game's own map tiles.
const POLL_MS = 200;
const $ = (id) => document.getElementById(id);

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
const player = L.marker([0, 0], { icon: arrow, interactive: false, keyboard: false });

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
  const info = maps[id];
  if (!info) { $("world").textContent = id; return; }
  if (current) map.removeLayer(current.layer);
  const layer = L.tileLayer(`tiles/${id}/{z}/{x}_{y}.webp`, {
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
    areaNames = await (await fetch("api/areas", { cache: "no-store" })).json();
  } catch (e) { /* keep what we have */ }
}
setInterval(refreshAreaNames, 60000);

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

$("copy").onclick = async () => {
  if (!state || !state.pos) return;
  const text = `${gameCoord(state.pos.x)}, ${gameCoord(state.pos.z)}`;
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
    const res = await fetch("api/state", { cache: "no-store" });
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
  $("coords").textContent = `${gameCoord(x)}, ${gameCoord(z)}`;
  $("height").textContent = `Height ${y.toFixed(0)}`;
  if (!current) return;
  const auto = state.calibration && state.calibration.source === "auto";
  $("calibrate").style.display = auto ? "none" : "";
  if (!calibrated()) {
    setStatus(state.mapPos
      ? "Calibrating from the game's minimap: walk about 25 m"
      : "Waiting for the game's minimap to calibrate (or use Calibrate)", false);
    if (map.hasLayer(player)) map.removeLayer(player);
    return;
  }
  setStatus(auto ? "Live" : "Live (manual calibration)", true);
  if (marksWaitingForPosition && !markData.fit) drawMarks();   // marks placed by the calibration
  const [px, py] = worldToPixel(x, z);
  renderLocation(px, py);
  const ll = toLatLng(px, py);
  player.setLatLng(ll);
  if (!map.hasLayer(player)) player.addTo(map);
  // Facing: map the world forward vector through the calibration to screen space
  const h = (state.heading * Math.PI) / 180;
  const dx = state.calibration.x[0] * Math.sin(h);
  const dy = state.calibration.z[0] * Math.cos(h);
  const deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
  const svg = player.getElement() && player.getElement().querySelector("svg");
  if (svg) svg.style.transform = `rotate(${deg}deg)`;
  renderRoute();
  if (follow && !picking) map.panTo(ll, { animate: true, duration: 0.25, noMoveStart: true });
}

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
  const res = await fetch(`api/calibration/${action}`, {
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
    markData = await (await fetch("api/marks", { cache: "no-store" })).json();
  } catch (e) {
    return;
  }
  drawMarks();
  renderAreaPanel();
  renderTracking();
}
setInterval(loadMarks, 60000);

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
    mark.status === STATUS_DONE ? "done" : ""].join(" ");
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

function markTooltip(mark) {
  const type = typeOf(mark).name || "Mark";
  const status = mark.status === STATUS_LOCKED ? "locked" : mark.status === STATUS_DONE ? "completed" : "";
  const where = mark.world ? `${mark.world[0].toFixed(0)}, ${mark.world[1].toFixed(0)}` : "";
  const head = mark.title ? `<b>${mark.title}</b> · ${type}` : `<b>${type}</b>`;
  return [head, status, where].filter(Boolean).join(" · ");
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
// shown = max(L, round(0.2 L + 0.8 T)) + 1. T per star title is measured from the game's own labels;
// unmeasured star titles show the base ranges.
const LEVEL_SCALING = { region: 300001, weight: 0.2, targetByStarTitle: { 7: 49 } };

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
let pickedTrack = null;                       // Aniimo you picked here; the game's own pick otherwise
let trackClosed = null;                       // the game's pick you dismissed here
let openAreaId = new URLSearchParams(location.search).get("area");   // ?area=20800 opens that area's panel
const spawnLayer = L.layerGroup().addTo(map);

async function getJson(url) {
  return (await fetch(url, { cache: "no-store" })).json();
}

async function loadHandbook() {
  try {
    const [book, sky] = await Promise.all([getJson("api/handbook"), getJson("api/weather")]);
    handbook = Object.assign({ states: {}, blockCaught: {}, tracked: null }, book);
    weather = sky.weather || {};
    timePeriod = sky.timePeriod || null;
  } catch (e) {
    return;
  }
  if (markData.marks.length) drawMarks();   // area labels show completion
  renderAreaPanel();
  renderTracking();
  renderTimeOfDay();
}
setInterval(loadHandbook, HANDBOOK_POLL_MS);

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

function petLabel(proto) {
  const pet = (markData.pets || {})[proto] || {};
  return [pet.name || `Aniimo ${proto}`, pet.form].filter(Boolean).join(" · ");
}

function petIcon(proto, cls) {
  const pet = (markData.pets || {})[proto] || {};
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
$("area-close").onclick = () => { openAreaId = null; renderAreaPanel(); };

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
      + `<span class="hex">${petIcon(proto, "head")}</span>${conditionBadge(condition, state, weatherMatches(condition, openAreaId))}</button>`;
  });
  $("area-pets").innerHTML = honeycombHtml(honeycombRows(cells));
  for (const button of $("area-pets").querySelectorAll(".pet")) {
    button.onclick = () => setTrack(button.dataset.proto);
  }
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

function setTrack(proto) {
  pickedTrack = String(proto) === String(currentTrack()) ? null : proto;
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
  const pet = (markData.pets || {})[proto] || {};
  $("track-name").textContent = pet.name || `Aniimo ${proto}`;
  $("track-form").textContent = pet.form || "";
  const cells = new Set();
  for (const [x, y] of (markData.spawns || {})[proto] || []) cells.add(`${Math.floor(x / SPAWN_CELL)},${Math.floor(y / SPAWN_CELL)}`);
  for (const cell of cells) {
    const [cx, cy] = cell.split(",").map(Number);
    L.rectangle([toLatLng(cx * SPAWN_CELL, (cy + 1) * SPAWN_CELL), toLatLng((cx + 1) * SPAWN_CELL, cy * SPAWN_CELL)],
      { className: "spawn-cell", interactive: false }).addTo(spawnLayer);
  }
  $("track-where").textContent = cells.size ? "" : "Not found on this map";
}

// ---- start ----
fetch("maps.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((m) => { maps = m; poll(); loadHandbook(); });
