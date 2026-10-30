
(() => {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const REACTIONS = Object.freeze([
    Object.freeze({key:"love", icon:"😊", label:"Lit up"}),
    Object.freeze({key:"ok", icon:"😐", label:"Okay"}),
    Object.freeze({key:"upset", icon:"😟", label:"Not for them"})
  ]);

  function evidenceMarkup(evidence) {
    if (!evidence) return "";
    const tags = (Array.isArray(evidence.shared_tags) ? evidence.shared_tags : []).slice(0, 4);
    const drivers = (Array.isArray(evidence.drivers) ? evidence.drivers : []).map(d => d && d.name).filter(Boolean).slice(0, 2);
    const affinity = typeof evidence.affinity === "number"
      ? (evidence.affinity <= 1 ? `${Math.round(evidence.affinity * 100)}%` : evidence.affinity.toFixed(2))
      : "";
    if (!tags.length && !drivers.length && !affinity) return "";
    const viaText = evidence.drivers_source === "qloo" ? "via" : "similar to";
    const viaTitle = evidence.drivers_source === "qloo"
      ? "Qloo's own explainability named these seeds"
      : "Inferred from tags Qloo returned for both items";
    return `<div class="evidence-row" aria-label="Evidence"><span class="evidence-label">Evidence</span>${affinity ? `<span class="evidence-chip affinity" title="Qloo affinity score">match ${esc(affinity)}</span>` : ""}${tags.map(tag => `<span class="evidence-chip">${esc(tag)}</span>`).join("")}${drivers.length ? `<span class="evidence-driver" title="${esc(viaTitle)}">${viaText} ${esc(drivers.join(", "))}</span>` : ""}</div>`;
  }

  function whyMarkup(why, evidence, className = "discovery-why") {
    const explanation = String(why || "").trim();
    const evidenceHtml = evidenceMarkup(evidence);
    if (!explanation && !evidenceHtml) return "";
    return `<div class="${esc(className)}"><span class="path-link-label">Why it's here</span>${explanation ? `<p>${esc(explanation)}</p>` : ""}${evidenceHtml}</div>`;
  }

  function traceMarkup(steps, options = {}) {
    const list = Array.isArray(steps) ? steps : [];
    if (!list.length) return "";
    const summary = options.summary || "How the agent got here";
    const suffix = options.suffix || "";
    const rows = list.map(step => {
      const label = step && (step.tool || step.title) || "Step";
      const detail = step && (step.summary || step.detail) || "";
      return `<li><code>${esc(label)}</code> ${esc(detail)}</li>`;
    }).join("");
    return `<details class="agent-trace discovery-trace"${options.open ? " open" : ""}><summary>${esc(summary)} (${list.length} steps${suffix ? ` · ${esc(suffix)}` : ""})</summary><ol>${rows}</ol></details>`;
  }

  function feedbackGuidanceMarkup(description) {
    const copy = String(description || "").trim();
    if (!copy) return "";
    return `<div class="discovery-feedback-guidance"><strong>Adapt to reactions</strong><p>${esc(copy)}</p></div>`;
  }

  function adaptationMarkup(adaptations) {
    const list = Array.isArray(adaptations) ? adaptations.filter(Boolean) : [];
    if (!list.length) return "";
    return `<section class="adapt-banner discovery-adaptation" aria-label="Adapted to reactions"><strong>Adapted to reactions</strong><ul>${list.map(item => `<li>${esc(item)}</li>`).join("")}</ul></section>`;
  }

  function mapCurioAction(action) {
    if (action === "like") return "love";
    if (action === "skip") return "upset";
    return null;
  }

  function reactionMarkup(id, current) {
    return `<div class="memory-reactions discovery-reactions" role="group" aria-label="How did they respond?">${REACTIONS.map(({key, icon, label}) => `<button type="button" class="reaction-btn ${current === key ? `active ${key}` : ""}" data-react="${key}" data-id="${esc(id)}" aria-pressed="${current === key}"><span aria-hidden="true">${icon}</span> ${label}</button>`).join("")}</div>`;
  }

  window.CurioDiscoveryUI = Object.freeze({
    evidenceMarkup,
    whyMarkup,
    traceMarkup,
    adaptationMarkup,
    feedbackGuidanceMarkup,
    reactionMarkup,
    mapCurioAction,
    reactions: REACTIONS
  });
})();
