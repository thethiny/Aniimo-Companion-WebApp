// Wild Aniimo near you: /api/wild every second (only what the game has loaded around you). Each one is a small
// portrait dot ringed in its rarity's colour, gliding between reads (Glider, app.js): only the tracked kind
// while you track one, every one otherwise, always through the rarity / variant filter (wildShown, app.js).
// The tracking chip and the area panel's hexes count them (app.js).
const Wild = (() => {
  const POLL_MS = 1000;
  const GLIDE_MAX_MS = 1500;           // positions come about once a second
  const DOT_SIZE = 24;

  const dots = new Map();              // entity id -> { marker, glider }
  let counts = "";                     // what the counts last showed, to redraw only when they change

  const rarityName = (rarity) => ((markData.profile || {}).rarities || {})[rarity] || "";
  const dotIcon = (wild) => L.divIcon({ className: `wild-dot r${wild.rarity || 0}`, iconSize: [DOT_SIZE, DOT_SIZE],
                                        html: petIcon(wild.form, "wild-head") });

  function tooltip(wild) {
    const tagNames = (markData.profile || {}).tags || {};
    const tags = variantsOf(wild).map((t) => (tagNames[t] || {}).name || t);
    return [`<b>${petLabel(wild.form)}</b>`, wild.level ? `Lv. ${wild.level}` : "",
      rarityName(wild.rarity) + (wild.potential ? ` (best stats ${wild.potential.join(" / ")})` : ""), ...tags, heightText(wild.y)]
      .filter(Boolean).join(" · ");
  }

  function dotFor(wild) {
    let entry = dots.get(wild.id);
    if (!entry) {
      entry = { marker: L.marker([0, 0], { icon: dotIcon(wild), keyboard: false, zIndexOffset: 8000 }), glider: new Glider(GLIDE_MAX_MS) };
      entry.marker.bindTooltip(tooltip(wild), { direction: "top", offset: [0, -DOT_SIZE / 2] });
      dots.set(wild.id, entry);
    }
    return entry;
  }

  function placeDots() {
    const tracked = currentTrack(), shown = new Set();
    if (current && calibrated()) {
      for (const wild of wildNearby) {
        if ((tracked && String(wild.form) !== String(tracked)) || !wildShown(wild)) continue;
        const entry = dotFor(wild);
        const [px, py] = worldToPixel(wild.x, wild.z);
        entry.glider.to(current.id, px, py, 0);
        entry.marker.setTooltipContent(tooltip(wild));   // height changes as it moves
        if (!map.hasLayer(entry.marker)) entry.marker.setLatLng(toLatLng(px, py)).addTo(map);
        shown.add(wild.id);
      }
    }
    for (const [id, entry] of dots) {
      if (!shown.has(id)) {
        map.removeLayer(entry.marker);
        dots.delete(id);
      }
    }
  }

  function renderCounts() {
    const now = wildNearby.map((w) => w.form).sort().join(",") + `|${currentTrack()}`;
    if (now === counts) return;
    counts = now;
    renderTrackCounts();
    if (openAreaId) renderAreaPanel();
  }

  function draw(now) {
    requestAnimationFrame(draw);
    if (mapPaused) return;
    for (const { marker, glider } of dots.values()) drawGlide(marker, glider, now);
  }
  requestAnimationFrame(draw);

  async function poll() {
    if (OFFLINE) return;   // the offline map has no live Aniimo
    try {
      wildNearby = (await getJson(apiUrl("api/wild"))).wild || [];
      placeDots();
      renderCounts();
    } catch (e) { /* server restarting: keep what's shown */ }
    setTimeout(poll, POLL_MS);
  }
  poll();
  return { refresh: () => { placeDots(); renderCounts(); } };
})();
