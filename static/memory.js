(() => {
  "use strict";
  const CATS = ["Artists", "TV Shows", "Movies"];
  const MAX_SEEDS = 6;
  const STORE_KEY = "curio_memory_v1";
  const M = {category: "Artists", seeds: [], results: [], items: {}, reactions: {}, lastBody: null, busy: false, presetsLoaded: false};

  const safeUrl = url => (typeof url === "string" && /^https:\/\//i.test(url)) ? url : "";
  const clip = (value, max) => (typeof clipText === "function" ? clipText(value, max) : String(value || "").slice(0, max));
  const art = (item, cls = "artwork") => {
    const url = safeUrl(item.image);
    return url ? `<img class="${cls}" src="${esc(url)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=&quot;${cls}&quot;>◉</div>'">` : `<div class="${cls}">◉</div>`;
  };
  const slim = seed => ({
    entity_id: String(seed.entity_id), name: seed.name, category: seed.category,
    tags: (seed.tags || []).slice(0, 12), description: clip(seed.description || seed.short_description || "", 300),
    release_year: seed.release_year ?? null, image: safeUrl(seed.image)
  });
  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({birthYear: $("memoryBirthYear").value, region: $("memoryRegion").value, seeds: M.seeds}));
    } catch {}
  }
  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      $("memoryBirthYear").value = saved.birthYear || "";
      $("memoryRegion").value = saved.region || "";
      M.seeds = Array.isArray(saved.seeds) ? saved.seeds.filter(s => s && s.entity_id && CATS.includes(s.category)).slice(0, MAX_SEEDS) : [];
    } catch {}
  }
  function renderCats() {
    $("memoryCats").innerHTML = CATS.map(category => {
      const count = M.seeds.filter(s => s.category === category).length;
      return `<button type="button" class="category-tab ${category === M.category ? "active" : ""}" data-mcat="${esc(category)}">${esc(category)}<span class="tab-count">${count}</span></button>`;
    }).join("");
    $("memoryCats").querySelectorAll("[data-mcat]").forEach(button => button.addEventListener("click", () => {
      M.category = button.dataset.mcat; M.results = [];
      $("memorySearchInput").value = ""; $("memoryResults").innerHTML = ""; setMessage("memorySearchMessage", "");
      renderCats();
    }));
  }
  function renderSeeds() {
    $("memorySeedCount").textContent = `${M.seeds.length} / ${MAX_SEEDS}`;
    $("memorySeeds").innerHTML = M.seeds.length
      ? M.seeds.map(seed => `<div class="selected-chip"><span title="${esc(seed.category)}">${esc(seed.name)}</span><small class="chip-cat">${esc(seed.category)}</small><button type="button" data-mremove="${esc(seed.entity_id)}" aria-label="Remove ${esc(seed.name)}">×</button></div>`).join("")
      : `<div class="empty-note">Add a song they loved, a favourite show, a film…</div>`;
    $("memorySeeds").querySelectorAll("[data-mremove]").forEach(button => button.addEventListener("click", () => {
      M.seeds = M.seeds.filter(s => s.entity_id !== button.dataset.mremove);
      renderSeeds(); renderCats(); renderResults(); persist();
    }));
  }
  function renderResults() {
    const box = $("memoryResults");
    if (!M.results.length) { box.innerHTML = ""; return; }
    box.innerHTML = M.results.map((item, index) => {
      const picked = M.seeds.some(s => s.entity_id === item.entity_id);
      const full = M.seeds.length >= MAX_SEEDS;
      const year = item.release_year ? ` · ${esc(item.release_year)}` : "";
      return `<article class="result-card">${art(item)}<div class="result-info"><strong>${esc(item.name)}</strong><small>${esc(item.category)}${year}</small></div><button type="button" class="add-button" data-madd="${index}" ${picked || full ? "disabled" : ""} aria-label="${picked ? "Already added" : "Add " + esc(item.name)}">${picked ? "✓" : "+"}</button></article>`;
    }).join("");
    box.querySelectorAll("[data-madd]").forEach(button => button.addEventListener("click", () => addSeed(M.results[Number(button.dataset.madd)])));
  }
  function addSeed(item) {
    if (!item || M.seeds.length >= MAX_SEEDS || M.seeds.some(s => s.entity_id === item.entity_id)) return;
    M.seeds.push({...slim(item), category: item.category || M.category});
    renderSeeds(); renderCats(); renderResults(); persist();
  }
  async function searchSeeds() {
    const query = $("memorySearchInput").value.trim();
    if (!query) return setMessage("memorySearchMessage", "Enter a name or title to search.", true);
    setMessage("memorySearchMessage", "Searching Qloo…");
    $("memorySearchButton").disabled = true;
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}&category=${encodeURIComponent(M.category)}&limit=8`);
      const seen = new Set();
      M.results = (data.results || []).filter(r => r.entity_id && r.name && !seen.has(r.entity_id) && seen.add(r.entity_id)).map(r => ({...r, category: r.category || M.category}));
      renderResults();
      setMessage("memorySearchMessage", M.results.length ? `${M.results.length} results. Check it's the right one before adding.` : "Nothing found. Try the full name.");
    } catch (error) { setMessage("memorySearchMessage", error.message, true); }
    finally { $("memorySearchButton").disabled = false; }
  }
  async function loadPresets() {
    if (M.presetsLoaded) return;
    try {
      const data = await api("/api/memory/presets");
      M.presetsLoaded = true;
      $("memoryPresets").innerHTML = (data.presets || []).map(p => `<button type="button" class="memory-preset" data-preset="${esc(p.key)}"><strong>${esc(p.label)}</strong><small>${esc(p.blurb)}</small></button>`).join("");
      $("memoryPresets").querySelectorAll("[data-preset]").forEach(button => button.addEventListener("click", () => usePreset(button)));
    } catch {}
  }
  async function usePreset(button) {
    button.disabled = true;
    try {
      const data = await api(`/api/memory/preset/${encodeURIComponent(button.dataset.preset)}`, {method: "POST"});
      $("memoryBirthYear").value = data.birth_year; $("memoryRegion").value = data.region || "";
      M.seeds = (data.seeds || []).map(slim).map((s, i) => ({...s, category: data.seeds[i].category})).slice(0, MAX_SEEDS);
      renderSeeds(); renderCats(); renderResults(); persist();
      toast(`Loaded “${data.label}”. Check the favourites, then plan a session.`);
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }
  function requestBody(adapt) {
    const birthYear = Number($("memoryBirthYear").value);
    if (!Number.isInteger(birthYear) || birthYear < 1900 || birthYear > 2010) throw new Error("Enter a birth year between 1900 and 2010.");
    if (!M.seeds.length) throw new Error("Add at least one artist, show or film they loved.");
    return {birth_year: birthYear, region: $("memoryRegion").value.trim(), seeds: M.seeds.map(slim), adapt: Boolean(adapt)};
  }
  async function build(adapt = false) {
    if (M.busy) return;
    let body;
    try { body = requestBody(adapt); } catch (error) { return toast(error.message); }
    M.busy = true; M.lastBody = {...body, adapt: false};
    const output = $("memoryOutput"); output.classList.remove("hidden");
    output.innerHTML = `<div class="ai-loading" role="status" aria-live="polite"><div class="deep-radar"><span></span><span></span><span></span></div><div data-live-status></div></div>`;
    renderLiveProgress(output, {title: adapt ? "Reading the reactions" : "Reading their story", detail: "Working out which years of music and TV will mean the most."});
    $("memoryBuildButton").disabled = true;
    output.scrollIntoView({behavior: "smooth", block: "start"});
    try {
      const data = await apiStream("/api/memory/session/stream", {method: "POST", body: JSON.stringify(body)}, progress => renderLiveProgress(output, progress));
      if (!adapt) M.reactions = {};
      renderSession(data);
      persist();
    } catch (error) { output.innerHTML = `<div class="ai-error" role="alert">${esc(errorText(error))}</div>`; }
    finally { M.busy = false; $("memoryBuildButton").disabled = false; }
  }
  function listenMarkup(item) {
    const e = item.entity || {}; const links = e.links || {};
    if (e.category === "Artists") {
      const open = safeUrl(links.open);
      return `<div class="memory-listen" data-listen-box="${esc(e.entity_id)}">${safeUrl(links.embed) ? `<button type="button" class="mini-button explore" data-listen="${esc(e.entity_id)}">▶ Listen here</button>` : ""}${open ? `<a class="mini-button" href="${esc(open)}" target="_blank" rel="noopener noreferrer">${links.embed ? "Open in Spotify" : "Find on Spotify"}</a>` : ""}</div>`;
    }
    const providers = Array.isArray(e.provider_links) ? e.provider_links : [];
    const anchors = providers.filter(l => safeUrl(l.url)).slice(0, 2).map(l => `<a class="mini-button" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label || "Watch")}</a>`).join("");
    return anchors ? `<div class="memory-listen">${anchors}</div>` : "";
  }
  function reactionMarkup(id) {
    const current = M.reactions[id];
    if (window.CurioDiscoveryUI) return window.CurioDiscoveryUI.reactionMarkup(id, current);
    const options = [["love", "😊", "Lit up"], ["ok", "😐", "Okay"], ["upset", "😟", "Not for them"]];
    return `<div class="memory-reactions" role="group" aria-label="How did they respond?">${options.map(([key, icon, label]) => `<button type="button" class="reaction-btn ${current === key ? "active " + key : ""}" data-react="${key}" data-id="${esc(id)}" aria-pressed="${current === key}"><span aria-hidden="true">${icon}</span> ${label}</button>`).join("")}</div>`;
  }
  function itemCard(item, kind) {
    const e = item.entity || {}; const id = String(e.entity_id || "");
    M.items[id] = item;
    const year = e.year || e.release_year;
    const desc = clip(e.description || e.short_description || "", 170);
    const songs = ((e.facts || {}).notable_songs || []).slice(0, 3);
    return `<article class="memory-card" data-id="${esc(id)}">
      <div class="memory-card-top">${art(e, "memory-art")}<div><div class="rec-meta">${esc(e.category || "")}${year ? ` · ${esc(year)}` : ""}</div><h4>${esc(e.name)}</h4>${desc ? `<p class="memory-desc">${esc(desc)}</p>` : ""}</div></div>
      ${songs.length ? `<p class="memory-songs"><span>Songs to try</span> ${songs.map(esc).join(" · ")}</p>` : ""}
      ${item.prompt ? `<blockquote class="memory-prompt"><span>Ask</span>${esc(item.prompt)}</blockquote>` : ""}
      ${listenMarkup(item)}
      ${window.CurioDiscoveryUI ? window.CurioDiscoveryUI.whyMarkup(item.why, item.evidence, "memory-why discovery-why") : `<div class="memory-why"><span class="path-link-label">Why it's here</span><p>${esc(item.why || "")}</p>${typeof evidenceMarkup === "function" ? evidenceMarkup(item.evidence) : ""}</div>`}
      ${kind === "revisit" ? "" : reactionMarkup(id)}
    </article>`;
  }
  const section = (title, hint, items, kind) => items.length
    ? `<section class="memory-section"><div class="memory-section-head"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div><div class="memory-grid">${items.map(i => itemCard(i, kind)).join("")}</div></section>` : "";

  function starterCardMarkup(starter, index) {
    const question = typeof starter === "string" ? starter : (starter.question || starter.text || "");
    const category = typeof starter === "object" && starter.category ? starter.category : inferStarterCategory(question);
    const context = typeof starter === "object" && starter.context ? starter.context : "Let the conversation unfold naturally; there is no need to find a perfect answer.";
    const symbols = {"Music & memories":"♫", "Film & television":"▣", "Places & everyday life":"⌖", "Personal stories":"♡", "A gentle prompt":"✳"};
    const label = category || "A gentle prompt";
    return `<article class="conversation-card"><div class="conversation-card-top"><span class="conversation-card-icon" aria-hidden="true">${symbols[label] || "✳"}</span><span class="conversation-card-label">${esc(label)}</span><span class="conversation-card-index">${String(index + 1).padStart(2, "0")}</span></div><p class="conversation-question">${esc(question)}</p><p class="conversation-context">${esc(context)}</p></article>`;
  }
  function inferStarterCategory(question) {
    const q = String(question || "").toLowerCase();
    if (/song|music|singer|radio|dance|concert|band/.test(q)) return "Music & memories";
    if (/film|movie|show|television|tv|actor|cinema/.test(q)) return "Film & television";
    if (/home|street|town|school|holiday|travel|place|neighbourhood/.test(q)) return "Places & everyday life";
    if (/remember|childhood|family|friend|smile|story|younger|life/.test(q)) return "Personal stories";
    return "A gentle prompt";
  }
  function caregiverTipMarkup(tip) {
    const text = String(tip || "").toLowerCase();
    let label = "A gentle reminder", icon = "✳";
    if (/rush|pace|time|wait|slow|pause/.test(text)) { label = "Follow their pace"; icon = "◷"; }
    else if (/choice|choose|ask|lead|prefer|want/.test(text)) { label = "Make room for choice"; icon = "◌"; }
    else if (/comfort|upset|distress|stop|difficult|overwhelm/.test(text)) { label = "Follow their comfort"; icon = "♡"; }
    else if (/listen|story|share|together|connect|present/.test(text)) { label = "Stay present"; icon = "↗"; }
    return `<article class="caregiver-tip-card"><span class="caregiver-tip-icon" aria-hidden="true">${icon}</span><div><span class="caregiver-tip-label">${label}</span><p>${esc(tip)}</p></div></article>`;
  }

  function renderSession(data) {
    M.items = {};
    const gemini = data.mode === "gemini-agent";
    const modeText = gemini ? "Gemini chose the Qloo queries and adapted to what came back" : "Planned directly from Qloo (rule-based)";
    const picks = [...(data.music || []), ...(data.screen || [])];
    const stats = data.stats || {};
    const trace = window.CurioDiscoveryUI
      ? window.CurioDiscoveryUI.traceMarkup(data.trace, {suffix:`${stats.qloo_calls ?? 0} Qloo queries`})
      : `<details class="agent-trace"><summary>How the agent got here (${(data.trace || []).length} steps)</summary><ol>${(data.trace || []).map(t => `<li><code>${esc(t.tool)}</code> ${esc(t.summary)}</li>`).join("")}</ol></details>`;
    const adaptations = window.CurioDiscoveryUI
      ? window.CurioDiscoveryUI.adaptationMarkup(data.adaptations)
      : ((data.adaptations || []).length ? `<div class="adapt-banner"><strong>Adapted to reactions</strong><ul>${data.adaptations.map(a => `<li>${esc(a)}</li>`).join("")}</ul></div>` : "");
    const era = data.era || {};
    $("memoryOutput").innerHTML = `
      <div class="ai-output-head"><span class="ai-kicker">YOUR SESSION</span><h3>${esc(data.title)}</h3><p>${esc(data.intro)}</p>
        <p class="memory-meta"><span class="mode-badge ${gemini ? "gemini" : ""}">${esc(modeText)}</span><span class="mode-badge">Born ${esc(era.birth_year)} · years that stay with us: ${esc(era.from)}–${esc(era.to)}${era.region ? " · " + esc(era.region) : ""}</span></p>
        ${data.note ? `<div class="memory-warning" role="status"><strong>AI planner temporarily unavailable</strong><p>${esc(data.note)}</p></div>` : ""}</div>
      ${adaptations}
      ${section("Revisit", "You loved these last time.", data.revisit || [], "revisit")}
      ${section("Listen", "Start with one song. Let the music do the talking.", data.music || [], "music")}
      ${section("Watch & talk", "Shows and films from the same years. Talk about them or find a clip.", data.screen || [], "screen")}
      ${(data.starters || []).length ? `<section class="memory-section memory-guidance-section"><div class="memory-section-head"><span class="memory-section-kicker">GENTLE WAYS TO BEGIN</span><h3>Conversation starters</h3><p>Open questions that invite a story or a feeling, never a quiz. Follow their lead and take your time.</p></div><div class="conversation-starters">${data.starters.map((starter, index) => starterCardMarkup(starter, index)).join("")}</div></section>` : ""}
      ${(data.tips || []).length ? `<section class="memory-section caregiver-companion"><div class="caregiver-heading"><div class="caregiver-mark" aria-hidden="true">♡</div><div><span class="memory-section-kicker">A LITTLE SUPPORT FOR YOU</span><h3>For you, the caregiver</h3><p>Small reminders to help keep the experience comfortable and meaningful.</p></div></div><div class="caregiver-guidance-grid">${data.tips.map((tip, index) => caregiverTipMarkup(tip, index)).join("")}</div></section>` : ""}
      <div class="memory-controls"><button type="button" id="memoryAdaptButton" class="button button-primary" disabled>Adapt to reactions</button></div>
      <div id="memoryReactionHint">${window.CurioDiscoveryUI ? window.CurioDiscoveryUI.feedbackGuidanceMarkup("Mark what lit up, felt okay or wasn't right. Loved items become new starting points, the era narrows to where they cluster, and anything upsetting is removed.") : `<p class="inline-message">Mark what lit up, felt okay or wasn't right, then adapt to reactions.</p>`}</div>
      ${trace}
      <div class="interpretation-note">${picks.length} picks, each resolved to an entity Qloo returned (${esc(stats.entities_considered ?? 0)} candidates considered). The AI can choose queries, write questions and explain, but can't add items Qloo didn't return. This is a conversation aid, not medical advice.</div>`;
    updateAdaptButton();
  }
  function updateAdaptButton() {
    const button = $("memoryAdaptButton"); if (!button) return;
    const count = Object.keys(M.reactions).length;
    button.disabled = count === 0 || M.busy;
    button.textContent = count ? `Adapt to reactions (${count})` : "Adapt to reactions";
  }
  async function react(button) {
    const id = button.dataset.id; const reaction = button.dataset.react; const item = M.items[id];
    if (!item) return;
    const previous = M.reactions[id];
    M.reactions[id] = reaction;
    const card = button.closest(".memory-card");
    card.querySelector(".memory-reactions").outerHTML = reactionMarkup(id);
    updateAdaptButton();
    try {
      await api("/api/memory/reaction", {method: "POST", body: JSON.stringify({entity: item.entity, reaction})});
    } catch (error) {
      if (previous) M.reactions[id] = previous; else delete M.reactions[id];
      card.querySelector(".memory-reactions").outerHTML = reactionMarkup(id);
      updateAdaptButton(); toast(error.message);
    }
  }
  function toggleListen(button) {
    const box = button.closest(".memory-listen");
    const existing = box.parentElement.querySelector(".memory-embed");
    if (existing) { existing.remove(); button.textContent = "▶ Listen here"; return; }
    const item = M.items[button.dataset.listen]; const src = safeUrl(((item || {}).entity || {}).links?.embed);
    if (!src || !src.startsWith("https://open.spotify.com/embed/")) return;
    const frame = document.createElement("div"); frame.className = "memory-embed";
    frame.innerHTML = `<iframe src="${esc(src)}" width="100%" height="152" frameborder="0" allow="encrypted-media" loading="lazy" title="Spotify player"></iframe>`;
    box.insertAdjacentElement("afterend", frame); button.textContent = "■ Hide player";
  }
  function addSeedsFromCollection(items) {
    const result = {added: 0, unsupported: [], duplicates: 0, full: 0};
    const supported = Array.isArray(items) ? items : [];
    for (const item of supported) {
      if (!item || !item.entity_id || !item.name || !CATS.includes(item.category)) {
        if (item && item.category && !CATS.includes(item.category)) result.unsupported.push(item.category);
        continue;
      }
      if (M.seeds.some(seed => String(seed.entity_id) === String(item.entity_id))) {
        result.duplicates += 1;
        continue;
      }
      if (M.seeds.length >= MAX_SEEDS) {
        result.full += 1;
        continue;
      }
      M.seeds.push({...slim(item), category: item.category});
      result.added += 1;
    }
    result.unsupported = [...new Set(result.unsupported)];
    renderSeeds(); renderCats(); renderResults(); persist();
    return result;
  }
  function showCollectionImportNotice(result) {
    const el = $("memoryImportNotice");
    if (!el) return;
    const notes = [];
    if (result.added) notes.push(`${result.added} ${result.added === 1 ? "discovery was" : "discoveries were"} added from your Curio collection.`);
    if (result.unsupported.length) notes.push(`Not transferred: ${result.unsupported.join(", ")} (not yet supported in Memory Lane).`);
    if (result.duplicates) notes.push(`${result.duplicates} already in the favourites list.`);
    if (result.full) notes.push(`${result.full} not added because Memory Lane allows up to ${MAX_SEEDS} favourites.`);
    el.textContent = notes.join(" ");
    el.classList.toggle("hidden", !notes.length);
  }
  function openMemory() { showScreen("memoryScreen"); loadPresets(); }
  function bind() {
    $("homeMemoryDoor").addEventListener("click", openMemory);
    $("memoryBackButton").addEventListener("click", () => showScreen("homeScreen"));
    $("memorySearchButton").addEventListener("click", searchSeeds);
    $("memorySearchInput").addEventListener("keydown", event => { if (event.key === "Enter") searchSeeds(); });
    $("memoryBuildButton").addEventListener("click", () => build(false));
    ["memoryBirthYear", "memoryRegion"].forEach(id => $(id).addEventListener("change", persist));
    $("memoryOutput").addEventListener("click", event => {
      const target = event.target.closest("button");
      if (!target) return;
      if (target.dataset.react) react(target);
      else if (target.dataset.listen) toggleListen(target);
      else if (target.id === "memoryAdaptButton") build(true);
    });
  }
  window.CurioMemory = {renderSession, reactionMarkup, addSeedsFromCollection, showCollectionImportNotice, getSeeds: () => M.seeds.map(seed => ({...seed})), open: openMemory};
  restore(); renderCats(); renderSeeds(); bind();
})();
