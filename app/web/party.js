// Party: a card per member beside your profile (picture, rank, level, their <N>P cat badge, the leader's
// flag, where they are) and their arrows on the map, badged like the game's. /api/party is read every second
// and the arrows glide between reads (Glider, app.js).
const Party = (() => {
  const POLL_MS = 1000;
  const GLIDE_MAX_MS = 1500;           // party positions come about once a second
  const LEADER_TITLE = "Party leader";

  let party = { members: [] };
  const arrows = new Map();            // uid -> { marker, glider }

  const catBadge = (order) => `<svg class="party-cat" viewBox="0 0 40 32" aria-label="${order}P">`
    + '<path d="M5 3 L13 10 Q20 8.5 27 10 L35 3 L36 17 Q37 30 20 30 Q3 30 4 17 Z"/>'
    + `<text x="20" y="25">${order}P</text></svg>`;
  const leaderFlag = `<svg class="party-flag" viewBox="0 0 24 24"><title>${LEADER_TITLE}</title><path d="M6 21V3.5M6 4h11l-2.5 4.5L17 13H6"/></svg>`;
  const ARROW_SVG = '<svg class="arrow" viewBox="0 0 34 34"><path d="M17 4 L26 27 L17 22 L8 27 Z" fill="#ffd34d" stroke="#1b1300" stroke-width="1.5"/></svg>';

  const others = () => party.members.filter((m) => !m.self);
  // Map marker: badge on top, arrow below; anchored at the arrow's centre (24 px badge + 2 gap + half the 30 px arrow)
  const markerIcon = (order) => L.divIcon({ className: "party-marker", iconSize: [40, 56], iconAnchor: [20, 41],
                                            html: catBadge(order) + ARROW_SVG });

  function where(member) {
    if (!member.pose) return member.online ? "Not nearby" : "Offline";
    const text = state && state.scene ? gameCoords(member.pose) : "";
    if (!state || !state.pos) return text;
    const metres = Math.round(Math.hypot(member.pose.x - state.pos.x, member.pose.z - state.pos.z));
    return `${text} · ${metres} m`;
  }

  function cardHtml(member) {
    const profile = markData.profile || {};
    const rank = (profile.ranks || {})[member.rank] || {};
    const head = (profile.headIcons || {})[member.headIcon], frame = (profile.headFrames || {})[member.headFrame];
    return `<button class="party-card${member.online ? "" : " offline"}" data-uid="${member.uid}" title="Show ${member.name} on the map">`
      + `<span class="party-picture">${head ? `<img class="party-icon" src="icons/${head}" alt="">` : ""}`
      + `${frame ? `<img class="party-frame" src="icons/${frame}" alt="">` : ""}${catBadge(member.order)}</span>`
      + `<span class="party-info"><b>${member.name || "?"}${member.leader ? leaderFlag : ""}</b>`
      + (rank.name ? `<span class="profile-rank">${rank.icon ? `<img src="icons/${rank.icon}" alt="">` : ""}<span>${rank.name}</span></span>` : "")
      + `<span class="party-level">Lv. ${member.level ?? "-"}</span>`
      + `<span class="party-where">${where(member)}</span></span></button>`;
  }

  function renderCards() {
    const list = others();
    const box = document.getElementById("party");
    box.classList.toggle("hidden", !list.length);
    box.innerHTML = list.map(cardHtml).join("");
    // Your own badge (and flag) on your profile picture
    const me = party.members.find((m) => m.self);
    document.getElementById("profile-party").innerHTML = me ? catBadge(me.order) + (me.leader ? leaderFlag : "") : "";
  }

  function arrowFor(member) {
    let entry = arrows.get(member.uid);
    if (!entry) {
      entry = { marker: L.marker([0, 0], { icon: markerIcon(member.order), interactive: false, keyboard: false, zIndexOffset: 9000 }),
                glider: new Glider(GLIDE_MAX_MS), order: member.order };
      arrows.set(member.uid, entry);
    }
    if (entry.order !== member.order) {   // someone left: badges renumber
      entry.order = member.order;
      entry.marker.setIcon(markerIcon(member.order));
    }
    return entry;
  }

  function placeArrows() {
    const shown = new Set();
    if (current && calibrated()) {
      for (const member of others()) {
        if (!member.pose) continue;
        const entry = arrowFor(member);
        const [px, py] = worldToPixel(member.pose.x, member.pose.z);
        entry.glider.to(current.id, px, py, screenAngle(member.pose.heading || 0));
        if (!map.hasLayer(entry.marker)) entry.marker.setLatLng(toLatLng(px, py)).addTo(map);
        shown.add(member.uid);
      }
    }
    for (const [uid, entry] of arrows) {
      if (!shown.has(uid)) {
        map.removeLayer(entry.marker);
        arrows.delete(uid);
      }
    }
  }

  function draw(now) {
    requestAnimationFrame(draw);
    if (mapPaused) return;
    for (const { marker, glider } of arrows.values()) drawGlide(marker, glider, now);
  }
  requestAnimationFrame(draw);

  async function poll() {
    try {
      party = Object.assign({ members: [] }, await getJson("api/party"));
      renderCards();
      placeArrows();
    } catch (e) { /* server restarting: keep what's shown */ }
    setTimeout(poll, POLL_MS);
  }
  poll();

  document.getElementById("party").onclick = (e) => {
    const card = e.target.closest(".party-card");
    const entry = card && arrows.get(card.dataset.uid);
    if (!entry) return;
    setFollow(false);
    map.panTo(entry.marker.getLatLng());
  };
  return { members: () => party.members };
})();
