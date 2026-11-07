// Aniilog collection: full screen over the map (which stays loaded underneath, paused). Every Aniimo of the
// game's Aniilogs with what you've ever caught (your handbook, kept fresh by app.js), what you own right now
// (/api/collection) and the game's own totals. The static part (collection.json) loads the first time the
// page opens; the grid is only redrawn when the view or something it shows changes.
const Collection = (() => {
  const OWNED_POLL_MS = 5000;          // re-read what you own while the page is open (the server reads every 10 s)
  const ALL = "all";                   // the Aniilog tab that shows every Aniilog, one section each
  const BIT_KNOWN = 1, BIT_CAUGHT = 2;
  const VARIANT_BITS = { sparkling: [4, 8], alpha: [32, 64], prismana: [256, 512] };   // handbook [seen, caught]
  const TAG_ORDER = ["sparkling", "alpha", "prismana", "umbral"];
  const GENDERS = { 1: "♂", 2: "♀" };
  const STATUS_FILTERS = [["all", "All"], ["caught", "Caught"], ["seen", "Seen"], ["unseen", "Not seen"],
                          ["stored", "In storage"], ["missing", "Missing a variant"]];
  const STATE_TEXT = { caught: "Caught", seen: "Seen", unseen: "Not seen yet" };
  const RANK = { unseen: 0, seen: 1, caught: 2 };
  const VIEW_KEY = "collectionView";

  let book = null;                     // collection.json
  let ownedByForm = new Map();         // form -> [owned Aniimo]
  let loading = null, isOpen = false, ownedTimer = null, drawnKey = "";
  const view = Object.assign({ log: "300001", mode: "family", element: "", status: "all" },
                             JSON.parse(localStorageGet(VIEW_KEY) || "{}"), { search: "", selected: null, focus: null });   // focus: form picked in the panel
  const el = (id) => document.getElementById(id);

  // ---- what you have: one place decides every state the page shows ----
  // Offline (no player): every form caught, with every variant it can come as
  const maxedMask = (form) => BIT_KNOWN | BIT_CAUGHT | VARIANT_BITS.alpha[0] | VARIANT_BITS.alpha[1]
    | ((info(form).can || []).includes("sparkling") ? VARIANT_BITS.sparkling[0] | VARIANT_BITS.sparkling[1] : 0)
    | (info(form).prismana ? VARIANT_BITS.prismana[0] | VARIANT_BITS.prismana[1] : 0);
  const mask = (form) => (OFFLINE ? maxedMask(form) : handbook.states[form] || 0);
  const formState = (form) => (mask(form) & BIT_CAUGHT ? "caught" : mask(form) & BIT_KNOWN ? "seen" : "unseen");
  const bestState = (states) => states.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), "unseen");
  const info = (form) => book.forms[form] || {};

  // A variant's state for one form: "caught", "seen" or null; Umbral is only known from what you own now
  function variantState(form, tag) {
    if (tag === "umbral" && OFFLINE) return (info(form).can || []).includes("umbral") ? "caught" : null;
    if (tag === "umbral") return (ownedByForm.get(+form) || []).some((p) => p.tags.includes("umbral")) ? "caught" : null;
    const [seen, caught] = VARIANT_BITS[tag];
    return mask(form) & caught ? "caught" : mask(form) & seen ? "seen" : null;
  }

  // Whether a variant exists for this form at all (badges only for those)
  function variantExists(form, tag) {
    if (tag === "prismana") return !!info(form).prismana;
    if (tag === "alpha") return true;
    return (info(form).can || []).includes(tag);
  }

  // Everything a tile or the detail panel shows for a set of forms (one form, or a family's forms)
  function summary(forms) {
    const states = forms.map(formState);
    const tags = {};
    for (const tag of TAG_ORDER) {
      const having = forms.filter((f) => variantExists(f, tag));
      if (having.length) tags[tag] = bestState(having.map((f) => (variantState(f, tag) || "unseen")));
    }
    const owned = forms.flatMap((f) => ownedByForm.get(+f) || []);
    const shown = forms.find((f) => formState(f) === "caught") || forms.find((f) => formState(f) === "seen") || forms[0];
    return { state: bestState(states), caught: states.filter((s) => s === "caught").length, states, tags, owned, shown };
  }

  // ---- the entries of the current view ----
  function logs() {
    return view.log === ALL ? book.aniilogs : book.aniilogs.filter((a) => String(a.id) === String(view.log));
  }

  function number(log, index) {
    return log.season ? `S1·${String(index + 1).padStart(2, "0")}` : String(index + 1).padStart(3, "0");
  }

  // One entry per family (Family Mode) or per form (Form Mode): {key, num, base, forms, form?}
  function entries(log) {
    const out = [];
    log.families.forEach(([base, forms], i) => {
      if (!forms.length) return;
      if (view.mode === "family") out.push({ key: `${log.id}:${base}`, num: number(log, i), base, forms });
      else for (const form of forms) out.push({ key: `${log.id}:${form}`, num: number(log, i), base, forms: [form], form });
    });
    return out;
  }

  function matches(entry, s) {
    const names = entry.forms.flatMap((f) => [info(f).name, info(f).form]).join(" ").toLowerCase();
    if (view.search && !names.includes(view.search.toLowerCase())) return false;
    if (view.element && !entry.forms.some((f) => (info(f).elements || []).includes(view.element))) return false;
    switch (view.status) {
      case "caught": return s.state === "caught";
      case "seen": return s.state === "seen";
      case "unseen": return s.state === "unseen";
      case "stored": return s.owned.length > 0;
      case "missing": return s.state === "caught" && Object.values(s.tags).some((t) => t !== "caught");
      default: return true;
    }
  }

  // ---- counters along the top, like the game's (and the Aniilog's collection bar) ----
  function counts(log) {
    const units = view.mode === "family" ? log.families.map(([, forms]) => forms) : log.families.flatMap(([, forms]) => forms.map((f) => [f]));
    const any = (forms, test) => forms.some(test);
    return {
      owned: units.filter((u) => any(u, (f) => mask(f) & BIT_CAUGHT)).length,
      sparkling: units.filter((u) => any(u, (f) => mask(f) & VARIANT_BITS.sparkling[1])).length,
      prismana: units.filter((u) => any(u, (f) => mask(f) & VARIANT_BITS.prismana[1])).length,
      umbral: units.filter((u) => any(u, (f) => variantState(f, "umbral"))).length,
      max: view.mode === "family" ? log.maxFamilies : log.maxForms,
    };
  }

  function renderCounters() {
    const total = { owned: 0, sparkling: 0, prismana: 0, umbral: 0, max: 0 };
    for (const log of logs()) for (const [k, v] of Object.entries(counts(log))) total[k] += v;
    const c = book.counters, mode = view.mode, umbral = book.tags.umbral;
    const counter = (name, value, label, icon) => `<span class="col-counter" title="${label}">`
      + (icon ? `<img src="icons/${icon}" alt="">` : "") + `<b>${value}</b><span>${label}</span></span>`;
    const percent = total.max ? Math.min(100, (100 * total.owned) / total.max) : 0;
    el("col-counters").innerHTML = counter("owned", total.owned, c.owned[mode], c.owned.icon)
      + counter("sparkling", total.sparkling, c.sparkling[mode], c.sparkling.icon)
      + counter("prismana", total.prismana, c.prismana[mode], c.prismana.icon)
      + counter("umbral", total.umbral, OFFLINE ? umbral.name : `${umbral.name} in storage`, umbral.icon)
      + `<span class="col-progress" title="${c.collection[mode]}"><span>${c.collection[mode]}</span>`
      + `<b>${total.owned}<small>/${total.max}</small></b><i><em style="width:${percent}%"></em></i></span>`;
    el("collection-summary").textContent = `${total.owned}/${total.max}`;
  }

  // ---- tiles ----
  const icon = (form, cls) => (info(form).icon
    ? `<img class="${cls}" src="icons/${info(form).icon}" alt="" loading="lazy" onerror="this.classList.add('missing')">`
    : `<span class="${cls} missing"></span>`);

  function tagBadges(tags) {
    return TAG_ORDER.filter((t) => t in tags).map((t) => {
      const tag = book.tags[t], state = tags[t];
      const text = state === "caught" ? (t === "umbral" ? "in storage" : "caught") : state === "seen" ? "seen" : "not yet";
      return `<img class="col-tag ${state}" src="icons/${tag.icon}" alt="" title="${tag.name}: ${text}">`;
    }).join("");
  }

  function elementIcons(form) {
    return (info(form).elements || []).map((name) => {
      const e = book.elements[name] || {};
      return `<img class="col-el" src="icons/${e.icon}" alt="" title="${e.name || name}">`;
    }).join("");
  }

  function pips(forms) {
    if (forms.length < 2) return "";
    return `<span class="col-pips">${forms.map((f) => `<i class="${formState(f)}"></i>`).join("")}</span>`;
  }

  function storedChip(s) {
    if (OFFLINE) return "";   // no storage without a player
    if (s.owned.length) return `<span class="col-stored" title="In your storage now">×${s.owned.length}</span>`;
    return s.state === "caught" ? '<span class="col-stored none" title="Caught before, none in your storage now">×0</span>' : "";
  }

  // Not caught yet: shown in full but faded, so you can see what's still missing
  function tileHtml(entry, s) {
    const head = info(s.shown);
    const sub = view.mode === "form" ? `<span class="col-form">${head.form || ""}</span>` : "";
    const progress = view.mode === "family" ? `<span class="col-count">${s.caught}/${entry.forms.length}</span>` : "";
    return `<button class="col-tile ${s.state}${view.selected === entry.key ? " selected" : ""}" data-key="${entry.key}">`
      + `<span class="col-num">${entry.num}</span>${storedChip(s)}`
      + `<span class="col-portrait">${icon(s.shown, "col-headimg")}</span>`
      + `<span class="col-name">${head.name || ""}</span>${sub}`
      + `<span class="col-meta"><span class="col-els">${elementIcons(entry.form || entry.base)}</span>${pips(entry.forms)}${progress}</span>`
      + `<span class="col-tags">${tagBadges(s.tags)}</span></button>`;
  }

  function renderGrid() {
    const sections = [];
    let shown = 0;
    for (const log of logs()) {
      const tiles = [];
      for (const entry of entries(log)) {
        const s = summary(entry.forms);
        if (matches(entry, s)) tiles.push(tileHtml(entry, s));
      }
      shown += tiles.length;
      const title = view.log === ALL ? `<h3 class="col-section">${log.name}</h3>` : "";
      if (tiles.length) sections.push(`${title}<div class="col-tiles">${tiles.join("")}</div>`);
    }
    el("col-grid").innerHTML = shown ? sections.join("") : '<p class="col-empty">No Aniimo match this filter.</p>';
  }

  // ---- detail panel: the family's forms, how to find them, and the ones you own ----
  function findEntry(key) {
    const [logId, id] = String(key).split(":");
    const log = book.aniilogs.find((a) => String(a.id) === logId);
    if (!log) return null;
    const index = log.families.findIndex(([base, forms]) => String(base) === id || forms.map(String).includes(id));
    if (index < 0) return null;
    const [base, forms] = log.families[index];
    return { log, base, forms, num: number(log, index), form: forms.map(String).includes(id) && view.mode === "form" ? +id : null };
  }

  function ownedRow(pet) {
    const f = info(pet.form);
    const tags = pet.tags.map((t) => `<img class="col-tag caught" src="icons/${book.tags[t].icon}" alt="" title="${book.tags[t].name}">`).join("");
    return `<li>${icon(pet.form, "cd-ownedimg")}<span class="cd-owned-name">${f.form || f.name || ""}</span>`
      + `<span class="cd-level">Lv. ${pet.level ?? "-"}</span>`
      + `<span class="cd-rarity r${pet.rarity}">${book.rarities[pet.rarity] || ""}</span>`
      + `<span class="cd-personality">${book.personalities[pet.personality] || ""}</span>`
      + `<span class="cd-gender">${GENDERS[pet.gender] || ""}</span><span class="cd-owned-tags">${tags}</span></li>`;
  }

  function renderDetail() {
    const panel = el("col-detail");
    const entry = view.selected && findEntry(view.selected);
    panel.classList.toggle("hidden", !entry);
    if (!entry) return;
    const s = summary(entry.forms);
    const focus = entry.forms.includes(view.focus) ? view.focus : entry.form || s.shown;
    const forms = entry.forms.map((f) => {
      const st = formState(f), one = summary([f]);
      return `<li class="${st}${f === focus ? " focus" : ""}" data-form="${f}" title="Show how to find it">${icon(f, "cd-formimg")}`
        + `<span class="cd-form-name">${info(f).form || ""}</span>`
        + `<em class="cd-state ${st}">${STATE_TEXT[st]}</em><span class="col-tags">${tagBadges(one.tags)}</span></li>`;
    }).join("");
    const pet = info(focus);
    const finds = [];
    if (pet.condition) finds.push(`<p class="cd-condition">${richText(pet.condition)}</p>`);
    if (pet.find && pet.find !== pet.condition) finds.push(`<p>${richText(pet.find)}</p>`);
    const owned = [...s.owned].sort((a, b) => (b.rarity || 0) - (a.rarity || 0) || (b.level || 0) - (a.level || 0));
    panel.innerHTML = `<div class="cd-head">${icon(focus, `cd-headimg ${s.state}`)}`
      + `<div class="cd-title"><span class="col-num">${entry.num} · ${entry.log.name}</span><b>${info(entry.base).name || ""}</b>`
      + `<span class="col-els">${elementIcons(entry.base)}</span></div>`
      + `<button class="col-close" id="cd-close" title="Close"><svg viewBox="0 0 24 24"><path d="M5 5l14 14M19 5L5 19"/></svg></button></div>`
      + `<h4>Forms <span>${s.caught}/${entry.forms.length} caught</span></h4><ul class="cd-forms">${forms}</ul>`
      + (finds.length ? `<h4>${pet.form || "How to find"}</h4><div class="cd-find">${finds.join("")}</div>` : "")
      + (OFFLINE ? "" : `<h4>In storage <span>${owned.length}</span></h4>`
        + (owned.length ? `<ul class="cd-owned">${owned.map(ownedRow).join("")}</ul>`
                        : `<p class="cd-none">${s.state === "caught" ? "Caught before, none in your storage now." : "None yet."}</p>`))
      + `<button class="cd-map" id="cd-map">Show ${[pet.name, entry.forms.length > 1 ? pet.form : ""].filter(Boolean).join(" · ") || "it"} on the map</button>`;
    el("cd-close").onclick = () => select(null);
    panel.querySelector(".cd-forms").onclick = (e) => {   // a form: its how-to-find text, and the map button tracks it
      const row = e.target.closest("li[data-form]");
      if (row) { view.focus = +row.dataset.form; renderDetail(); }
    };
    el("cd-map").onclick = () => { close(); setTrack(focus, false); zoomToTrack(); };
  }

  // ---- view controls ----
  function tabs(container, items, current, onPick) {
    container.innerHTML = items.map(([key, label]) =>
      `<button class="${String(key) === String(current) ? "on" : ""}" data-key="${key}">${label}</button>`).join("");
    container.onclick = (e) => {
      const button = e.target.closest("button[data-key]");
      if (button) onPick(button.dataset.key);
    };
  }

  function renderControls() {
    tabs(el("col-logs"), [...book.aniilogs.map((a) => [a.id, a.name]), [ALL, "All"]], view.log, (key) => setView({ log: key }));
    tabs(el("col-modes"), [["family", book.modes.family], ["form", book.modes.form]], view.mode, (key) => setView({ mode: key }));
    tabs(el("col-elements"), [["", "Any element"], ...Object.entries(book.elements).map(([k, e]) =>
      [k, `<img src="icons/${e.icon}" alt="">${e.name}`])], view.element, (key) => setView({ element: key }));
    const statuses = OFFLINE ? STATUS_FILTERS.filter(([key]) => key !== "stored") : STATUS_FILTERS;   // no storage offline
    tabs(el("col-status"), statuses, view.status, (key) => setView({ status: key }));
  }

  function setView(changes) {
    Object.assign(view, changes);
    const { log, mode, element, status } = view;
    localStorageSet(VIEW_KEY, JSON.stringify({ log, mode, element, status }));
    draw(true);
  }

  function select(key) {
    view.selected = key;
    view.focus = null;
    for (const tile of el("col-grid").querySelectorAll(".col-tile.selected")) tile.classList.remove("selected");
    const tile = key && el("col-grid").querySelector(`.col-tile[data-key="${key}"]`);
    if (tile) tile.classList.add("selected");
    renderDetail();
  }

  // Redraws only when the view or the data it shows changed (live reads come every few seconds)
  function draw(force = false) {
    if (!book || !isOpen) return;
    const key = JSON.stringify([handbook.states, [...ownedByForm.entries()], view]);
    if (!force && key === drawnKey) return;
    drawnKey = key;
    renderControls();
    renderCounters();
    renderGrid();
    renderDetail();
  }

  async function loadOwned() {
    try {
      const data = await getJson("api/collection");
      ownedByForm = new Map();
      for (const pet of data.owned || []) {
        if (!ownedByForm.has(pet.form)) ownedByForm.set(pet.form, []);
        ownedByForm.get(pet.form).push(pet);
      }
    } catch (e) { /* server busy: keep the last read */ }
    draw();
  }

  function loadBook() {
    loading = loading || fetch("collection.json", { cache: "no-store" }).then((r) => r.json()).then((data) => {
      book = data;
      Object.assign(extraPets, book.forms);   // the map's tracking card names forms that live on no map
      if (!book.aniilogs.some((a) => String(a.id) === String(view.log))) view.log = String(book.aniilogs[0].id);
    });
    return loading;
  }

  async function open() {
    isOpen = true;
    mapPaused = true;
    document.body.classList.add("collection-open");
    el("collection").classList.remove("hidden");
    try {
      await loadBook();
    } catch (e) {
      el("col-grid").innerHTML = '<p class="col-empty">No collection data yet: run tools/build_mapdata.py.</p>';
      return;
    }
    draw(true);
    if (OFFLINE) return;   // nothing live to read
    await loadOwned();
    clearInterval(ownedTimer);
    ownedTimer = setInterval(loadOwned, OWNED_POLL_MS);   // also picks up handbook changes (app.js polls it)
  }

  function close() {
    isOpen = false;
    mapPaused = false;
    clearInterval(ownedTimer);
    document.body.classList.remove("collection-open");
    el("collection").classList.add("hidden");
  }

  // ---- wiring ----
  el("collection-open").onclick = open;
  el("col-close").onclick = close;
  el("col-grid").onclick = (e) => {
    const tile = e.target.closest(".col-tile");
    if (tile) select(view.selected === tile.dataset.key ? null : tile.dataset.key);
  };
  el("col-search").oninput = (e) => { view.search = e.target.value.trim(); renderGrid(); };
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !isOpen) return;
    if (view.selected) select(null);
    else close();
  });

  // ?collection opens it on load; ?collection=<Aniilog id or "all"> picks the tab, &mode=form the mode
  const params = new URLSearchParams(location.search);
  if (params.has("collection")) {
    if (params.get("collection")) view.log = params.get("collection");
    if (params.get("mode")) view.mode = params.get("mode");
    if (params.get("select")) view.selected = params.get("select");
    open();
  }
  return { open, close };
})();
