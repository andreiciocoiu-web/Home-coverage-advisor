"use strict";
// Toate textele provenite din documente sau de la utilizator sunt escapate (esc) inainte de afisare.
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NS = "Nespecificat in documentele analizate";
const ST = { provided: "Furnizat", inferred: "Dedus", unknown: "Necunoscut" };
let TOKEN = "";
let STATUS = null, ME = null, EXTRACTION = null, ANALYSIS = null, radarChart = null;

try { TOKEN = sessionStorage.getItem("hca_token") || ""; } catch (e) { TOKEN = ""; }

function toast(msg, ms = 3500) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (TOKEN) headers.Authorization = "Bearer " + TOKEN;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) {}
    throw new Error(`${res.status}: ${detail}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

// ------------------------------------------------------------------ navigare
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
  if (name === "review") loadFeedback();
  if (name === "method") loadMethod();
}

// ------------------------------------------------------------------ autentificare
$("#loginBtn").addEventListener("click", async () => {
  TOKEN = $("#token").value.trim();
  try { sessionStorage.setItem("hca_token", TOKEN); } catch (e) {}
  await boot();
});
$("#token").value = TOKEN;

async function boot() {
  try {
    ME = await api("/api/me");
    $("#who").textContent = `${ME.username} (${ME.role})`;
    STATUS = await api("/api/status");
    renderStatus();
    await Promise.all([loadSources(), loadDocs()]);
  } catch (e) {
    $("#who").textContent = "Neautentificat";
    $("#statusBar").textContent = "Introduceti tokenul de acces. " + e.message;
  }
}

function renderStatus() {
  const s = STATUS;
  $("#statusBar").innerHTML = [
    `Documente active: <b>${s.documents}</b>`, `Produse: <b>${s.products}</b>`,
    `Analiza: <b>${s.llm_enabled ? "LLM (" + esc(s.llm_model) + ") + verificare citate" : "euristica (fara cheie LLM) - fiabilitate redusa"}</b>`,
    `Cautare: ${esc(s.retrieval_backend)}`, `Metodologie v${s.methodology_version}`, `Retentie analize: ${s.retention_days} zile`,
  ].join(" · ");
}

// ------------------------------------------------------------------ surse
$("#sourceForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/sources", { method: "POST", body: JSON.stringify({ kind: $("#srcKind").value, location: $("#srcLoc").value, label: $("#srcLabel").value }) });
    $("#srcLoc").value = ""; $("#srcLabel").value = "";
    await loadSources();
  } catch (e) { toast(e.message); }
});

async function loadSources() {
  const rows = await api("/api/sources");
  $("#sources").innerHTML = rows.length ? `<table><tr><th>Sursa</th><th>Tip</th><th>Ultima sincronizare</th><th>Stare</th><th></th></tr>` +
    rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${esc(r.kind)}</td><td>${esc(r.last_sync || "-")}</td><td>${esc(r.status || "-")}</td>
      <td><button class="btn sm" data-sync="${esc(r.id)}">Sincronizeaza</button> <button class="btn sm" data-runs="${esc(r.id)}">Istoric</button></td></tr>`).join("") + "</table>"
    : `<p class="muted">Nicio sursa configurata.</p>`;
  document.querySelectorAll("[data-sync]").forEach((b) => b.addEventListener("click", () => startSync(b.dataset.sync, b)));
  document.querySelectorAll("[data-runs]").forEach((b) => b.addEventListener("click", () => showRuns(b.dataset.runs)));
}

async function startSync(id, btn) {
  btn.disabled = true;
  try {
    await api(`/api/sources/${id}/sync`, { method: "POST" });
    $("#syncInfo").innerHTML = "Sincronizare in curs: listare, descarcare, parsare, OCR, extragere clauze, indexare...";
    const poll = async () => {
      const s = await api(`/api/sources/${id}/sync`);
      if (s.job && s.job.state === "running") return setTimeout(poll, 1500);
      btn.disabled = false;
      renderRuns(s);
      STATUS = await api("/api/status"); renderStatus();
      await Promise.all([loadSources(), loadDocs()]);
    };
    poll();
  } catch (e) { btn.disabled = false; toast(e.message); }
}

async function showRuns(id) { renderRuns(await api(`/api/sources/${id}/sync`)); }
function renderRuns(s) {
  let h = "";
  if (s.job && s.job.state === "error") h += `<div class="badbox">Eroare: ${esc(s.job.error)}</div>`;
  h += s.runs.map((r) => `<div class="ev"><div class="meta">${esc(r.started)} → ${esc(r.finished || "")} · ${esc(r.status)}</div>
    Noi: <b>${r.added}</b> · Modificate: <b>${r.modified}</b> · Sterse: <b>${r.deleted}</b> · Neschimbate: ${r.unchanged}
    ${r.errors.length ? `<div class="warnbox">Erori de procesare:<ul>${r.errors.map((e) => `<li>${esc(e.fisier)}: ${esc(e.eroare)}</li>`).join("")}</ul></div>` : ""}</div>`).join("");
  $("#syncInfo").innerHTML = h || "Nicio sincronizare efectuata.";
}

// ------------------------------------------------------------------ documente
$("#showInactive").addEventListener("change", loadDocs);
async function loadDocs() {
  const { documents, relations } = await api("/api/documents?include_inactive=" + ($("#showInactive").checked ? "true" : "false"));
  const byId = Object.fromEntries(documents.map((d) => [d.id, d]));
  const relLabel = { duplicate: "Duplicat exact", near_duplicate: "Aproape identic", version_conflict: "Versiuni diferite / conflictuale", amends: "Act aditional care modifica" };
  $("#relations").innerHTML = relations.map((r) => {
    const a = byId[r.doc_a], b = byId[r.doc_b];
    if (!a || !b) return "";
    const cls = r.relation === "version_conflict" ? "warnbox" : "ev";
    const det = r.relation === "version_conflict" ? ` (in vigoare: ${esc(r.detail.data_a || "nedeterminat")} vs ${esc(r.detail.data_b || "nedeterminat")}; clauze diferite: ${esc((r.detail.clauze_diferite || []).join(", ") || "-")}). Aplicabilitatea se stabileste dupa data evenimentului, nu dupa data incarcarii.`
      : r.relation === "amends" ? ` (articole referite: ${esc((r.detail.articole_referite || []).join(", "))})` : "";
    return `<div class="${cls}"><b>${esc(relLabel[r.relation] || r.relation)}:</b> ${esc(a.filename)} ↔ ${esc(b.filename)}${det}</div>`;
  }).join("");
  $("#docs").innerHTML = documents.length ? `<table><tr><th>Asigurator</th><th>Produs</th><th>Fisier</th><th>Tip (nivel)</th><th>Versiune</th><th>In vigoare din</th><th>Clauze</th><th>Semnale</th><th>Stare</th></tr>` +
    documents.map((d) => `<tr class="click" data-doc="${esc(d.id)}"><td>${esc(d.insurer || NS)}</td><td>${esc(d.product || NS)}</td><td>${esc(d.filename)}</td>
      <td>${esc(d.doc_type_label)} (${d.hierarchy_level ?? "-"})</td><td>${esc(d.version_label || NS)}</td>
      <td>${d.effective_date ? esc(d.effective_date) : `<span class="pill o-POTENTIALLY_COVERED">nedeterminat</span>`}</td><td>${d.clause_count}</td>
      <td>${d.ocr_pages ? `<span class="pill">OCR ${d.ocr_pages} p.</span> ` : ""}${d.injection_flags.length ? `<span class="pill o-LIKELY_EXCLUDED">text suspect</span> ` : ""}${d.metadata_overridden ? `<span class="pill">corectat manual</span>` : ""}</td>
      <td>${d.status === "ok" ? "OK" : `<span class="pill o-LIKELY_EXCLUDED">${esc(d.status)}</span> ${esc(d.error || "")}`}</td></tr>`).join("") + "</table>"
    : `<p class="muted">Niciun document indexat.</p>`;
  document.querySelectorAll("[data-doc]").forEach((tr) => tr.addEventListener("click", () => openDoc(tr.dataset.doc)));
}

async function openDoc(id) {
  const d = await api("/api/documents/" + id);
  const canEdit = ME && ["reviewer", "admin"].includes(ME.role);
  const ev = d.metadata.evidence || {};
  const types = Object.entries(STATUS.doc_types).map(([k, v]) => `<option value="${k}" ${k === d.doc_type ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#drawerBody").innerHTML = `<h2>${esc(d.filename)}</h2>
    <p class="muted small">Metadatele sunt extrase euristic; dovada fiecarei valori este afisata. Un reviewer le poate corecta (actiune auditata).</p>
    <form id="metaForm" class="stack">
      <label>Asigurator <input name="insurer" value="${esc(d.insurer || "")}" ${canEdit ? "" : "disabled"}></label><span class="small muted">Dovada: ${esc(ev.insurer || NS)}</span>
      <label>Produs <input name="product" value="${esc(d.product || "")}" ${canEdit ? "" : "disabled"}></label><span class="small muted">Dovada: ${esc(ev.product || NS)}</span>
      <label>Tip document <select name="doc_type" ${canEdit ? "" : "disabled"}>${types}</select></label>
      <label>Versiune <input name="version_label" value="${esc(d.version_label || "")}" ${canEdit ? "" : "disabled"}></label><span class="small muted">Dovada: ${esc(ev.version || NS)}</span>
      <label>In vigoare din (AAAA-LL-ZZ) <input name="effective_date" value="${esc(d.effective_date || "")}" ${canEdit ? "" : "disabled"}></label><span class="small muted">Dovada: ${esc(ev.effective_date || NS)}</span>
      ${canEdit ? `<button class="btn primary">Salveaza corectiile</button>` : ""}
    </form>
    ${d.injection_flags.length ? `<div class="badbox">Fragmente care par instructiuni catre un sistem AI (tratate exclusiv ca date):<ul>${d.injection_flags.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
    ${(d.warnings || []).map((w) => `<div class="warnbox">${esc(w)}</div>`).join("")}
    <p><a href="#" id="dlOrig">Descarca documentul original</a></p>
    <h3>Clauze extrase (${d.clauses.length})</h3>
    ${d.clauses.map((c) => `<div class="clause"><div class="meta small muted">${esc(c.number ? "Clauza " + c.number : "Fara numar")} · ${esc(STATUS.clause_kinds[c.kind] || c.kind)} · ${esc(c.location_label)} · ID ${esc(c.id)}</div><pre>${esc(c.text)}</pre></div>`).join("")}`;
  $("#drawer").classList.remove("hidden");
  $("#dlOrig").addEventListener("click", (e) => { e.preventDefault(); downloadAuth(`/api/documents/${id}/original`, d.filename.split("/").pop()); });
  if (canEdit) $("#metaForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target).entries());
    try { await api("/api/documents/" + id, { method: "PATCH", body: JSON.stringify(fd) }); toast("Metadate actualizate"); $("#drawer").classList.add("hidden"); loadDocs(); }
    catch (err) { toast(err.message); }
  });
}
$("#drawerClose").addEventListener("click", () => $("#drawer").classList.add("hidden"));

async function downloadAuth(url, name) {
  try {
    const res = await api(url);
    const blob = await res.blob();
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ incident
$("#exampleBtn").addEventListener("click", () => {
  $("#incidentText").value = "Un apartament din Bucuresti a fost avariat dupa ce o teava de apa s-a spart in baie. Apa a deteriorat parchetul, mobila si tavanul apartamentului de dedesubt. Locuinta a fost nelocuita timp de trei saptamani.";
});
$("#extractBtn").addEventListener("click", async () => {
  const btn = $("#extractBtn"); btn.disabled = true;
  try {
    EXTRACTION = await api("/api/incident/extract", { method: "POST", body: JSON.stringify({ text: $("#incidentText").value }) });
    renderFacts();
  } catch (e) { toast(e.message); }
  btn.disabled = false;
});

function fmtVal(v) { return Array.isArray(v) ? v.join(", ") : v ?? ""; }
function renderFacts() {
  const x = EXTRACTION;
  $("#factsMode").textContent = "Extractie: " + x.mode;
  const perils = (x.facts.incident_type?.value || []).map((p) => x.peril_labels[p] || p);
  $("#facts").innerHTML = `<table><tr><th>Fapt</th><th>Valoare</th><th>Statut</th><th>Sursa / rationament</th></tr>` +
    Object.entries(x.facts).map(([k, f]) => `<tr><td>${esc(x.fact_labels[k] || k)}</td><td>${esc(k === "incident_type" ? perils.join("; ") : fmtVal(f.value)) || "-"}</td>
      <td><span class="st ${esc(f.status)}">${esc(ST[f.status] || f.status)}</span></td><td class="small">${esc(f.evidence || "-")}</td></tr>`).join("") + "</table>";
  const cat = { building: "Cladire (bun asigurat)", contents: "Bunuri mobile (bun asigurat)", third_party: "Bunuri ale tertilor - raspundere civila", expenses: "Cheltuieli", other: "Altele" };
  $("#components").innerHTML = x.loss_components.map((c) => `<span class="pill" style="margin:2px">${esc(cat[c.category] || c.category)}: ${esc(c.component)}</span>`).join(" ");
  $("#factsCard").classList.remove("hidden");
  $("#questions").innerHTML = x.questions.length ? x.questions.map((q) => `<div class="q"><label for="q_${esc(q.fact)}">${esc(q.question)}</label>
      <div class="why">De ce conteaza: ${esc(q.why)}${q.current_status === "inferred" ? ` · Valoare dedusa acum: „${esc(fmtVal(q.current_value))}” (neconfirmata)` : ""}</div>
      <input id="q_${esc(q.fact)}" data-fact="${esc(q.fact)}" maxlength="1000"></div>`).join("")
    : `<p class="muted">Nu sunt necesare clarificari suplimentare pentru riscurile identificate.</p>`;
  $("#questionsCard").classList.remove("hidden");
}

$("#analyzeBtn").addEventListener("click", async () => {
  if (!EXTRACTION) return;
  const btn = $("#analyzeBtn"); btn.disabled = true; btn.textContent = "Se analizeaza documentele...";
  const answers = {};
  document.querySelectorAll("#questions input[data-fact]").forEach((i) => { if (i.value.trim()) answers[i.dataset.fact] = i.value.trim(); });
  try {
    ANALYSIS = await api("/api/analyses", { method: "POST", body: JSON.stringify({ extraction_id: EXTRACTION.extraction_id, answers }) });
    renderResults(); showTab("results");
  } catch (e) { toast(e.message, 6000); }
  btn.disabled = false; btn.textContent = "Compara produsele";
});

// ------------------------------------------------------------------ rezultate
function evHtml(e) {
  if (!e) return "";
  return `<div class="ev"><div class="tag">Text contractual${e.verified ? "" : " - NEVERIFICAT"}</div>
    <div class="meta">${esc(e.insurer)} · ${esc(e.document)} (${esc(e.doc_type)}, nivel ${esc(e.hierarchy_level)}) · in vigoare: ${esc(e.effective_date || NS)} · versiune: ${esc(e.version || NS)}</div>
    <div class="meta">Clauza <b>${esc(e.clause_number)}</b> · ${esc(e.location)}</div>
    <blockquote>„${esc(e.quote)}”</blockquote>
    ${e.exception_quote ? `<div class="small"><b>Exceptie de la excludere:</b> „${esc(e.exception_quote)}”</div>` : ""}
    <div class="tag">Interpretarea aplicatiei</div><div>${esc(e.interpretation)}</div>
    <div class="${e.verified ? "ver-ok" : "ver-ko"}">${e.verified ? "✓ Citat verificat literal in clauza" : "✗ " + esc(e.verification_note)}</div></div>`;
}

function renderResults() {
  const r = ANALYSIS;
  $("#noResults").classList.add("hidden"); $("#results").classList.remove("hidden");
  $("#verdict").className = "card verdict" + (r.verdict.type === "none" ? " none" : "");
  $("#verdict").innerHTML = `<b>${esc(r.verdict.text)}</b>`;
  $("#disclaimer").textContent = r.disclaimer;
  $("#analysisMeta").textContent = `Analiza ${r.id} · ${r.created_at} · mod: ${r.mode} · cautare: ${r.retrieval_backend} · metodologie v${r.methodology_version} · data de referinta: ${r.reference_date || "nedeterminata (s-au folosit versiunile curente)"}`;
  $("#rankTable").innerHTML = `<table><tr><th>Loc</th><th>Asigurator</th><th>Produs</th><th>Rezultat</th><th>Scor sustinere contractuala</th><th>Incredere dovezi</th><th>Incertitudine rezultat</th><th>Clauza principala</th><th>Excludere / limitare principala</th><th>Fransiza / sublimita</th><th>Informatii lipsa</th><th>Recomandare</th></tr>` +
    r.products.map((p) => { const t = p.table; return `<tr><td>${t.rank}</td><td>${esc(t.insurer)}</td><td>${esc(t.product)}</td>
      <td><span class="pill o-${esc(p.score.outcome)}">${esc(t.outcome)}</span></td><td class="score">${t.score}/100</td><td>${esc(t.evidence_confidence)}</td><td>${esc(t.uncertainty)}</td>
      <td>${esc(t.main_clause)}</td><td class="small">${esc(t.main_exclusion)}</td><td class="small">${esc(t.deductible_or_sublimit)}</td>
      <td class="small">${t.missing_info.map(esc).join("<br>") || "-"}</td><td>${esc(t.recommendation)}</td></tr>`; }).join("") + "</table>" +
    `<p class="small muted">Scorul (0-100) masoara puterea dovezilor contractuale disponibile pentru acest incident, nu probabilitatea de plata. Incredere dovezi = cat de fiabile si complete sunt citatele extrase. Incertitudine rezultat = cat depinde rezultatul de fapte nestabilite sau de interpretare.</p>`;
  renderRadar(r); renderDims(r); renderDetails(r);
}

function renderRadar(r) {
  $("#radarNote").textContent = r.radar.note;
  if (!window.Chart) { $("#radar").replaceWith(Object.assign(document.createElement("p"), { textContent: "Biblioteca de grafice nu s-a putut incarca; vedeti tabelul de scoruri." })); return; }
  const colors = ["#1f4e79", "#c0504d", "#4f8a3a", "#8064a2"];
  if (radarChart) radarChart.destroy();
  radarChart = new Chart($("#radar"), {
    type: "radar",
    data: { labels: r.radar.labels, datasets: r.radar.series.map((s, i) => ({ label: s.label, data: s.values, borderColor: colors[i], backgroundColor: colors[i] + "22", pointRadius: 3 })) },
    options: { scales: { r: { min: 0, max: 100, ticks: { stepSize: 25 } } }, plugins: { legend: { position: "bottom" } } },
  });
}

function renderDims(r) {
  const keys = Object.keys(r.dimension_labels);
  $("#dimTable").innerHTML = `<table><tr><th>Produs</th>${keys.map((k) => `<th title="pondere ${r.weights[k]}%">${esc(r.dimension_labels[k])} (${r.weights[k]}%)</th>`).join("")}</tr>` +
    r.products.map((p, i) => `<tr><td>${esc(p.product_label)}</td>${keys.map((k) => `<td class="dimcell score" data-p="${i}" data-k="${k}">${p.score.dimensions[k].score}</td>`).join("")}</tr>`).join("") + "</table>";
  document.querySelectorAll(".dimcell").forEach((c) => c.addEventListener("click", () => {
    const p = r.products[+c.dataset.p], d = p.score.dimensions[c.dataset.k];
    const evs = (d.evidence || []).filter(Boolean);
    $("#dimEvidence").innerHTML = `<h4>${esc(p.product_label)} · ${esc(r.dimension_labels[c.dataset.k])}: ${d.score}/100</h4><p>Nivel: <b>${esc(d.level)}</b></p>` +
      (evs.length ? evs.map(evHtml).join("") : `<p class="muted">Nicio dovada directa pentru aceasta dimensiune (scorul rezulta din regulile metodologiei sau din lipsa dovezilor).</p>`);
  }));
}

function list(items) { return items && items.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted">-</p>`; }
function renderDetails(r) {
  const cat = { building: "dauna la bunul asigurat (cladire)", contents: "dauna la bunul asigurat (bunuri mobile)", third_party: "raspundere civila fata de terti", other: "alta" };
  $("#details").innerHTML = r.products.slice(0, 3).map((p) => {
    const d = p.details, s = p.score;
    return `<div class="card"><div class="cardhead"><h2>${p.rank}. ${esc(p.product_label)}</h2><span class="pill o-${esc(s.outcome)}">${esc(s.outcome_label)} · ${s.overall}/100</span></div>
      <p><b>De ce:</b> ${s.outcome_reasons.map(esc).join(" ")} ${s.caps.map((c) => `<span class="pill o-LIKELY_EXCLUDED">${esc(c)}</span>`).join(" ")}</p>
      <p class="small">Incredere in dovezi: <b>${esc(s.evidence_confidence)}</b> · Incertitudinea rezultatului daunei: <b>${esc(s.uncertainty)}</b>${s.uncertainty_reasons.length ? " - " + s.uncertainty_reasons.map(esc).join("; ") : ""}</p>
      ${p.mode !== r.mode ? `<div class="warnbox">Mod analiza pentru acest produs: ${esc(p.mode)} ${esc(p.llm_error || "")}</div>` : ""}
      ${p.version_limitations.map((l) => `<div class="warnbox">${esc(l)}</div>`).join("")}
      <h4>Bun asigurat vs. raspundere civila</h4><ul>${d.property_vs_liability.map((c) => `<li>${esc(c.component)} - <i>${esc(cat[c.category] || c.category)}</i>: ${esc(c.coverage_level)}</li>`).join("")}</ul>
      <h4>1-2. Ce clauza sustine acoperirea si ce acopera</h4>${d.supporting_clauses.map(evHtml).join("") || `<p class="muted">${NS}</p>`}
      <h4>3. Ce parti ale pierderii pot fi despagubite</h4><p>Posibil: ${esc(d.covered_components.join("; ") || "-")}<br>Fara acoperire identificata: ${esc(d.uncovered_components.join("; ") || "-")}</p>
      <h4>4. Excluderi care ar putea fi aplicabile</h4>${d.exclusions.map(evHtml).join("") || `<p class="muted">Nicio excludere relevanta identificata in documentele analizate.</p>`}
      <h4>5. Fapte inca necunoscute</h4>${list(d.unknown_facts)}
      <h4>6. Limite si fransize</h4>${d.limits.map(evHtml).join("") || `<p class="muted">${NS}</p>`}
      <h4>7. Ce ar putea duce la respingerea sau reducerea daunei</h4>${list(d.reject_reasons)}
      <h4>8. Documente si dovezi de obtinut</h4>${list(d.documents_to_obtain)}
      <h4>9. De clarificat cu asiguratorul inainte de cumparare</h4>${list(d.clarify_before_purchase)}
      ${d.conflicts.length ? `<h4>Conflicte intre documente (necesita revizuire umana)</h4>${d.conflicts.map((c) => `<div class="warnbox">${esc(c.explanation)}</div>${evHtml(c.evidence)}${evHtml(c.evidence_b)}`).join("")}` : ""}
      ${d.unverified.length ? `<h4>Constatari neverificate (excluse din scor)</h4>${d.unverified.map(evHtml).join("")}` : ""}
      ${d.summary ? `<h4>Rezumat (interpretare)</h4><p>${esc(d.summary)}</p>` : ""}
      <p class="small muted">Product key pentru feedback: ${esc(p.product_key)}</p></div>`;
  }).join("");
  $("#fbAnalysis").value = r.id;
}

document.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", () => {
  if (!ANALYSIS) return;
  const f = b.dataset.export; downloadAuth(`/api/analyses/${ANALYSIS.id}/report.${f}`, `raport_${ANALYSIS.id}.${f}`);
}));

// ------------------------------------------------------------------ revizuire
async function loadFeedback() {
  if (!STATUS) return;
  $("#fbKind").innerHTML = Object.entries(STATUS.feedback_kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  try {
    const rows = await api("/api/feedback");
    $("#fbList").innerHTML = `<table><tr><th>#</th><th>Tip</th><th>Autor</th><th>Analiza / clauza</th><th>Continut</th><th>Stare</th><th></th></tr>` +
      rows.map((f) => `<tr><td>${f.id}</td><td>${esc(STATUS.feedback_kinds[f.kind] || f.kind)}</td><td>${esc(f.reviewer)}</td><td class="small">${esc(f.analysis_id || "-")}<br>${esc(f.clause_id || "")}</td>
        <td class="small">${esc(JSON.stringify(f.content))}</td><td>${esc(f.status)}${f.validated_by ? " (" + esc(f.validated_by) + ")" : ""}</td>
        <td>${f.status === "pending" ? `<button class="btn sm" data-fb="${f.id}" data-d="validate">Valideaza</button> <button class="btn sm" data-fb="${f.id}" data-d="reject">Respinge</button>` : ""}</td></tr>`).join("") + "</table>";
    document.querySelectorAll("[data-fb]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/feedback/${b.dataset.fb}/${b.dataset.d}`, { method: "POST" }); loadFeedback(); } catch (e) { toast(e.message); }
    }));
  } catch (e) { $("#fbList").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}
$("#fbForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const content = { text: $("#fbText").value };
  if ($("#fbRelevant").value !== "") content.relevant = $("#fbRelevant").value === "1";
  try {
    await api("/api/feedback", { method: "POST", body: JSON.stringify({ kind: $("#fbKind").value, analysis_id: $("#fbAnalysis").value || null,
      product_key: $("#fbProduct").value || null, clause_id: $("#fbClause").value || null, content }) });
    $("#fbText").value = ""; toast("Feedback inregistrat (in asteptarea validarii)"); loadFeedback();
  } catch (e) { toast(e.message); }
});

// ------------------------------------------------------------------ metodologie & audit
async function loadMethod() {
  const versions = await api("/api/methodology");
  const cur = versions.find((v) => v.status === "approved");
  const dl = STATUS.dimension_labels || {};
  if (cur) {
    $("#methodCurrent").innerHTML = `<p>Versiunea in vigoare: <b>v${cur.version}</b> (aprobata de ${esc(cur.approved_by)})</p><table><tr><th>Dimensiune</th><th>Pondere</th><th>Rubrica (nivel → puncte)</th></tr>` +
      Object.entries(cur.config.weights).map(([k, w]) => `<tr><td>${esc(k)}</td><td>${w}%</td><td class="small">${esc(JSON.stringify(cur.config.rubrics[k] || "calculat din dovezi"))}</td></tr>`).join("") +
      `</table><p class="small muted">Plafoane: ${esc(JSON.stringify(cur.config.gates))}</p>`;
    $("#weightInputs").innerHTML = Object.entries(cur.config.weights).map(([k, w]) => `<label>${esc(k)}<input type="number" step="1" min="0" max="100" data-w="${esc(k)}" value="${w}"></label>`).join("");
  }
  $("#methodVersions").innerHTML = `<h3>Versiuni</h3><table><tr><th>v</th><th>Stare</th><th>Autor</th><th>Nota</th><th></th></tr>` + versions.map((v) =>
    `<tr><td>${v.version}</td><td>${esc(v.status)}</td><td>${esc(v.created_by)}</td><td class="small">${esc(v.note)}</td>
     <td>${v.status === "proposed" && ME.role === "admin" ? `<button class="btn sm" data-mv="${v.version}" data-d="approve">Aproba</button> <button class="btn sm" data-mv="${v.version}" data-d="reject">Respinge</button>` : ""}</td></tr>`).join("") + "</table>";
  document.querySelectorAll("[data-mv]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/methodology/${b.dataset.mv}/${b.dataset.d}`, { method: "POST" }); STATUS = await api("/api/status"); renderStatus(); loadMethod(); } catch (e) { toast(e.message); }
  }));
  if (ME.role === "admin") {
    try {
      const rows = await api("/api/audit");
      $("#audit").innerHTML = `<table><tr><th>Moment</th><th>Utilizator</th><th>Actiune</th><th>Tinta</th><th>Detalii</th></tr>` +
        rows.map((a) => `<tr><td class="small">${esc(a.ts)}</td><td>${esc(a.username)} (${esc(a.role)})</td><td>${esc(a.action)}</td><td class="small">${esc(a.target)}</td><td class="small">${esc(JSON.stringify(a.detail))}</td></tr>`).join("") + "</table>";
    } catch (e) { $("#audit").textContent = e.message; }
  } else { $("#audit").innerHTML = `<p class="muted">Jurnalul de audit este disponibil doar administratorilor.</p>`; }
}
$("#methodForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const weights = {}; document.querySelectorAll("[data-w]").forEach((i) => (weights[i.dataset.w] = +i.value));
  try { await api("/api/methodology", { method: "POST", body: JSON.stringify({ weights, note: $("#methodNote").value }) }); toast("Propunere inregistrata; necesita aprobarea unui administrator."); loadMethod(); }
  catch (e) { toast(e.message); }
});
$("#retentionBtn").addEventListener("click", async () => {
  try { const r = await api("/api/retention/run", { method: "POST" }); toast(`Retentie aplicata: ${r.deleted} analize sterse`); } catch (e) { toast(e.message); }
});

boot();
