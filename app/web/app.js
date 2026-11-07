// Aniimo live map: polls /api/state and draws the player on the game's own map tiles.
const POLL_MS = 200;
const $ = (id) => document.getElementById(id);

const map = L.map("map", { crs: L.CRS.Simple, zoomControl: false, attributionControl: false, zoomSnap: 0.25 });
L.control.zoom({ position: "bottomright" }).addTo(map);

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
  if (!info) { $("scene").textContent = `Unknown map ${id}`; return; }
  if (current) map.removeLayer(current.layer);
  const layer = L.tileLayer(`tiles/${id}/{z}/{x}_{y}.webp`, {
    tileSize: info.tileSize, minZoom: 0, maxNativeZoom: info.maxZoom, maxZoom: info.maxZoom + 2, noWrap: true,
    bounds: L.latLngBounds(map.unproject([0, info.height], info.maxZoom), map.unproject([info.width, 0], info.maxZoom)),
  });
  current = { id, info, layer };
  layer.addTo(map);
  map.setMinZoom(0);
  map.setMaxZoom(info.maxZoom + 2);
  map.setView(toLatLng(info.width / 2, info.height / 2), 2);
  $("scene").textContent = `${info.name} (${id})`;
  drawCalibDots();
}

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
  $("x").textContent = x.toFixed(0);
  $("z").textContent = z.toFixed(0);
  $("y").textContent = y.toFixed(0);
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
  const [px, py] = worldToPixel(x, z);
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

// ---- follow (Google Maps style: dragging stops it, the button resumes it at the current zoom) ----
function setFollow(on) {
  follow = on;
  $("center").classList.toggle("on", on);
  if (on && map.hasLayer(player)) map.panTo(player.getLatLng(), { animate: true });
}
$("center").onclick = () => setFollow(true);
map.on("dragstart", () => setFollow(false));

// ---- fit: the landmass you're on (or the main one) fills the screen ----
$("fit").onclick = () => {
  if (!current) return;
  const regions = current.info.regions || [];
  let box = [0, 0, current.info.width, current.info.height];
  if (regions.length) {
    box = regions[0];
    if (calibrated() && state.pos) {
      const [px, py] = worldToPixel(state.pos.x, state.pos.z);
      box = regions.find((r) => px >= r[0] && px <= r[2] && py >= r[1] && py <= r[3]) || box;
    }
  }
  setFollow(false);
  map.fitBounds(L.latLngBounds(toLatLng(box[0], box[3]), toLatLng(box[2], box[1])), { padding: [12, 12] });
};

// ---- route mirrored from the game's guide line ----
const routeLine = L.polyline([], { color: "#4da3ff", weight: 5, opacity: 0.9, className: "route-line" }).addTo(map);
const routeDone = L.polyline([], { color: "#93a4b8", weight: 4, opacity: 0.5, dashArray: "4 8" }).addTo(map);

const OFF_ROUTE_METRES = 40;   // further than this from the path: you still have to get to it
const routeMarks = L.layerGroup().addTo(map);
let routeKey = null;

function markIcon(cls, html) {
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
      L.marker(ll(route.start.pos), { icon: markIcon("start", "&#x2726;"), title: "Teleport here" }).addTo(routeMarks);
    }
    L.marker(ll(route.end), { icon: markIcon("end", "&#x2691;"), title: "Target" }).addTo(routeMarks);
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

// ---- start ----
fetch("maps.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((m) => { maps = m; poll(); });
