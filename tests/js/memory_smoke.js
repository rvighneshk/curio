
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const els = {};
const el = id => els[id] || (els[id] = {id, innerHTML: "", value: "", textContent: "", disabled: false, dataset: {}, classList: {add() {}, remove() {}, toggle() {}},
  addEventListener() {}, querySelectorAll: () => [], querySelector: () => null, scrollIntoView() {}, closest: () => null});
const store = {};
const ctx = {
  console, window: {}, document: {getElementById: el, createElement: () => el("tmp")},
  localStorage: {getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }},
  $: el, esc: v => String(v ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c])),
  clipText: (t, m) => String(t || "").slice(0, m), evidenceMarkup: () => "<div class=\"evidence-row\"></div>",
  api: async () => ({}), apiStream: async () => ({}), renderLiveProgress() {}, toast() {}, showScreen() {}, setMessage() {},
  errorText: e => e.message, currentFavourites: () => [],
};
ctx.window = ctx; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../../static/discovery-ui.js"), "utf8"), ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../../static/memory.js"), "utf8"), ctx);
const {renderSession} = ctx.CurioMemory;

const item = (id, name, category, extra = {}) => ({
  handle: id, why: "Shares Comedy with I Love Lucy (Qloo match 90%).", prompt: "What did you enjoy about it?", evidence: {shared_tags:["Comedy"], affinity:0.9},
  entity: {entity_id: id, name, category, year: 1955, description: "A classic.", tags: ["Comedy"], image: "https://images.qloo.com/x.jpg", ...extra},
});
const data = {
  title: "Sunday evenings", intro: "Gentle start.", mode: "gemini-agent", note: null,
  era: {from: 1952, to: 1972, birth_year: 1942, region: "United States"},
  music: [item("A1", "Louis Armstrong", "Artists", {links: {open: "https://open.spotify.com/artist/abc", embed: "https://open.spotify.com/embed/artist/abc"}, facts: {notable_songs: ["What a Wonderful World"]}}),
          item("A2", "<img src=x onerror=alert(1)>", "Artists", {links: {open: "javascript:alert(1)"}})],
  screen: [item("T1", "The Honeymooners", "TV Shows", {provider_links: [{label: "IMDb", url: "https://www.imdb.com/title/tt1/"}, {label: "Bad", url: "javascript:alert(1)"}]})],
  revisit: [item("T9", "Hazel", "TV Shows")], starters: ["Shall we put on it?"], tips: ["Go slowly."],
  adaptations: ["You loved Hazel, so it's now a starting point."], trace: [{tool: "explore", summary: "TV Shows from I Love Lucy → 4 usable"}],
  stats: {qloo_calls: 3, entities_considered: 14, grounded: true},
};
renderSession(data);
const html = els.memoryOutput.innerHTML;
assert(html.includes("Sunday evenings") && html.includes("Listen here") && html.includes("Adapted to reactions"));
assert(html.includes("Revisit") && html.includes("Conversation starters") && html.includes("How the agent got here (1 steps"));
assert(!html.includes("Take me deeper") && !html.includes("Compare with Gemini"), "Memory Lane should not expose personal discovery tools");
assert(!html.includes("VIBE CHECK"), "Memory Lane should not expose Vibe Check");
assert(!html.includes("<img src=x onerror"), "raw HTML from entity names must be escaped");
assert(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
assert(!html.includes("javascript:"), "non-https links must be dropped");
assert(html.includes("https://www.imdb.com/title/tt1/"));
assert(html.includes('aria-pressed="false"'));
assert(html.includes("Lit up") && html.includes("Not for them"));
assert(html.includes("Why it's here") && html.includes("Evidence"));
assert(html.includes("Adapt to reactions"));
const transfer = ctx.CurioMemory.addSeedsFromCollection([
  {entity_id:"C1",name:"Sudirman",category:"Artists",image:"https://images.example/a.jpg"},
  {entity_id:"C2",name:"The Prisoner",category:"TV Shows"},
  {entity_id:"C3",name:"The Sound of Music",category:"Movies"},
  {entity_id:"C4",name:"A novel",category:"Books"},
  {entity_id:"C1",name:"Sudirman",category:"Artists"},
  {entity_id:"C5",name:"Artist Five",category:"Artists"},
  {entity_id:"C6",name:"Artist Six",category:"Artists"},
  {entity_id:"C7",name:"Artist Seven",category:"Artists"},
  {entity_id:"C8",name:"Artist Eight",category:"Artists"},
]);
assert.strictEqual(transfer.added, 6, "only six compatible unique items should transfer");
assert.deepStrictEqual(Array.from(transfer.unsupported), ["Books"]);
assert.strictEqual(transfer.duplicates, 1);
assert.strictEqual(transfer.full, 1);
assert.strictEqual(ctx.CurioMemory.getSeeds().length, 6);
ctx.CurioMemory.showCollectionImportNotice(transfer);
assert(els.memoryImportNotice.textContent.includes("Books"));
const shared = ctx.CurioDiscoveryUI;
assert(shared.whyMarkup("A <safe> connection", {shared_tags:["Dream pop"]}).includes("A &lt;safe&gt; connection"));
assert(shared.traceMarkup([{tool:"qloo.search",summary:"Found 4 candidates"}]).includes("How the agent got here"));
assert(shared.adaptationMarkup(["A loved item became a new seed."]).includes("Adapted to reactions"));
assert(shared.feedbackGuidanceMarkup("The next path follows your choices.").includes("Adapt to reactions"));
assert.strictEqual(shared.mapCurioAction("like"), "love");
assert.strictEqual(shared.mapCurioAction("skip"), "upset");
console.log("memory.js smoke test passed");
