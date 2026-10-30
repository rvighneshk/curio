const API_DEFAULT = "";
const CATEGORIES = ["Movies", "TV Shows", "Books", "Podcasts", "Artists"];
const state = {
  apiBase: API_DEFAULT,
  sessionId: getSessionId(),
  categories: CATEGORIES,
  activeCategory: "Movies",
  favourites: {},
  searchResults: [],
  journeyRoot: null,
  recommendationCategory: "Movies",
  recommendations: [],
  collectionType: "liked",
  collection: {liked: [], saved: [], skipped_ids: []},
  collectionSelection: new Set(),
  journey: [],
  busy: false,
  actionHistory: new Set(),
  itemActions: {},
  excludedIds: new Set(),
  alternativesByParent: {},
  journeyFinished: false,
  popularity: "any"
};

const PERSIST_KEY = "curio_journey_state_v1";
const STORAGE_SCHEMA_VERSION = 2;
function restoreLocalJourneyState() {
  try {
    const saved = JSON.parse(localStorage.getItem(PERSIST_KEY) || "{}");
    const schemaVersion = Number(saved.storageSchemaVersion || 0);
    if (schemaVersion < STORAGE_SCHEMA_VERSION) {
      const likedIds = (saved.collection?.liked || []).map(item => String(item.entity_id));
      const skippedIds = (saved.collection?.skipped_ids || []).map(String);
      saved.excludedIds = [...new Set([...likedIds, ...skippedIds])];
      saved.actionHistory = [...new Set([...likedIds, ...skippedIds])];
      saved.recommendations = [];
      saved.storageSchemaVersion = STORAGE_SCHEMA_VERSION;
      localStorage.setItem(PERSIST_KEY, JSON.stringify(saved));
    }
    state.excludedIds = new Set(saved.excludedIds || []);
    state.actionHistory = new Set(saved.actionHistory || []);
    state.alternativesByParent = saved.alternativesByParent || {};
    state.journeyFinished = Boolean(saved.journeyFinished);
    state.favourites = saved.favourites || {};
    state.journeyRoot = saved.journeyRoot || null;
    state.journey = saved.journey || [];
    state.collection = saved.collection || {liked: [], saved: [], skipped_ids: []};
    state.recommendationCategory = saved.recommendationCategory || "Movies";
    state.recommendations = saved.recommendations || [];
    state.activeJourneyNode = saved.activeJourneyNode || null;
  } catch {}
}
function persistLocalJourneyState() {
  try {
    localStorage.setItem(PERSIST_KEY, JSON.stringify({
      storageSchemaVersion: STORAGE_SCHEMA_VERSION,
      excludedIds: [...state.excludedIds],
      actionHistory: [...state.actionHistory],
      alternativesByParent: state.alternativesByParent,
      journeyFinished: state.journeyFinished,
      favourites: state.favourites,
      journeyRoot: state.journeyRoot,
      journey: state.journey,
      collection: state.collection,
      recommendationCategory: state.recommendationCategory,
      recommendations: state.recommendations,
      activeJourneyNode: state.activeJourneyNode
    }));
  } catch {}
}
restoreLocalJourneyState();

function getSessionId() {
  const key = "curio_session_id";
  let id = localStorage.getItem(key);
  if (!id) {
    id = `rh-${crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("")}`;
    localStorage.setItem(key, id);
  }
  return id;
}
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const keyOf = (item) => String(item.entity_id || `${item.category || ""}:${item.name || ""}`).toLowerCase();
const currentFavourites = () => Object.values(state.favourites).flat();
const holeId = (item) => String(item.entity_id);
const activeSeed = () => state.journeyRoot;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}
function setMessage(id, message = "", error = false) {
  const el = $(id);
  el.textContent = message;
  el.classList.toggle("error", error);
}
async function api(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, {
    ...options,
    headers: {"Content-Type": "application/json", "X-Session-ID": state.sessionId, ...(options.headers || {})}
  });
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) { const detail = data.detail?.message || data.detail || data.message; throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || `Request failed (${response.status})`)); }
  return data;
}
async function apiStream(path, options = {}, onProgress = () => {}) {
  const response = await fetch(`${state.apiBase}${path}`, {
    ...options,
    headers: {"Content-Type": "application/json", "Accept": "text/event-stream", "X-Session-ID": state.sessionId, ...(options.headers || {})}
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch {}
    const detail = data.detail?.message || data.detail || data.message;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || `Request failed (${response.status})`));
  }
  if (!response.body) throw new Error("Live progress is unavailable in this browser.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  const consume = block => {
    let eventName = "message";
    const dataLines = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    let payload;
    try { payload = JSON.parse(dataLines.join("\n")); } catch { return; }
    if (eventName === "progress") onProgress(payload);
    else if (eventName === "result") result = payload.data;
    else if (eventName === "error") throw new Error(payload.message || "The operation failed.");
  };
  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) consume(block);
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  if (!result) throw new Error("The operation ended before Curio returned a result.");
  return result;
}
function renderLiveProgress(container, progress) {
  const target = container.querySelector("[data-live-status]");
  if (!target) return;
  target.innerHTML = `<strong>${esc(progress.title || "Working on your discovery")}</strong><p>${esc(progress.detail || "Curio is working with Qloo's cultural graph.")}</p>`;
}
function normalize(item, categoryFallback = state.activeCategory) {
  const asObject = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const props = asObject(item.properties);
  return {
    ...item,
    entity_id: String(item.entity_id || item.id || item.entity?.id || ""),
    name: item.name || item.title || item.entity?.name || "Untitled",
    category: item.category || categoryFallback,
    type: item.type || item.category || categoryFallback,
    description: item.description || props.description || item.short_description || "",
    short_description: item.short_description || "",
    image: item.image || item.image_url || props.image || item.artwork || "",
    properties: props,
    external: asObject(item.external),
    ratings: asObject(item.ratings),
    provider_links: item.provider_links && typeof item.provider_links === "object" ? item.provider_links : {},
    review_links: item.review_links && typeof item.review_links === "object" ? item.review_links : {}
  };
}
function linkEntries(item) {
  const entries = [];
  const add = (label, url) => {
    if (typeof url !== "string" || !/^https?:\/\//i.test(url)) return;
    let parsed;
    try { parsed = new URL(url); } catch { return; }
    const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
    const providers = [
      ["imdb.com", "IMDb"], ["rottentomatoes.com", "Rotten Tomatoes"],
      ["netflix.com", "Netflix"], ["letterboxd.com", "Letterboxd"],
      ["openlibrary.org", "Open Library"], ["spotify.com", "Spotify"],
      ["youtube.com", "YouTube"], ["youtu.be", "YouTube"],
      ["music.apple.com", "Apple Music"], ["tv.apple.com", "Apple TV"],
      ["disneyplus.com", "Disney+"], ["primevideo.com", "Prime Video"],
      ["max.com", "Max"], ["hulu.com", "Hulu"], ["goodreads.com", "Goodreads"]
    ];
    const match = providers.find(([domain]) => host === domain || host.endsWith(`.${domain}`));
    if (!match) return;
    if (!entries.some(entry => entry.url === url)) entries.push({label: match[1], url});
  };
  const visit = (obj, path = "") => {
    if (!obj || typeof obj !== "object") return;
    if (Array.isArray(obj)) { obj.forEach(v => visit(v, path)); return; }
    for (const [key, value] of Object.entries(obj)) {
      const nextPath = path ? `${path} ${key}` : key;
      if (typeof value === "string") {
        if (/^https?:\/\//i.test(value)) add(nextPath, value);
        else if (/imdb/i.test(nextPath) && /^tt\d{5,}$/.test(value)) add("IMDb", `https://www.imdb.com/title/${value}/`);
      } else if (value && typeof value === "object") {
        const id = value.id || value.entity_id;
        if (/imdb/i.test(nextPath) && typeof id === "string" && /^tt\d{5,}$/.test(id)) add("IMDb", `https://www.imdb.com/title/${id}/`);
        visit(value, nextPath);
      }
    }
  };
  visit(item.external);
  visit(item.properties?.external);
  visit(item.provider_links);
  visit(item.review_links);
  visit(item.properties);

  const category = item.category || state.recommendationCategory;
  const props = item.properties || {};
  const title = item.name || item.title || "";
  if (category === "Books") {
    const isbn = props.isbn13 || item.isbn13 || props.isbn10 || item.isbn10;
    if (isbn && /^[0-9Xx-]+$/.test(String(isbn))) add("Open Library", `https://openlibrary.org/isbn/${encodeURIComponent(String(isbn).replace(/-/g, ""))}`);
  }
  if (category === "Movies" || category === "TV Shows") {
    const netflix = entries.find(entry => entry.label === "Netflix");
    for (let i = entries.length - 1; i >= 0; i--) {
      if (["YouTube", "Trailer", "Netflix"].includes(entries[i].label)) entries.splice(i, 1);
    }
    if (netflix) entries.push(netflix);
    entries.push({label:"Trailer", url:`https://www.youtube.com/results?search_query=${encodeURIComponent(`${title} ${category === "TV Shows" ? "official trailer tv series" : "official movie trailer"}`)}`, generated:true});
  }
  if (category === "Artists" || category === "Podcasts") {
    if (!entries.some(entry => entry.label === "Spotify")) entries.push({label:"Spotify", url:`https://open.spotify.com/search/${encodeURIComponent(title)}`, generated:true});
  }
  const deduped = [];
  const seenLabels = new Set();
  for (const entry of entries) {
    if (seenLabels.has(entry.label)) continue;
    seenLabels.add(entry.label);
    deduped.push(entry);
  }
  const order = ["IMDb", "Rotten Tomatoes", "Letterboxd", "Netflix", "Prime Video", "Disney+", "Max", "Hulu", "Apple TV", "Spotify", "Apple Music", "Trailer", "YouTube", "Open Library", "Goodreads"];
  return deduped.sort((a, b) => order.indexOf(a.label) - order.indexOf(b.label)).slice(0, 9);
}

function sourceIcon(label) {
  const icons = {
    "IMDb": ["https://cdn.simpleicons.org/imdb/F5C518", "IMDb"],
    "Rotten Tomatoes": ["https://cdn.simpleicons.org/rottentomatoes/FA320A", "Rotten Tomatoes"],
    "Letterboxd": ["https://cdn.simpleicons.org/letterboxd/00E054", "Letterboxd"],
    "Netflix": ["https://cdn.simpleicons.org/netflix/E50914", "Netflix"],
    "Spotify": ["https://cdn.simpleicons.org/spotify/1ED760", "Spotify"],
    "Apple Music": ["https://cdn.simpleicons.org/applemusic/FA243C", "Apple Music"],
    "YouTube": ["https://cdn.simpleicons.org/youtube/FF0033", "YouTube"],
    "Trailer": ["https://cdn.simpleicons.org/youtube/FF0033", "Find trailer on YouTube"],
    "Disney+": ["https://cdn.simpleicons.org/disneyplus/FFFFFF", "Disney+"],
    "Prime Video": ["https://cdn.simpleicons.org/primevideo/00A8E1", "Prime Video"],
    "Apple TV": ["https://cdn.simpleicons.org/appletv/FFFFFF", "Apple TV"],
    "Max": ["https://cdn.simpleicons.org/hbo/FFFFFF", "Max"],
    "Hulu": ["https://cdn.simpleicons.org/hulu/1CE783", "Hulu"],
    "Open Library": ["https://openlibrary.org/static/images/openlibrary-logo-tighter.svg", "Open Library"],
    "Goodreads": ["https://cdn.simpleicons.org/goodreads/FFFFFF", "Goodreads"]
  };
  const found = icons[label];
  if (found) return `<img class="source-icon" src="${found[0]}" alt="${esc(label)}" title="${esc(label)}" loading="lazy" onerror="this.outerHTML='<span class=&quot;source-fallback&quot;>${esc(label.slice(0,2))}</span>'">`;
  return `<span class="source-fallback" title="${esc(label)}">${esc(label.slice(0,2))}</span>`;
}

function ratingEntries(item) {
  const ratings = [];
  const add = (label, value) => {
    if (value === null || value === undefined || value === "") return;
    const raw = typeof value === "object" ? value.value ?? value.rating ?? value.score : value;
    if (raw === null || raw === undefined || raw === "" || typeof raw === "object") return;
    const normalized = String(raw).trim();
    if (!/^(?:\d+(?:\.\d+)?(?:\s*\/\s*\d+(?:\.\d+)?)?|\d{1,3}\s*%)$/.test(normalized)) return;
    const pretty = /imdb/i.test(label) ? "IMDb" : /rotten|tomato/i.test(label) ? "Rotten Tomatoes" : /letterboxd/i.test(label) ? "Letterboxd" : /metacritic/i.test(label) ? "Metacritic" : label;
    if (!ratings.some(r => r.label === pretty)) ratings.push({label: pretty, value: normalized});
  };
  const scan = (obj, path = "") => {
    if (!obj || typeof obj !== "object") return;
    for (const [key, value] of Object.entries(obj)) {
      const next = path ? `${path} ${key}` : key;
      if (!/content[_ ]?rating|parental[_ ]?rating/i.test(next) && /imdb[_ ]?rating|rotten|tomatometer|letterboxd|metacritic|rating|score/i.test(next) && (typeof value !== "object" || value === null)) add(next, value);
      else if (value && typeof value === "object") scan(value, next);
    }
  };
  scan(item.ratings);
  scan(item.properties?.ratings);
  scan(item.properties);
  return ratings.slice(0, 3);
}

function metadataMarkup(item) {
  const props = item.properties || {};
  const fields = [];
  const add = (label, value) => {
    if (value !== undefined && value !== null && String(value).trim()) fields.push(`<span class="metadata-chip"><b>${esc(label)}</b> ${esc(value)}</span>`);
  };
  if (item.category === "Movies" || item.category === "TV Shows") {
    add("Year", props.release_year || props.release_date || item.release_year || item.release_date);
    add("Rated", props.content_rating || item.content_rating);
    add("Studio", props.production_company || item.production_company);
    if (props.release_country || item.release_country) add("Country", props.release_country || item.release_country);
  } else if (item.category === "Books") {
    add("Author", props.author || props.authors || item.author || item.authors);
    add("Published", props.publication_year || props.publication_date || item.publication_year || item.publication_date);
    add("Pages", props.page_count || item.page_count);
    add("Format", props.format || item.format);
    add("ISBN", props.isbn13 || props.isbn10 || item.isbn13 || item.isbn10);
  } else if (item.category === "Artists") {
    add("Duration", props.duration || item.duration);
  }
  const ratings = ratingEntries(item);
  const links = linkEntries(item);
  return `${fields.length ? `<div class="metadata-row">${fields.slice(0, item.category === "Books" ? 5 : 4).join("")}</div>` : ""}${ratings.length ? `<div class="ratings-row">${ratings.map(r => `<span class="rating-chip"><span>★</span><b>${esc(r.label)}</b> ${esc(r.value)}</span>`).join("")}</div>` : ""}${links.length ? `<div class="external-links" aria-label="Available sources">${links.map(link => `<a class="external-link" href="${esc(link.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(link.label)}" title="${esc(link.label)}">${sourceIcon(link.label)}</a>`).join("")}</div>` : ""}`;
}

function imgMarkup(item, cls = "artwork") {
  const url = item.image;
  if (url && /^https?:\/\//i.test(url)) return `<img class="${cls}" src="${esc(url)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=&quot;${cls}&quot;>◉</div>'">`;
  return `<div class="${cls}">◉</div>`;
}
let currentScreen = "homeScreen";
let collectionReturnScreen = "homeScreen";
function showScreen(name) {
  const screens = ["homeScreen", "setupScreen", "discoveryScreen", "collectionScreen", "memoryScreen"];
  if (!screens.includes(name)) return;
  if (name === "collectionScreen" && currentScreen !== "collectionScreen") collectionReturnScreen = currentScreen;
  screens.forEach(id => $(id).classList.add("hidden"));
  $(name).classList.remove("hidden");
  $("homeNavButton").classList.toggle("hidden", name === "homeScreen");
  currentScreen = name;
  window.scrollTo({top: 0, behavior: "smooth"});
}
function renderTabs() {
  $("categoryTabs").innerHTML = state.categories.map(category => {
    const count = (state.favourites[category] || []).length;
    return `<button class="category-tab ${category === state.activeCategory ? "active" : ""}" data-category="${esc(category)}">${esc(category)}<span class="tab-count">${count}/3</span></button>`;
  }).join("");
  $("categoryTabs").querySelectorAll("[data-category]").forEach(button => button.addEventListener("click", () => {
    state.activeCategory = button.dataset.category;
    renderTabs(); renderSelected(); $("searchInput").value = ""; $("searchResults").innerHTML = "";
    setMessage("searchMessage", "");
  }));
}
function renderSelected() {
  const items = state.favourites[state.activeCategory] || [];
  $("categoryPickCount").textContent = `${items.length} / 3`;
  $("favouriteCount").textContent = currentFavourites().length;
  $("selectedFavourites").innerHTML = items.length ? items.map(item => `<div class="selected-chip"><span>${esc(item.name)}</span><button data-remove="${esc(item.entity_id)}" aria-label="Remove ${esc(item.name)}">×</button></div>`).join("") : `<div class="empty-note">Your picks for ${esc(state.activeCategory)} will appear here.</div>`;
  $("selectedFavourites").querySelectorAll("[data-remove]").forEach(button => button.addEventListener("click", () => removeFavourite(button.dataset.remove)));
}
function renderSearchResults() {
  const results = state.searchResults;
  $("searchResults").innerHTML = results.length ? results.map((item, index) => {
    const selected = currentFavourites().some(f => keyOf(f) === keyOf(item));
    const full = (state.favourites[state.activeCategory] || []).length >= 3;
    return `<article class="result-card">${imgMarkup(item)}<div class="result-info"><strong>${esc(item.name)}</strong><small>${esc(item.category)}</small></div><button class="add-button" data-add="${index}" ${selected || full ? "disabled" : ""} title="${selected ? "Already selected" : full ? "Maximum 3 favourites" : "Add favourite"}">${selected ? "✓" : "+"}</button></article>`;
  }).join("") : `<div class="empty-note">Search for something you love to see results here.</div>`;
  $("searchResults").querySelectorAll("[data-add]").forEach(button => button.addEventListener("click", () => addFavourite(state.searchResults[Number(button.dataset.add)])));
}
async function searchEntities() {
  const query = $("searchInput").value.trim();
  if (!query) return setMessage("searchMessage", "Enter a title or name to search.", true);
  setMessage("searchMessage", "Searching Qloo…");
  $("searchButton").disabled = true;
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}&category=${encodeURIComponent(state.activeCategory)}&limit=10`);
    const unique = new Map();
    (data.results || data.entities || []).map(item => normalize(item, state.activeCategory)).filter(item => item.entity_id && item.name).forEach(item => { if (!unique.has(keyOf(item))) unique.set(keyOf(item), item); });
    state.searchResults = [...unique.values()];
    renderSearchResults();
    setMessage("searchMessage", `${state.searchResults.length} result${state.searchResults.length === 1 ? "" : "s"} found.`);
  } catch (error) { setMessage("searchMessage", error.message, true); }
  finally { $("searchButton").disabled = false; }
}
async function addFavourite(rawItem) {
  const item = normalize(rawItem, state.activeCategory);
  const list = state.favourites[state.activeCategory] || [];
  if (list.length >= 3 || currentFavourites().some(f => keyOf(f) === keyOf(item))) return;
  try {
    await api("/api/onboarding/favourites", {method:"POST", body:JSON.stringify(item)});
    state.favourites[state.activeCategory] = [...list, item];
    renderTabs(); renderSelected(); renderSearchResults();
    toast(`${item.name} added to your favourites`);
  } catch (error) { toast(error.message); }
}
async function removeFavourite(entityId) {
  try {
    await api(`/api/onboarding/favourites/${encodeURIComponent(state.activeCategory)}/${encodeURIComponent(entityId)}`, {method:"DELETE"});
    state.favourites[state.activeCategory] = (state.favourites[state.activeCategory] || []).filter(item => item.entity_id !== entityId);
    renderTabs(); renderSelected(); renderSearchResults();
  } catch (error) { toast(error.message); }
}
async function loadFavourites() {
  try {
    const data = await api("/api/onboarding/favourites");
    state.favourites = data.favourites || {};
    state.categories.forEach(c => { if (!state.favourites[c]) state.favourites[c] = []; });
    renderTabs(); renderSelected();
  } catch (error) { toast(`API connection issue: ${error.message}`); }
}
function enterDiscovery() {
  if (!currentFavourites().length) return toast("Pick at least one favourite first.");
  showScreen("discoveryScreen");
  renderHoleCards();
}
function renderHoleCards() {
  const items = currentFavourites();
  $("journeyRootList").innerHTML = items.map(item => `<button class="hole-card" data-hole="${esc(item.entity_id)}"><div class="hole-art">${item.image && /^https?:\/\//i.test(item.image) ? `<img src="${esc(item.image)}" alt="" onerror="this.remove()">` : ""}</div><div class="hole-body"><h3>${esc(item.name)}</h3><p>${esc(item.description || `Your ${item.category} portal starts here. Step through and follow the cultural connections.`)}</p><div class="hole-footer"><span>${esc(item.category)}</span><span>Enter portal </span></div></div></button>`).join("");
  $("journeyRootList").querySelectorAll("[data-hole]").forEach(button => button.addEventListener("click", () => openHole(items.find(item => item.entity_id === button.dataset.hole))));
}
function renderCategoryOptions() {
  $("recommendationCategory").innerHTML = state.categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  $("recommendationCategory").value = state.recommendationCategory;
}
async function openHole(item) {
  if (!item) return;
  state.journeyRoot = item;
  state.recommendationCategory = item.category;
  state.journeyFinished = false;
  persistLocalJourneyState();
  state.recommendations = [];
  $("activeHoleTitle").textContent = item.name;
  $("activeHoleSubtitle").textContent = `${item.category} · Discoveries branch from the item you're currently exploring.`;
  $("explorationPanel").classList.remove("hidden");
  renderCategoryOptions();
  await loadJourney();
  await fetchRecommendations();
  $("explorationPanel").scrollIntoView({behavior:"smooth",block:"start"});
}
function renderRecommendations() {
  const feedbackGuidance = state.recommendations.length && window.CurioDiscoveryUI
    ? window.CurioDiscoveryUI.feedbackGuidanceMarkup("Like discoveries that resonate, skip what doesn't, and explore connections to steer what comes next.")
    : "";
  $("recommendationGrid").innerHTML = state.recommendations.length ? `${feedbackGuidance}${state.recommendations.map((item, index) => {
    const id = keyOf(item);
    const actions = state.itemActions[item.entity_id] || {};
    const liked = Boolean(actions.liked) || (state.collection.liked || []).some(entry => String(entry.entity_id) === String(item.entity_id));
    return `<article class="recommendation-card card-enter" data-card-key="${esc(id)}" data-entity-id="${esc(item.entity_id)}"><div class="rec-art">${item.image && /^https?:\/\//i.test(item.image) ? `<img src="${esc(item.image)}" alt="" loading="lazy" onerror="this.remove()">` : "◉"}<span class="art-vignette"></span></div><div class="rec-content"><div class="rec-meta">${esc(item.category || state.recommendationCategory)}</div><h3>${esc(item.name)}</h3>${(item.description || item.short_description) ? `<p class="rec-description">${esc(item.description || item.short_description)}</p>` : (item.category !== "Books" ? `<p class="rec-description">A possible next discovery through this portal.</p>` : "")}${metadataMarkup(item)}${whyMarkup(item.why || item.explanation || item.reason, item.evidence, "rec-why")}<div class="rec-actions"><button class="mini-button like ${liked ? "is-done" : ""}" data-action="like" data-feedback="love" data-index="${index}" title="${liked ? "Unlike" : "❤ Like"}" aria-pressed="${liked ? "true" : "false"}"> ${liked ? "❤ Liked" : "❤ Like"}</button><button class="mini-button skip-button" data-action="skip" data-feedback="upset" data-index="${index}" title="Skip this recommendation">✕ Skip</button><button class="mini-button explore" data-action="explore" data-index="${index}" title="Explore"><span></span>Explore</button></div></div></article>`;
  }).join("")}` : `<div class="journey-empty">No recommendations to show yet. Try Find more, another category, or a different popularity setting.</div>`;
  $("recommendationGrid").querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => handleRecommendationAction(button.dataset.action, Number(button.dataset.index))));
}

async function fetchRecommendations() {
  const seed = state.journeyRoot;
  if (!seed) return;
  if (state.journeyFinished) {
    toast("Your journey is complete. Resume it before finding more.");
    return;
  }
  setMessage("recommendationMessage", "Finding fresh discoveries…");
  $("findMoreButton").disabled = true;
  if (!state.recommendations.length) $("recommendationGrid").innerHTML = Array.from({length: 3}, () => `<div class="rec-skeleton" aria-hidden="true"><div></div><span></span><span></span></div>`).join("");
  try {
    const activeParentId = await getActiveParentId();
    const exclusions = new Set([
      ...state.excludedIds,
      ...state.actionHistory,
      ...currentFavourites().map(item => String(item.entity_id)),
      String(seed.entity_id)
    ]);
    const data = await api("/api/recommendations", {
      method: "POST",
      body: JSON.stringify({
        seed_entity_id: activeParentId || seed.entity_id,
        seed_category: seed.category,
        journey_id: holeId(seed),
        category: state.recommendationCategory,
        limit: 10,
        popularity: state.popularity,
        exclude_ids: [...exclusions].slice(-200)
      })
    });
    const incoming = (data.results || []).map(item => normalize(item, state.recommendationCategory));
    const fresh = [];
    const seen = new Set(exclusions);
    for (const item of incoming) {
      const id = String(item.entity_id || "");
      const key = keyOf(item);
      if (!id || seen.has(id) || seen.has(key)) continue;
      seen.add(id);
      seen.add(key);
      fresh.push(item);
      if (fresh.length >= 10) break;
    }
    state.recommendations = fresh;
    const parentKey = String(activeParentId || seed.entity_id);
    const existing = state.alternativesByParent[parentKey] || [];
    const branchSeen = new Set(existing.map(item => String(item.entity_id)));
    state.alternativesByParent[parentKey] = [...existing, ...incoming.filter(item => item.entity_id && !branchSeen.has(String(item.entity_id)))];
    persistLocalJourneyState();
    renderRecommendations();
    renderJourney();
    const seedName = ((data.signals || [])[0] || {}).name || seed.name;
    const relaxed = (data.relaxed || []).includes("popularity") && state.popularity !== "any";
    setMessage("recommendationMessage", fresh.length
      ? `${fresh.length} fresh discoveries. Qloo matched fans of ${seedName} directly; topical results come first.${relaxed ? " Nothing matched that popularity filter, so it was relaxed." : ""}`
      : (data.notice || "No unseen recommendations came back. Try Find more, another category, or a different popularity setting."), !fresh.length);
  } catch (error) {
    setMessage("recommendationMessage", typeof error.message === "string" ? error.message : JSON.stringify(error.message), true);
  } finally {
    $("findMoreButton").disabled = false;
  }
}

async function handleRecommendationAction(action, index) {
  const item = state.recommendations[index];
  if (!item || !state.journeyRoot) return;
  if (state.journeyFinished) {
    toast("Your journey is complete. Resume it before making changes.");
    return;
  }
  const entityId = String(item.entity_id);
  const wasLiked = Boolean((state.itemActions[entityId] || {}).liked) || (state.collection.liked || []).some(entry => String(entry.entity_id) === entityId);
  const effectiveAction = action === "like" && wasLiked ? "unlike" : action;
  const parentId = await getActiveParentId();
  const payload = {entity_id:entityId, action:effectiveAction, journey_id:holeId(state.journeyRoot), parent_id:parentId || null, entity:item};
  if (effectiveAction === "like" || effectiveAction === "explore") payload.seed_entity = state.journeyRoot;
  try {
    await api("/api/recommendations/actions", {method:"POST", body:JSON.stringify(payload)});
    if (["like", "unlike", "skip", "explore"].includes(effectiveAction)) {
      state.actionHistory.add(entityId);
      state.excludedIds.add(entityId);
    }
    if (effectiveAction === "like") {
      state.itemActions[entityId] = {...(state.itemActions[entityId] || {}), liked:true};
      toast(`Liked ${item.name}`);
      updateRecommendationLikeButton(entityId, true);
    } else if (effectiveAction === "unlike") {
      state.itemActions[entityId] = {...(state.itemActions[entityId] || {}), liked:false};
      toast(`Unliked ${item.name}`);
      updateRecommendationLikeButton(entityId, false);
    } else if (effectiveAction === "skip") {
      toast("Discovery skipped");
      state.recommendations.splice(index, 1);
    } else if (effectiveAction === "explore") {
      toast(`Going deeper into ${item.name}`);
      state.activeJourneyNode = entityId;
      state.recommendations = [];
      await loadJourney();
      await fetchRecommendations();
      $("activeHoleSubtitle").textContent = `Exploring from ${item.name} · ${item.category}`;
      persistLocalJourneyState();
      return;
    }
    await loadCollection();
    await loadJourney();
    persistLocalJourneyState();
    if (effectiveAction === "skip") renderRecommendations();
    renderJourney();
  } catch (error) {
    toast(error.message);
  }
}

function updateRecommendationLikeButton(entityId, liked) {
  const card = [...document.querySelectorAll(".recommendation-card[data-entity-id]")]
    .find(node => String(node.dataset.entityId) === String(entityId));
  const button = card?.querySelector('[data-action="like"]');
  if (!button) return;
  button.classList.toggle("is-done", liked);
  button.setAttribute("aria-pressed", String(liked));
  button.title = liked ? "Unlike" : "❤ Like";
  button.textContent = liked ? "❤ Liked" : "❤ Like";
}

async function getActiveParentId() {
  try { const data = await api(`/api/journey/${encodeURIComponent(holeId(state.journeyRoot))}`); return data.active_parent_id || state.journeyRoot.entity_id; }
  catch { return state.journeyRoot.entity_id; }
}
async function loadJourney() {
  if (!state.journeyRoot) return;
  try {
    const data = await api(`/api/journey/${encodeURIComponent(holeId(state.journeyRoot))}`);
    state.journey = data.journey || [];
    persistLocalJourneyState();
    renderJourney();
  } catch (error) { $("journeyMap").innerHTML = `<div class="journey-empty">${esc(error.message)}</div>`; }
}
function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportJourneyJson() {
  const nodes = state.journey || [];
  const likedIds = new Set((state.collection.liked || []).map(item => String(item.entity_id)));
  const likedNodes = nodes.filter(node => !node.parent_id || likedIds.has(String(node.entity_id)) || node.feedback === "liked");
  const payload = {
    product: "Curio",
    exported_at: new Date().toISOString(),
    root: state.journeyRoot ? {entity_id: state.journeyRoot.entity_id, name: state.journeyRoot.name, category: state.journeyRoot.category || state.journeyRoot.type || null} : null,
    liked_discoveries: likedNodes.map(node => ({
      entity_id: node.entity_id,
      name: node.name,
      category: node.category || node.type || null,
      parent_id: node.parent_id || null,
      image: node.image || null,
      feedback: node.feedback || "liked"
    })),
    liked_collection: (state.collection.liked || []).map(item => ({entity_id:item.entity_id, name:item.name, category:item.category || null, parent_id:item.parent_id || null})),
    exploration_order: nodes.map((node, index) => ({entity_id:node.entity_id, order:index + 1})),
    statistics: {
      discoveries_liked: (state.collection.liked || []).length,
      categories_explored: new Set((state.collection.liked || []).map(item => item.category).filter(Boolean)).size,
      connections_made: nodes.filter(node => node.parent_id).length
    }
  };
  downloadBlob("curio-journey.json", new Blob([JSON.stringify(payload, null, 2)], {type:"application/json;charset=utf-8"}));
  toast("Journey JSON exported.");
}
function exportJourneyPng() {
  const nodes = state.journey || [];
  const root = nodes.find(node => !node.parent_id) || state.journeyRoot;
  if (!root) { toast("Start a journey before exporting the graph."); return; }
  const likedIds = new Set((state.collection.liked || []).map(item => String(item.entity_id)));
  const liked = nodes.filter(node => likedIds.has(String(node.entity_id)) || node.feedback === "liked");
  const visible = [root, ...liked.filter(node => String(node.entity_id) !== String(root.entity_id))];
  const width = Math.max(900, visible.length * 245 + 100);
  const height = 440;
  const canvas = document.createElement("canvas");
  canvas.width = width * 2; canvas.height = height * 2;
  const ctx = canvas.getContext("2d");
  ctx.scale(2, 2);
  ctx.fillStyle = "#101018"; ctx.fillRect(0, 0, width, height);
  const gradient = ctx.createLinearGradient(0, 0, width, height); gradient.addColorStop(0, "#211b35"); gradient.addColorStop(1, "#101018");
  ctx.fillStyle = gradient; ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#f3efff"; ctx.font = "600 27px Arial"; ctx.fillText("Curio — Your Cultural Journey", 42, 48);
  ctx.fillStyle = "#a99fc8"; ctx.font = "14px Arial"; ctx.fillText("Follow your curiosity.", 43, 73);
  const boxW = 180, boxH = 130, gap = 55, startX = 42, y = 145;
  visible.forEach((node, i) => {
    const x = startX + i * (boxW + gap);
    if (i) {
      ctx.strokeStyle = "#a78bfa"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x - gap + 5, y + boxH / 2); ctx.lineTo(x - 8, y + boxH / 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x - 16, y + boxH / 2 - 6); ctx.lineTo(x - 8, y + boxH / 2); ctx.lineTo(x - 16, y + boxH / 2 + 6); ctx.stroke();
    }
    ctx.fillStyle = i === visible.length - 1 ? "#27203b" : "#191824"; ctx.strokeStyle = i === visible.length - 1 ? "#c4b5fd" : "#65558d"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.roundRect(x, y, boxW, boxH, 14); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#b9a0ff"; ctx.font = "12px Arial"; ctx.fillText(i === 0 ? "STARTING FAVOURITE" : "LIKED DISCOVERY", x + 13, y + 24);
    ctx.fillStyle = "#f3efff"; ctx.font = "600 15px Arial";
    const words = String(node.name || "Discovery").split(/\s+/); let line = "", lines = [];
    for (const word of words) { const test = line ? line + " " + word : word; if (ctx.measureText(test).width > boxW - 26 && line) { lines.push(line); line = word; } else line = test; }
    if (line) lines.push(line); lines.slice(0, 3).forEach((text, j) => ctx.fillText(text, x + 13, y + 57 + j * 19));
    ctx.fillStyle = "#a99fc8"; ctx.font = "11px Arial"; ctx.fillText(String(node.category || node.type || "Discovery"), x + 13, y + boxH - 15);
  });
  ctx.fillStyle = "#8f85b2"; ctx.font = "12px Arial"; ctx.fillText(`${visible.length - 1} liked discoveries · Exported from Curio`, 43, height - 32);
  canvas.toBlob(blob => { if (blob) { downloadBlob("curio-journey.png", blob); toast("Journey PNG exported."); } else toast("Could not export PNG in this browser."); }, "image/png");
}
function renderJourney() {
  const nodes = state.journey || [];
  const root = nodes.find(node => !node.parent_id) || state.journeyRoot;
  if (!root) {
    $("journeyMap").innerHTML = `<div class="journey-empty"><span class="journey-empty-orbit">✦</span><strong>Your cultural trail starts here</strong><small>Like a discovery to begin your journey.</small></div>`;
    return;
  }
  const likedIds = new Set((state.collection.liked || []).map(item => String(item.entity_id)));
  const mainNodes = nodes.filter(node => likedIds.has(String(node.entity_id)) || node.feedback === "liked");
  const unique = [];
  const seen = new Set([String(root.entity_id)]);
  for (const node of mainNodes) {
    if (!seen.has(String(node.entity_id))) {
      seen.add(String(node.entity_id));
      unique.push(node);
    }
  }
  const allMain = [root, ...unique];
  const branchFor = parent => {
    const list = state.alternativesByParent[String(parent.entity_id)] || [];
    return list.filter(item => !likedIds.has(String(item.entity_id)) && String(item.entity_id) !== String(parent.entity_id));
  };
  $("journeyMap").innerHTML = `
    <div class="journey-map-intro">
      <span class="journey-map-orbit">✧</span>
      <div><strong>${esc(root.name)}</strong><small>YOUR CULTURAL JOURNEY · ${esc(root.category || root.type || "Discovery")}</small></div>
      <span class="journey-map-count">${unique.length} discoveries</span>
    </div>
    <div class="journey-map-hint">YOUR LIKED PATH <span>✦</span></div>
    <div class="culture-map-scroll"><div class="culture-map-path">
      ${allMain.map((node, i) => {
        const isRoot = i === 0;
        const active = String(state.activeJourneyNode || (unique.length ? unique[unique.length - 1].entity_id : root.entity_id)) === String(node.entity_id);
        const past = !active && i < allMain.length - 1;
        const branches = branchFor(node);
        return `<div class="culture-map-column">
          <div class="culture-map-main-row">
            ${i ? `<span class="culture-map-link" aria-hidden="true"></span>` : ""}
            <button type="button" class="culture-map-node ${active ? "is-active" : ""} ${past ? "is-past" : ""} ${isRoot ? "is-root" : ""}" data-journey-node="${esc(node.entity_id)}" aria-label="Explore ${esc(node.name)}">
              ${node.image && /^https?:\/\//i.test(node.image) ? `<img class="journey-node-art" src="${esc(node.image)}" alt="" loading="lazy" onerror="this.style.display='none'">` : `<span class="journey-node-art journey-node-placeholder">✧</span>`}
              <span class="journey-node-copy"><span class="journey-node-kind">${esc(node.category || node.type || "Discovery")}</span><strong>${esc(node.name)}</strong><small>${isRoot ? "Portal origin" : "Liked discovery"}</small></span>
            </button>
          </div>
          ${branches.length ? `<div class="culture-map-branch-list"><span class="culture-map-branch-title">Alternatives</span><span class="culture-map-tree-line"></span>${branches.map(item => `<button type="button" class="culture-map-alternative" data-branch-parent="${esc(node.entity_id)}" data-branch-id="${esc(item.entity_id)}" title="Unchosen recommendation">${esc(item.name)}</button>`).join("")}</div>` : ""}
        </div>`;
      }).join("")}
    </div></div>
    <div class="journey-finish-row"><button type="button" class="button button-primary" id="finishJourneyButton">${state.journeyFinished ? "View Journey Passport" : "Finish Journey"}</button>${state.journeyFinished ? `<button type="button" class="button button-quiet" id="resumeJourneyButton">Resume journey</button>` : ""}</div>
    <div id="journeyPassport" class="journey-passport ${state.journeyFinished ? "" : "hidden"}"></div>`;
  $("journeyMap").querySelectorAll("[data-journey-node]").forEach(button => button.addEventListener("click", () => openJourneyNode(button.dataset.journeyNode)));
  $("journeyMap").querySelectorAll("[data-branch-id]").forEach(button => button.addEventListener("click", () => openJourneyNode(button.dataset.branchId)));
  $("finishJourneyButton").addEventListener("click", () => showJourneyPassport(true));
  const resume = $("resumeJourneyButton");
  if (resume) resume.addEventListener("click", () => { state.journeyFinished = false; persistLocalJourneyState(); renderJourney(); });
  if (state.journeyFinished) showJourneyPassport(false);
}
function showJourneyPassport(finish = true) {
  if (finish) {
    state.journeyFinished = true;
    persistLocalJourneyState();
    renderJourney();
    return;
  }
  persistLocalJourneyState();
  const nodes = state.journey || [];
  const wholeRoot = nodes.find(node => !node.parent_id) || state.journeyRoot;
  if (!wholeRoot) return;
  const nodeIds = new Set(nodes.map(node => String(node.entity_id)));
  const liked = nodes.filter(node => node.feedback === "liked" && String(node.entity_id) !== String(wholeRoot.entity_id));
  const categoryCounts = new Map();
  for (const item of liked) {
    const category = String(item.category || item.type || "Other");
    categoryCounts.set(category, (categoryCounts.get(category) || 0) + 1);
  }
  const categories = new Set(nodes.map(node => String(node.category || node.type || "Other"))).size;
  const edges = nodes.filter(node => node.parent_id && nodeIds.has(String(node.parent_id))).length;
  const children = new Map();
  for (const node of nodes) {
    const parentId = String(node.parent_id || "");
    if (!parentId || !nodeIds.has(parentId)) continue;
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId).push(node);
  }
  const depths = new Map([[String(wholeRoot.entity_id), 0]]);
  const queue = [wholeRoot];
  while (queue.length) {
    const current = queue.shift();
    const currentDepth = depths.get(String(current.entity_id)) || 0;
    for (const child of children.get(String(current.entity_id)) || []) {
      const childId = String(child.entity_id);
      if (depths.has(childId)) continue;
      depths.set(childId, currentDepth + 1);
      queue.push(child);
    }
  }
  const depth = Math.max(0, ...depths.values());
  const panel = $("journeyPassport");
  if (!panel) return;
  const categoryStats = [...categoryCounts.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([category, count]) => `<div class="passport-stat passport-category-stat"><span aria-hidden="true">❤</span><strong>${count}</strong><small>${esc(category)} liked</small></div>`).join("");
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="passport-heading"><span class="passport-orbit">✧</span><div><small>YOUR CULTURAL PASSPORT</small><h3>${esc(wholeRoot.name || "Curiosity, explored.")}</h3><p>${state.journeyFinished ? "Journey completed. Your discoveries are still here whenever you want to return." : "Your passport for this portal, including every discovery across its branches."}</p></div></div>
    <div class="passport-stats">
      <div class="passport-stat"><span>❤</span><strong>${liked.length}</strong><small>Discoveries liked</small></div>
      <div class="passport-stat"><span>▱</span><strong>${categories}</strong><small>Categories explored</small></div>
      <div class="passport-stat"><span>⌘</span><strong>${edges}</strong><small>Connections made</small></div>
      <div class="passport-stat"><span>▼</span><strong>${depth}</strong><small>Journey depth</small></div>
      ${categoryStats}
    </div>
    <div class="passport-footer"><span>${state.journeyFinished ? "PASSPORT COMPLETE" : "JOURNEY IN PROGRESS"}</span></div><button type="button" class="button button-quiet passport-reset" id="resetJourneyButton">Reset journey</button>`;
  $("resetJourneyButton").addEventListener("click", resetJourney);
}
async function openJourneyNode(entityId) {
  const node = (state.journey || []).find(item => String(item.entity_id) === String(entityId));
  if (!node || !state.journeyRoot) return;
  if (String(node.entity_id) === String(state.journeyRoot.entity_id)) {
    state.activeJourneyNode = String(node.entity_id);
    renderJourney();
    state.recommendations = [];
    await fetchRecommendations();
    return;
  }
  try {
    await api("/api/recommendations/actions", {method:"POST", body:JSON.stringify({entity_id:node.entity_id, action:"explore", journey_id:holeId(state.journeyRoot), parent_id:node.parent_id || state.journeyRoot.entity_id, entity:node, seed_entity:state.journeyRoot})});
    state.activeJourneyNode = String(node.entity_id);
    state.actionHistory = new Set();
    state.recommendations = [];
    $("activeHoleSubtitle").textContent = `Exploring from ${node.name} · ${node.category || node.type || "Discovery"}`;
    renderJourney();
    await fetchRecommendations();
    toast(`Portal opened: ${node.name}`);
    $("recommendationGrid").scrollIntoView({behavior:"smooth", block:"start"});
  } catch (error) { toast(error.message); }
}
async function loadCollection() {
  try {
    state.collection = await api("/api/collection");
    $("collectionCount").textContent = (state.collection.liked_count || 0);
    $("likedCount").textContent = (state.collection.liked || []).length;
    persistLocalJourneyState();
  } catch {}
}
async function resetJourney() {
  if (!window.confirm("Reset this journey? Your likes, skips, map, and exclusions will be cleared. Your onboarding favourites will stay.")) return;
  try {
    await api("/api/session/reset", {method:"POST", body:JSON.stringify({})});
    state.excludedIds.clear();
    state.actionHistory.clear();
    state.alternativesByParent = {};
    state.journeyFinished = false;
    state.activeJourneyNode = null;
    state.journey = [];
    state.collection = {liked: [], saved: [], skipped_ids: []};
    state.itemActions = {};
    state.recommendations = [];
    persistLocalJourneyState();
    await loadJourney();
    await loadCollection();
    renderRecommendations();
    renderJourney();
    toast("Journey reset. Your favourites are unchanged.");
  } catch (error) {
    toast(error.message);
  }
}
function renderCollection() {
  const items = state.collection.liked || [];
  const compatible = items.filter(item => ["Artists", "TV Shows", "Movies"].includes(item.category));
  const selectedCount = state.collectionSelection.size;
  $("collectionGrid").innerHTML = items.length ? items.map(item => {
    const id = String(item.entity_id);
    const selected = state.collectionSelection.has(id);
    return `<article class="collection-card ${selected ? "is-selected" : ""}">
      <label class="collection-select-control"><input type="checkbox" data-collection-select="${esc(id)}" ${selected ? "checked" : ""} aria-label="Select ${esc(item.name)} for a Memory Lane session"><span>Select</span></label>
      ${imgMarkup(item)}<div class="item-info"><strong>${esc(item.name)}</strong><small>${esc(item.category || "")}</small></div>
    </article>`;
  }).join("") : `<div class="journey-empty">Nothing here yet. Like a discovery that catches your eye.</div>`;
  const status = $("collectionTransferStatus");
  if (status) status.textContent = `${selectedCount} selected · ${compatible.length} compatible with Memory Lane`;
  const transfer = $("planSessionFromCollection");
  if (transfer) transfer.disabled = selectedCount === 0;
  const selectAll = $("selectCompatibleCollection");
  if (selectAll) selectAll.disabled = compatible.length === 0;
  const notice = $("collectionCompatibilityNote");
  if (notice) {
    const unsupported = [...new Set(items.map(item => item.category).filter(category => category && !["Artists", "TV Shows", "Movies"].includes(category)))];
    notice.textContent = unsupported.length
      ? `Memory Lane currently accepts Artists, TV Shows and Movies. ${unsupported.join(", ")} ${unsupported.length === 1 ? "stays" : "stay"} in your Curio collection.`
      : "Choose discoveries to use as starting favourites for a Memory Lane session. Supported categories: Artists, TV Shows and Movies.";
  }
}
async function showCollection() {
  await loadCollection();
  state.collectionSelection.clear();
  $("collectionGrid").innerHTML = "";
  showScreen("collectionScreen");
  renderCollection();
}
function transferSelectedCollectionToMemory() {
  const selected = (state.collection.liked || []).filter(item => state.collectionSelection.has(String(item.entity_id)));
  if (!selected.length) return toast("Select at least one discovery first.");
  if (!window.CurioMemory || typeof window.CurioMemory.addSeedsFromCollection !== "function") {
    return toast("Memory Lane is unavailable right now. Please reload Curio.");
  }
  const result = window.CurioMemory.addSeedsFromCollection(selected);
  if (!result.added) {
    const reasons = [];
    if (result.unsupported.length) reasons.push(`${result.unsupported.length} unsupported`);
    if (result.duplicates) reasons.push(`${result.duplicates} already added`);
    if (result.full) reasons.push("the six-favourite limit is reached");
    return toast(reasons.length ? `Nothing added: ${reasons.join(", ")}.` : "No compatible items could be added.");
  }
  window.CurioMemory.open();
  window.CurioMemory.showCollectionImportNotice(result);
  state.collectionSelection.clear();
  renderCollection();
  const parts = [`Added ${result.added} ${result.added === 1 ? "favourite" : "favourites"}`];
  if (result.unsupported.length) parts.push(`${result.unsupported.length} unsupported`);
  if (result.duplicates) parts.push(`${result.duplicates} already present`);
  if (result.full) parts.push(`${result.full} over the six-item limit`);
  toast(`${parts.join(" · ")}. Review them in Memory Lane.`);
}
async function getActiveSource() {
  const parentId = await getActiveParentId();
  return state.journey.find(item => item.entity_id === parentId) || state.journeyRoot;
}

function startLiveStatus(container, stages, intervalMs = 1400) {
  let index = 0;
  const render = () => {
    const target = container.querySelector("[data-live-status]");
    if (!target) return;
    const stage = stages[Math.min(index, stages.length - 1)];
    target.innerHTML = `<strong>${esc(stage.title)}</strong><p>${esc(stage.detail)}</p>`;
    index = Math.min(index + 1, stages.length - 1);
  };
  render();
  const timer = setInterval(render, intervalMs);
  return () => clearInterval(timer);
}
async function showTasteProfile(refresh = false) {
  const output = $("aiOutput"); output.classList.remove("hidden"); output.innerHTML = `<div class="ai-loading" role="status" aria-live="polite"><div class="taste-orbit"><span></span><span></span><span></span></div><div data-live-status></div></div>`;
  renderLiveProgress(output, {title:"Checking your cultural signals", detail:"Loading onboarding favourites and the selected journey's liked discoveries."});
  try {
    const data = await apiStream("/api/gemini/taste-profile/stream", {method:"POST", body:JSON.stringify({journey_id:state.journeyRoot ? holeId(state.journeyRoot) : null, refresh: Boolean(refresh)})}, progress => renderLiveProgress(output, progress));
  const profile = data.profile || {};
    const signals = profile.signals || []; const connections = profile.connections || [];
    const liveTrace = traceMarkup(data.live_trace, {summary:"How the agent got here", open:true});
    output.innerHTML = `<div class="ai-output-head"><span class="ai-kicker">✦ YOUR PERSONAL TASTE MAP</span><h3>${esc(profile.title || "The shape of your taste")}</h3><p>${esc(profile.summary || "Your cultural choices form a map of curiosity, mood and recurring themes.")}</p></div><div class="taste-signal-grid">${signals.map((signal,i) => `<div class="taste-signal" style="--delay:${i*90}ms"><span class="signal-index">0${i+1}</span><strong>${esc(signal)}</strong><span class="signal-glow"></span></div>`).join("")}</div>${connections.length ? `<div class="connection-map"><div class="connection-map-title">THE THREADS THAT CONNECT YOUR TASTE <span>✦</span></div>${connections.map((c,i) => `<div class="connection-row" style="--delay:${i*100}ms"><div class="connection-endpoint">${esc(c.from)}</div><div class="connection-thread"><span>✧</span><i>connects</i></div><div class="connection-endpoint">${esc(c.to)}</div><p>${esc(c.reason)}</p></div>`).join("")}</div>` : ""}<div class="interpretation-note">${data.source === "gemini" ? `Gemini interpretation of your Qloo tags${data.cached ? " · saved from earlier, since your taste hasn't changed" : ""} · Reflective prompts, not a verified psychological assessment.` : "Built directly from the tags Qloo attaches to your picks. Gemini wasn't available this time."} <button class="mini-button profile-refresh" data-refresh-profile>↻ ${data.source === "gemini" ? "Regenerate" : "Try Gemini again"}</button></div>${liveTrace}`;
    output.querySelector("[data-refresh-profile]").addEventListener("click", () => showTasteProfile(true));
  } catch (error) { output.innerHTML = `<div class="ai-error" role="alert">${esc(typeof error.message === "string" ? error.message : JSON.stringify(error.message))}</div>`; }
}
function renderDeeperPathResult(data, returnToMemory = false) {
  const output = $("aiOutput");
  const steps = data.steps || [];
  const gemini = data.mode === "gemini";
  const modeText = gemini ? "Qloo built the path · Gemini wrote the bridges" : "Built hop by hop from Qloo's taste graph";
  const trace = traceMarkup(data.trace);
  const liveTrace = traceMarkup(data.live_trace, {summary:"Live execution details"});
  const returnBar = returnToMemory
    ? `<div class="memory-return-bar"><button type="button" id="returnToMemoryFromDeeper" class="button button-quiet">← Return to Memory Lane</button><span>Session preserved</span></div>`
    : "";
  output.innerHTML = `${returnBar}<div class="ai-output-head"><span class="ai-kicker">${returnToMemory ? "A MEMORY LANE DISCOVERY · BEYOND THE MOMENT" : "BEYOND YOUR USUAL ORBIT"}</span><h3>${esc(data.title || "A path beyond your usual orbit")}</h3><p><span class="mode-badge ${gemini ? "gemini" : ""}">${esc(modeText)}</span></p>${data.note ? `<p class="path-note">${esc(data.note)}</p>` : ""}</div>${steps.length ? `<ol class="path-list">${steps.map(pathStepMarkup).join("")}</ol>` : `<p class="journey-empty">Qloo had no new discoveries from this item. Try a different one.</p>`}${trace}${liveTrace}<div class="interpretation-note"></div>`;
  output.querySelectorAll("[data-path-like]").forEach(button => button.addEventListener("click", () => likePathItem(button, steps)));
  if (returnToMemory) $("returnToMemoryFromDeeper").addEventListener("click", () => showScreen("memoryScreen"));
}

async function startDeeperFromMemory(seed) {
  if (!seed || !seed.entity_id || !seed.category) throw new Error("This discovery is missing its Qloo entity details.");
  showScreen("discoveryScreen");
  const output = $("aiOutput"); output.classList.remove("hidden");
  output.innerHTML = `<div class="ai-loading" role="status" aria-live="polite"><div class="deep-radar"><span></span><span></span><span></span></div><div data-live-status></div></div>`;
  renderLiveProgress(output, {title:`Following a connection from ${seed.name}`, detail:"Verifying the item you loved, then exploring its cultural connections."});
  try {
    const data = await apiStream("/api/deeper/path/stream", {
      method:"POST",
      body:JSON.stringify({seed_entity_id:seed.entity_id, seed_category:seed.category, source:"memory", hops:5, popularity:"any"})
    }, progress => renderLiveProgress(output, progress));
    renderDeeperPathResult(data, true);
  } catch (error) {
    output.innerHTML = `<div class="memory-return-bar"><button type="button" id="returnToMemoryFromDeeper" class="button button-quiet">← Return to Memory Lane</button><span>Session preserved</span></div><div class="ai-error" role="alert">${esc(errorText(error))}</div>`;
    $("returnToMemoryFromDeeper").addEventListener("click", () => showScreen("memoryScreen"));
  }
}
window.CurioStartDeeperFromMemory = startDeeperFromMemory;

async function showDeeperPath() {
  const output = $("aiOutput"); output.classList.remove("hidden");
  const seed = (await getActiveSource()) || currentFavourites()[0];
  if (!seed) { output.innerHTML = `<div class="ai-error" role="alert">Pick a favourite first so there is somewhere to start.</div>`; return; }
  output.innerHTML = `<div class="ai-loading" role="status" aria-live="polite"><div class="deep-radar"><span></span><span></span><span></span></div><div data-live-status></div></div>`;
  renderLiveProgress(output, {title:`Starting from ${seed.name}`, detail:"Using the selected discovery as the exploration seed."});
  $("deeperButton").disabled = true;
  try {
    const data = await apiStream("/api/deeper/path/stream", {method:"POST", body:JSON.stringify({seed_entity_id:seed.entity_id, seed_category:seed.category, journey_id:state.journeyRoot ? holeId(state.journeyRoot) : null, hops:5, popularity:state.popularity})}, progress => renderLiveProgress(output, progress));
    renderDeeperPathResult(data, false);
  } catch (error) { output.innerHTML = `<div class="ai-error" role="alert">${esc(errorText(error))}</div>`; }
  finally { $("deeperButton").disabled = false; }
}

function evidenceMarkup(evidence) {
  return window.CurioDiscoveryUI
    ? window.CurioDiscoveryUI.evidenceMarkup(evidence)
    : "";
}
function whyMarkup(why, evidence, className = "discovery-why") {
  return window.CurioDiscoveryUI
    ? window.CurioDiscoveryUI.whyMarkup(why, evidence, className)
    : "";
}
function traceMarkup(steps, options = {}) {
  return window.CurioDiscoveryUI
    ? window.CurioDiscoveryUI.traceMarkup(steps, options)
    : "";
}
function errorText(error) { return typeof error.message === "string" ? error.message : JSON.stringify(error.message); }

function clipText(text, max = 300) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (value.length <= max) return value;
  const cut = value.slice(0, max); const stop = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("; "));
  return (stop > max * 0.5 ? cut.slice(0, stop + 1) : cut.replace(/\s+\S*$/, "") + "…");
}
function pathStepMarkup(step) {
  const item = step.entity; const liked = state.journeyRoot ? (state.journey || []).some(node => String(node.entity_id) === String(item.entity_id) && node.feedback === "liked") : (state.collection.liked || []).some(e => String(e.entity_id) === String(item.entity_id));
  const img = item.image && /^https?:\/\//i.test(item.image) ? `<img src="${esc(item.image)}" alt="" loading="lazy" onerror="this.remove()">` : "◉";
  const description = clipText(item.description || item.short_description || "");
  const connection = step.note || step.why || "";
  return `<li class="path-step"><div class="path-order" aria-hidden="true">${step.order}</div><div class="path-art">${img}</div><div class="path-body"><div class="rec-meta">${esc(item.category)}${step.from ? ` <span class="path-from">· from ${esc(step.from)}</span>` : ""}</div><h4>${esc(item.name)}</h4>${description ? `<p class="path-desc">${esc(description)}</p>` : ""}${whyMarkup(connection, step.evidence, "path-link")}<button class="mini-button like ${liked ? "is-done" : ""}" data-path-like="${esc(item.entity_id)}" data-feedback="love" aria-pressed="${liked}"><span aria-hidden="true">❤</span> ${liked ? "Liked" : "Like"}</button></div></li>`;
}
async function showVibeCheck(prompt) {
  const value = String(prompt || "").trim();
  const output = $("homeVibeOutput");
  if (!value) {
    output.classList.remove("hidden");
    output.innerHTML = `<div class="ai-error" role="alert">Describe a mood, theme or kind of discovery first.</div>`;
    return;
  }
  output.classList.remove("hidden");
  output.innerHTML = `<div class="ai-loading" role="status" aria-live="polite"><div class="deep-radar"><span></span><span></span><span></span></div><div data-live-status></div></div>`;
  renderLiveProgress(output, {title:"Understanding your vibe", detail:"Gemini is translating your description into a discovery direction; Qloo will ground the results."});
  const button = $("homeVibeButton");
  if (button) button.disabled = true;
  try {
    const data = await apiStream("/api/vibe-check/stream", {
      method:"POST", body:JSON.stringify({prompt:value})
    }, progress => renderLiveProgress(output, progress));
    const steps = data.steps || [];
    const gemini = data.mode === "gemini-agent";
    const intent = data.intent || {};
    const chips = [...(intent.mood || []), ...(intent.themes || [])].slice(0, 8);
    const intentMarkup = `<div class="vibe-intent-summary"><span class="step-label">YOUR VIBE</span><p>${esc(intent.original_prompt || value)}</p>${chips.length ? `<div class="vibe-intent-chips">${chips.map(x => `<span>${esc(x)}</span>`).join("")}</div>` : ""}<small>${esc((intent.categories || []).join(" · "))}</small></div>`;
    const modeText = gemini ? "Qloo found the candidates · Gemini ordered and explained them" : "Qloo-grounded discovery · deterministic planning";
    output.innerHTML = `${intentMarkup}<div class="ai-output-head"><span class="ai-kicker">YOUR DISCOVERY</span><h3>${esc(data.theme || "A path shaped by your vibe")}</h3><p><span class="mode-badge ${gemini ? "gemini" : ""}">${esc(modeText)}</span></p>${data.goal_status ? `<p class="path-note">${esc(data.goal_status)}</p>` : ""}${data.note ? `<p class="path-note">${esc(data.note)}</p>` : ""}</div>${steps.length ? `<ol class="path-list">${steps.map(pathStepMarkup).join("")}</ol>` : `<p class="journey-empty">Qloo didn't find enough grounded discoveries for this vibe. Try adjusting the description.</p>`}${traceMarkup(data.trace)}${data.category_note ? `<div class="interpretation-note">${esc(data.category_note)}</div>` : ""}<div class="interpretation-note"></div>`;
    output.querySelectorAll("[data-path-like]").forEach(button => button.addEventListener("click", () => likePathItem(button, steps)));
    output.scrollIntoView({behavior:"smooth", block:"start"});
  } catch (error) {
    output.innerHTML = `<div class="ai-error" role="alert">${esc(errorText(error))}</div>`;
  } finally {
    if (button) button.disabled = false;
  }
}

async function showAgentPath() {
  const goal = ($("agentGoal").value || "").trim();
  const output = $("aiOutput"); output.classList.remove("hidden");
  output.innerHTML = `<div class="ai-loading" role="status" aria-live="polite"><div class="deep-radar"><span></span><span></span><span></span></div><div data-live-status></div></div>`;
  renderLiveProgress(output, {title:"Reading your discovery goal", detail:"Preparing your preferences and constraints for this plan."});
  $("planButton").disabled = true;
  try {
    const data = await apiStream("/api/agent/path/stream", {method:"POST", body:JSON.stringify({goal: goal || null})}, progress => renderLiveProgress(output, progress));
    const steps = data.steps || [];
    const gemini = data.mode === "gemini-agent";
    const modeText = gemini ? "Qloo found the candidates · Gemini ordered and explained them" : "Ordered directly from Qloo scores";
    const trace = traceMarkup(data.trace);
    const liveTrace = traceMarkup(data.live_trace, {summary:"Live execution details"});
    output.innerHTML = `<div class="ai-output-head"><span class="ai-kicker">YOUR NEXT FIVE</span><h3>${esc(data.theme || "A path beyond your usual orbit")}</h3><p><span class="mode-badge ${gemini ? "gemini" : ""}">${esc(modeText)}</span></p>${data.goal_status ? `<p class="path-note">${esc(data.goal_status)}</p>` : ""}${data.note ? `<p class="path-note">${esc(data.note)}</p>` : ""}</div><ol class="path-list">${steps.map(pathStepMarkup).join("")}</ol>${trace}${liveTrace}<div class="interpretation-note"></div>`;
    output.querySelectorAll("[data-path-like]").forEach(button => button.addEventListener("click", () => likePathItem(button, steps)));
  } catch (error) { output.innerHTML = `<div class="ai-error" role="alert">${esc(errorText(error))}</div>`; }
  finally { $("planButton").disabled = false; }
}
async function likePathItem(button, steps) {
  const step = steps.find(s => String(s.entity.entity_id) === button.dataset.pathLike); if (!step) return;
  const entityId = String(step.entity.entity_id);
  const wasLiked = state.journeyRoot
    ? (state.journey || []).some(node => String(node.entity_id) === entityId && node.feedback === "liked")
    : (state.collection.liked || []).some(item => String(item.entity_id) === entityId);
  const journeyId = state.journeyRoot ? holeId(state.journeyRoot) : "agent-path";
  const parentId = state.journeyRoot ? await getActiveParentId() : null;
  const payload = {entity_id:entityId, action:wasLiked ? "unlike" : "like", journey_id:journeyId, parent_id:parentId, entity:step.entity};
  if (!wasLiked && state.journeyRoot) payload.seed_entity = state.journeyRoot;
  try {
    await api("/api/recommendations/actions", {method:"POST", body:JSON.stringify(payload)});
    await loadCollection();
    if (state.journeyRoot) await loadJourney();
    persistLocalJourneyState();
    button.classList.toggle("is-done", !wasLiked); button.setAttribute("aria-pressed", String(!wasLiked));
    button.innerHTML = `<span aria-hidden="true">❤</span> ${wasLiked ? "Like" : "Liked"}`;
    toast(wasLiked ? `Unliked ${step.entity.name}` : `Liked ${step.entity.name}`);
  } catch (error) { toast(errorText(error)); }
}

async function checkHealth() {
  try {
    await fetch(`${state.apiBase}/health`);
    $("connectionStatus").classList.add("online");
    $("connectionStatus").innerHTML = "<i></i> API connected";
  } catch {
    $("connectionStatus").classList.remove("online");
    $("connectionStatus").innerHTML = "<i></i> API unavailable";
  }
}
function bindEvents() {
  document.querySelectorAll("[data-fill-target][data-fill-text]").forEach(chip => chip.addEventListener("click", () => {
    const target = $(chip.dataset.fillTarget);
    if (!target) return;
    target.value = chip.dataset.fillText || "";
    target.focus();
    target.dispatchEvent(new Event("input", {bubbles:true}));
  }));
  $("searchButton").addEventListener("click", searchEntities);
  $("searchInput").addEventListener("keydown", event => { if (event.key === "Enter") searchEntities(); });
  $("clearCategoryButton").addEventListener("click", async () => {
    const items = [...(state.favourites[state.activeCategory] || [])];
    for (const item of items) await removeFavourite(item.entity_id);
  });
  $("startButton").addEventListener("click", enterDiscovery);
  $("editPicksButton").addEventListener("click", () => showScreen("setupScreen"));
  $("closeHoleButton").addEventListener("click", () => { $("explorationPanel").classList.add("hidden"); window.scrollTo({top:0,behavior:"smooth"}); });
  $("findMoreButton").addEventListener("click", fetchRecommendations);
  $("recommendationCategory").addEventListener("change", async event => { state.recommendationCategory = event.target.value; state.recommendations = []; await fetchRecommendations(); });
  $("refreshJourneyButton").addEventListener("click", loadJourney);
  $("downloadGraphPngButton").addEventListener("click", exportJourneyPng);
  $("downloadGraphJsonButton").addEventListener("click", exportJourneyJson);
  $("collectionButton").addEventListener("click", showCollection);
  $("backFromCollection").addEventListener("click", () => showScreen(collectionReturnScreen || "homeScreen"));
  $("collectionGrid").addEventListener("change", event => {
    const checkbox = event.target.closest("[data-collection-select]");
    if (!checkbox) return;
    const id = String(checkbox.dataset.collectionSelect);
    if (checkbox.checked) state.collectionSelection.add(id);
    else state.collectionSelection.delete(id);
    renderCollection();
  });
  $("selectCompatibleCollection").addEventListener("click", () => {
    (state.collection.liked || []).filter(item => ["Artists", "TV Shows", "Movies"].includes(item.category))
      .forEach(item => state.collectionSelection.add(String(item.entity_id)));
    renderCollection();
  });
  $("clearCollectionSelection").addEventListener("click", () => { state.collectionSelection.clear(); renderCollection(); });
  $("planSessionFromCollection").addEventListener("click", transferSelectedCollectionToMemory);
  document.querySelectorAll("[data-collection]").forEach(button => button.addEventListener("click", () => {
    state.collectionType = button.dataset.collection;
    document.querySelectorAll("[data-collection]").forEach(b => b.classList.toggle("active", b === button));
    renderCollection();
  }));
  $("tasteProfileButton").addEventListener("click", () => showTasteProfile(false));
  $("deeperButton").addEventListener("click", showDeeperPath);
  $("planButton").addEventListener("click", showAgentPath);
  $("agentGoal").addEventListener("keydown", event => { if (event.key === "Enter") showAgentPath(); });
  const refreshRecs = () => { if (state.journeyRoot) { state.recommendations = []; fetchRecommendations(); } };
  $("popularitySelect").addEventListener("change", event => { state.popularity = event.target.value; refreshRecs(); });
  $("homePersonalDoor").addEventListener("click", () => {
    if (currentFavourites().length) {
      showScreen("discoveryScreen");
      renderHoleCards();
    } else {
      showScreen("setupScreen");
    }
  });
  $("homeVibeButton").addEventListener("click", () => showVibeCheck($("homeVibePrompt").value));
  $("homeVibePrompt").addEventListener("keydown", event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) showVibeCheck($("homeVibePrompt").value); });
  $("homeNavButton").addEventListener("click", () => showScreen("homeScreen"));
  $("brandHome").addEventListener("click", event => { event.preventDefault(); showScreen("homeScreen"); });
}
async function init() {
  bindEvents();
  renderTabs();
  renderSelected();
  renderCategoryOptions();
  await checkHealth();
  await loadFavourites();
  await loadCollection();
  if (state.journeyRoot) {
    await loadJourney();
    renderRecommendations();
    renderJourney();
  }
  persistLocalJourneyState();
  setInterval(checkHealth, 12000);
}
init();
