// Party: a card per member beside your profile (picture, rank, level, their <N>P cat badge, the leader's
// flag, where they are, your friendship and the Aniimo out with them) and their arrows on the map, badged
// like the game's. Clicking a card opens their details (combat power, team, explore abilities, Collection
// Progress). /api/party is read every second and the arrows glide between reads (Glider, app.js);
// /api/friends (affinity, known once you've opened their info card in game) now and then.
const Party = (() => {
  const POLL_MS = 1000;
  const FRIENDS_POLL_MS = 30000;
  const DETAIL_GAP = 10;               // px between the cards and the details under them
  const PHONE_QUERY = "(max-width: 720px)";   // party.css's phone layout
  const CARD_HINT = "Open their info card in game to see your friendship and their Collection Progress";
  const GLIDE_MAX_MS = 1500;           // party positions come about once a second
  const LEADER_TITLE = "Party leader";

  let party = { members: [] };
  let friends = {};                    // uid -> {affinity, caught, sparkling, prismana, style}
  let openUid = null;                  // the member whose details are open
  const arrows = new Map();            // uid -> { marker, glider }

  const catBadge = (order) => `<svg class="party-cat" viewBox="0 0 40 32" aria-label="${order}P">`
    + '<path d="M5 3 L13 10 Q20 8.5 27 10 L35 3 L36 17 Q37 30 20 30 Q3 30 4 17 Z"/>'
    + `<text x="20" y="25">${order}P</text></svg>`;
  const leaderFlag = `<svg class="party-flag" viewBox="0 0 24 24"><title>${LEADER_TITLE}</title><path d="M6 21V3.5M6 4h11l-2.5 4.5L17 13H6"/></svg>`;
  const ARROW_SVG = '<svg class="arrow" viewBox="0 0 34 34"><path d="M17 4 L26 27 L17 22 L8 27 Z" fill="#ffd34d" stroke="#1b1300" stroke-width="1.5"/></svg>';

  const others = () => party.members.filter((m) => !m.self);
  // Map marker: badge on top, arrow below; anchored at the arrow's centre (24 px badge + 2 gap + half the 30 px arrow)
  const companionOf = (member) => (member.companions || [])[0];
  const markerIcon = (order, companion) => L.divIcon({ className: "party-marker", iconSize: [40, 56], iconAnchor: [20, 41],
    html: catBadge(order) + ARROW_SVG + (companion ? `<span class="party-companion">${petIcon(companion.form, "wild-head")}</span>` : "") });

  // "Good Friend · 470/500": the tier your affinity is in and the next one's start (Soulmate: just the value)
  function friendship(uid) {
    const value = (friends[uid] || {}).affinity;
    const tiers = (markData.profile || {}).friendship || [];
    const tier = value == null ? null : tiers.find((t) => value >= t.min && value <= t.max) || tiers[tiers.length - 1];
    if (!tier) return "";
    const next = tiers[tiers.indexOf(tier) + 1];
    return `<span class="party-friendship">${tier.icon ? `<img src="icons/${tier.icon}" alt="">` : ""}${tier.name} · `
      + `${value}${next ? `/${next.min}` : ""}</span>`;
  }

  const mapName = (scene) => (maps[scene] || {}).name || `Map ${scene}`;

  function where(member) {
    if (!member.online) return "Offline";
    if (member.scene && current && String(member.scene) !== String(current.id)) return mapName(member.scene);
    if (!member.pose) return "Not nearby";
    const text = state && state.scene ? gameCoords(member.pose) : "";
    if (!state || !state.pos) return text;
    const metres = Math.round(Math.hypot(member.pose.x - state.pos.x, member.pose.z - state.pos.z));
    return `${text} · ${metres} m`;
  }

  function cardHtml(member) {
    const profile = markData.profile || {};
    const rank = (profile.ranks || {})[member.rank] || {};
    const head = (profile.headIcons || {})[member.headIcon], frame = (profile.headFrames || {})[member.headFrame];
    const companion = companionOf(member);
    return `<button class="party-card${member.online ? "" : " offline"}${openUid === member.uid ? " open" : ""}" `
      + `data-uid="${member.uid}" title="${member.name}: details">`
      + `<span class="party-picture">${head ? `<img class="party-icon" src="icons/${head}" alt="">` : ""}`
      + `${frame ? `<img class="party-frame" src="icons/${frame}" alt="">` : ""}${catBadge(member.order)}</span>`
      + `<span class="party-info"><b>${member.name || "?"}${member.leader ? leaderFlag : ""}</b>`
      + (rank.name ? `<span class="profile-rank">${rank.icon ? `<img src="icons/${rank.icon}" alt="">` : ""}<span>${rank.name}</span></span>` : "")
      + `<span class="party-level">Lv. ${member.level ?? "-"}</span>`
      + `<span class="party-where">${where(member)}</span>${friendship(member.uid)}</span>`
      + (companion ? `<span class="party-pet" title="${petLabel(companion.form)}">${petIcon(companion.form, "wild-head")}</span>` : "")
      + `</button>`;
  }

  // Details under the cards: combat power, their team, explore abilities and Collection Progress
  function detailHtml(member) {
    const profile = markData.profile || {};
    const tagNames = profile.tags || {};
    const team = (member.team || []).map((pet) => `<span class="party-mon">${petIcon(pet.form, "wild-head")}`
      + `<b>${petLabel(pet.form)}</b><small>Lv. ${pet.level ?? "-"} · CP ${pet.cp ?? "-"}`
      + `${(pet.tags || []).map((t) => ` · ${(tagNames[t] || {}).name || t}`).join("")}</small></span>`).join("");
    const abilities = (member.abilities || []).map((id) => (profile.abilities || {})[id]).filter(Boolean)
      .map((a) => `<span class="party-ability"><img src="icons/${a.icon}" alt="">${a.name}</span>`).join("");
    const known = friends[member.uid] || {};
    const stats = (profile.collectStats || []).filter((s) => known[s.key] != null)
      .map((s) => `<span class="party-stat"><img src="icons/${s.icon}" alt="">${s.name}<b>${known[s.key]}</b></span>`).join("");
    return `<div class="party-detail-head"><b>${member.name || "?"}</b><span>CP ${member.cp ?? "-"}</span></div>`
      + (team ? `<div class="party-section">Team</div><div class="party-team">${team}</div>` : "")
      + (abilities ? `<div class="party-section">Explore abilities</div><div class="party-abilities">${abilities}</div>` : "")
      + `<div class="party-section">Collection Progress</div>`
      + (stats ? `<div class="party-stats">${stats}</div>` : `<div class="party-hint">${CARD_HINT}</div>`);
  }

  function renderDetail() {
    const member = others().find((m) => m.uid === openUid);
    if (!member) openUid = null;
    const box = document.getElementById("party-detail");
    box.classList.toggle("hidden", !member);
    box.innerHTML = member ? detailHtml(member) : "";
    // Right under the cards, however tall they are (friendship adds a line)
    const phone = window.matchMedia(PHONE_QUERY).matches;   // on phones the cards and details sit at the bottom
    box.style.top = member && !phone ? `${document.getElementById("party").getBoundingClientRect().bottom + DETAIL_GAP}px` : "";
  }

  function renderCards() {
    const list = others();
    const box = document.getElementById("party");
    box.classList.toggle("hidden", !list.length);
    box.innerHTML = list.map(cardHtml).join("");
    renderDetail();
    // Your own badge (and flag) on your profile picture
    const me = party.members.find((m) => m.self);
    document.getElementById("profile-party").innerHTML = me ? catBadge(me.order) + (me.leader ? leaderFlag : "") : "";
  }

  function arrowFor(member) {
    let entry = arrows.get(member.uid);
    const companion = (companionOf(member) || {}).form || null;
    if (!entry) {
      entry = { marker: L.marker([0, 0], { icon: markerIcon(member.order, companionOf(member)), interactive: false,
                                           keyboard: false, zIndexOffset: 9000 }),
                glider: new Glider(GLIDE_MAX_MS), order: member.order, companion };
      arrows.set(member.uid, entry);
    }
    if (entry.order !== member.order || entry.companion !== companion) {   // someone left (badges renumber), a new companion
      Object.assign(entry, { order: member.order, companion });
      entry.marker.setIcon(markerIcon(member.order, companionOf(member)));
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
    if (OFFLINE) return;   // the offline map has no players
    try {
      party = Object.assign({ members: [] }, await getJson(apiUrl("api/party")));
      renderCards();
      placeArrows();
    } catch (e) { /* server restarting: keep what's shown */ }
    setTimeout(poll, POLL_MS);
  }
  poll();

  async function pollFriends() {
    if (OFFLINE) return;
    try {
      friends = (await getJson(apiUrl("api/friends"))).friends || {};
      renderCards();
    } catch (e) { /* server restarting: keep what's shown */ }
    setTimeout(pollFriends, FRIENDS_POLL_MS);
  }
  pollFriends();

  // A card opens (or closes) their details, and shows them on the map when they're nearby
  document.getElementById("party").onclick = (e) => {
    const card = e.target.closest(".party-card");
    if (!card) return;
    openUid = openUid === card.dataset.uid ? null : card.dataset.uid;
    renderCards();
    const entry = arrows.get(card.dataset.uid);
    if (openUid && entry) {
      setFollow(false);
      map.panTo(entry.marker.getLatLng());
    }
  };
  return { members: () => party.members };
})();
