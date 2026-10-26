// RoleRadar dashboard. Plain JavaScript: it calls the same API you saw at /docs.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { profile: null, tab: "review", query: "", builds: new Map(), selected: new Set(), timer: null };

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private window: fine */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc.slice(-1)[0]}: ${d.msg}`).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data;
}
const P = (path = "") => `/api/profiles/${encodeURIComponent(state.profile)}${path}`;

let toastTimer;
function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), isError ? 7000 : 3500);
}
async function act(fn, success) {
  try {
    const result = await fn();
    if (success) toast(typeof success === "function" ? success(result) : success);
    await refresh();
    return result;
  } catch (error) {
    toast(error.message, true);
  }
}

// --- Profiles ------------------------------------------------------------------------------------

async function loadProfiles() {
  const profiles = await api("/api/profiles");
  const valid = profiles.filter((p) => p.valid);
  $("profile").innerHTML = profiles
    .map((p) => `<option value="${esc(p.name)}" ${p.valid ? "" : "disabled"}>${esc(p.display_name || p.name)}${p.valid ? "" : " (has errors)"}</option>`)
    .join("");
  const saved = recall("profile");
  const pick = valid.find((p) => p.name === saved) || valid.find((p) => p.name !== "example") || valid[0];
  state.profile = pick ? pick.name : null;
  if (pick) $("profile").value = pick.name;
}
$("profile").onchange = () => {
  state.profile = $("profile").value;
  remember("profile", state.profile);
  state.selected.clear();
  refresh(true);
};

// --- Setup checklist ---------------------------------------------------------------------------------

async function loadChecks() {
  const checks = await api(P("/checks"));
  const failing = checks.filter((c) => !c.ok);
  const pill = $("setupPill");
  pill.textContent = failing.length ? `Setup: ${failing.length} to fix` : "Setup complete";
  pill.className = "setup-pill " + (failing.length ? "bad" : "ok");
  $("checks").innerHTML = checks
    .map((c) => `<li class="${c.ok ? "ok" : "bad"}"><span class="mark" aria-label="${c.ok ? "OK" : "Needs fixing"}">${c.ok ? "✓" : "✗"}</span>
      <span>${esc(c.label)}</span><span class="detail">${esc(c.detail)}</span>
      ${c.fix ? `<span class="fix">${esc(c.fix)}</span>` : ""}</li>`)
    .join("");
  if (failing.length && !state.setupSeen) $("setup").hidden = false;
  state.setupSeen = true;
}
$("setupPill").onclick = () => ($("setup").hidden = !$("setup").hidden);

// --- Pipeline strip ---------------------------------------------------------------------------------------

function runLine(run, active) {
  if (!run) return "";
  const pct = run.total ? Math.round((100 * run.done) / run.total) : 0;
  const bar = active ? `<div class="bar"><i style="width:${pct}%"></i></div>` : "";
  const when = run.finished_at ? ` <span title="${esc(run.finished_at)} UTC">(${ago(run.finished_at)})</span>` : "";
  return bar + esc(run.message || "") + (active ? "" : when);
}

function ago(utc) {
  const seconds = (Date.now() - new Date(utc.replace(" ", "T") + "Z")) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function renderSummary(s) {
  const j = s.jobs;
  const total = Object.values(j).reduce((a, b) => a + b, 0);
  $("countCollected").textContent = total;
  $("countWaiting").textContent = j.passed;
  $("countRanked").textContent = j.ranked;
  $("countWritten").textContent = j.built;
  $("countApplied").textContent = j.applied;

  const tabCounts = { review: j.built, ranked: j.ranked, waiting: j.passed + j.new, dropped: j.dropped, applied: j.applied, archive: j.skipped + j.closed };
  document.querySelectorAll("#tabs button").forEach((b) => (b.querySelector("span").textContent = tabCounts[b.dataset.tab] || ""));

  const runs = s.latest_runs;
  const active = s.active;
  $("runScrape").innerHTML = active.scrape === "waiting"
    ? "Waiting for the Chrome extension to pick this up (checks every minute)…"
    : runLine(runs.scrape, active.scrape === "running");
  $("runFilter").innerHTML = runLine(runs.filter, false);
  $("runRank").innerHTML = runLine(runs.rank, active.rank);
  $("runBuild").innerHTML = runLine(runs.build, active.build);
  for (const [id, run] of [["runScrape", runs.scrape], ["runRank", runs.rank], ["runBuild", runs.build]]) {
    $(id).classList.toggle("failed", run?.status === "failed");
  }

  $("scrapeBtn").hidden = Boolean(active.scrape);
  $("scrapeStage").hidden = Boolean(active.scrape);
  $("scrapeCancel").hidden = !active.scrape;
  $("rankBtn").hidden = active.rank;
  $("rankStop").hidden = !active.rank;
  $("rankBtn").disabled = j.passed === 0;
  $("rankBtn").textContent = j.passed ? `Rank ${j.passed} waiting job${j.passed === 1 ? "" : "s"}` : "Nothing to rank";
  $("buildBtn").hidden = active.build;
  $("buildStop").hidden = !active.build;
  $("buildBtn").disabled = j.ranked === 0;

  document.querySelector('[data-step="scrape"]').classList.toggle("active", Boolean(active.scrape));
  document.querySelector('[data-step="rank"]').classList.toggle("active", active.rank);
  document.querySelector('[data-step="build"]').classList.toggle("active", active.build);
  const busy = Boolean(active.scrape) || active.rank || active.build;
  document.querySelector(".pipeline").classList.toggle("busy", busy);
  return busy;
}

$("scrapeBtn").onclick = () => act(
  () => api(P("/scrape"), { method: "POST", body: { stage: $("scrapeStage").value ? Number($("scrapeStage").value) : null } }),
  (r) => `Scrape requested for ${r.searches} searches. Keep Chrome open; a LinkedIn tab will open within a minute.`);
$("scrapeCancel").onclick = () => act(() => api(P("/scrape/cancel"), { method: "POST" }), "Scrape cancelled");
$("refilterBtn").onclick = () => act(() => api(P("/filter?rerun=true"), { method: "POST" }),
  (r) => `Re-checked ${r.checked} jobs: ${r.passed} pass, ${r.dropped} dropped`);
$("rankBtn").onclick = () => act(() => api(P("/rank"), { method: "POST" }), (r) => r.message);
$("rankStop").onclick = () => act(() => api(P("/rank/stop"), { method: "POST" }), "Stopping after the current job");
$("buildBtn").onclick = () => act(() => api(P("/build"), { method: "POST", body: { count: Number($("buildCount").value) || 5 } }), (r) => r.message);
$("buildStop").onclick = () => act(() => api(P("/build/stop"), { method: "POST" }), "Stopping after the current job");

// --- Job lists ---------------------------------------------------------------------------------------------

const TAB_STATUS = { review: ["built"], ranked: ["ranked"], waiting: ["passed", "new"], dropped: ["dropped"], applied: ["applied"], archive: ["skipped", "closed"] };
const EMPTY = {
  review: "Nothing to review yet. When applications are written they appear here, with the CV, cover letter and posting.",
  ranked: "No ranked jobs. Rank the jobs waiting for AI, or scrape for new ones.",
  waiting: "No jobs waiting. Scrape LinkedIn to collect new ones.",
  dropped: "Nothing dropped yet. Every job the rules or the AI rejected shows up here with the reason.",
  applied: "No applications marked yet. Tick “Applied” on a job once you have sent it.",
  archive: "Jobs you skip, and postings that closed, end up here.",
};

document.querySelectorAll("#tabs button").forEach((button) => {
  button.onclick = () => {
    state.tab = button.dataset.tab;
    document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b === button)));
    state.selected.clear();
    loadList();
  };
});
let searchTimer;
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = $("search").value.trim(); loadList(); }, 250);
};

async function loadList() {
  const statuses = TAB_STATUS[state.tab];
  const sort = ["ranked", "review"].includes(state.tab) ? "score" : "newest";
  const q = state.query ? `&q=${encodeURIComponent(state.query)}` : "";
  const pages = await Promise.all(statuses.map((s) => api(P(`/jobs?status=${s}&sort=${sort}&limit=200${q}`))));
  const items = pages.flatMap((p) => p.items);
  if (state.tab === "review") {
    const builds = await api(P("/builds"));
    state.builds = new Map(builds.map((b) => [b.job_id, b]));
  }
  $("buildSelected").hidden = !(state.tab === "ranked" && state.selected.size);
  $("list").innerHTML = items.length ? items.map(card).join("") : `<div class="empty">${esc(state.query ? "No jobs match your search." : EMPTY[state.tab])}</div>`;
}

function card(job) {
  const where = [job.company, job.location].filter(Boolean).map(esc).join(", ");
  const title = `<h3><button type="button" data-open="${job.id}">${esc(job.title)}</button></h3><div class="where">${where}</div>`;
  const score = job.rank_score != null ? `<div class="score">${job.rank_score}<small>score</small></div>` : "<div></div>";

  if (state.tab === "review") {
    const b = state.builds.get(job.id) || {};
    const flags = (b.flags || []).map((f) => `<span class="flag">${esc(f)}</span>`).join("");
    const doc = (kind, label) => b[`${kind}_pdf`]
      ? `<a href="${P(`/builds/${b.id}/${kind}.pdf`)}" target="_blank" rel="noopener">${label}</a>`
      : (b.id ? `<a href="${P(`/builds/${b.id}/${kind}.html`)}" target="_blank" rel="noopener">${label} (HTML)</a>` : "");
    return `<article class="job no-check"><div>${title}
      ${b.fit_summary ? `<p class="why">${esc(b.fit_summary)}</p>` : ""}
      ${flags ? `<div class="flags">${flags}</div>` : ""}
      <div class="actions">${doc("cv", "CV")}${doc("letter", "Cover letter")}
        <a href="${esc(job.url)}" target="_blank" rel="noopener">Posting</a>
        <button class="primary" data-status="applied" data-id="${job.id}">Applied</button>
        <button data-status="skipped" data-id="${job.id}">Skip</button>
      </div></div>${score}</article>`;
  }
  if (state.tab === "ranked") {
    const checked = state.selected.has(job.id) ? "checked" : "";
    return `<article class="job"><input type="checkbox" data-select="${job.id}" ${checked} aria-label="Select ${esc(job.title)}">
      <div>${title}${job.rank_reason ? `<p class="why">${esc(job.rank_reason)}</p>` : ""}
      <div class="actions"><a href="${esc(job.url)}" target="_blank" rel="noopener">Posting</a>
        <button data-status="skipped" data-id="${job.id}">Skip</button></div></div>${score}</article>`;
  }
  if (state.tab === "dropped") {
    return `<article class="job no-check"><div>${title}
      <p class="why drop">${esc(job.status_reason || "Dropped")}</p>
      ${job.evidence && job.evidence !== job.title ? `<p class="evidence">“${esc(job.evidence)}”</p>` : ""}</div>${score}</article>`;
  }
  const undo = state.tab === "applied" || state.tab === "archive"
    ? `<div class="actions"><a href="${esc(job.url)}" target="_blank" rel="noopener">Posting</a>
       ${job.status === "skipped" ? `<button data-status="ranked" data-id="${job.id}">Undo skip</button>` : ""}</div>`
    : "";
  const note = job.status === "closed" ? `<p class="why drop">${esc(job.status_reason || "Closed")}</p>` : "";
  return `<article class="job no-check"><div>${title}${note}${undo}</div>${score}</article>`;
}

$("list").addEventListener("click", (event) => {
  const open = event.target.closest("[data-open]");
  if (open) return showDetail(Number(open.dataset.open));
  const status = event.target.closest("[data-status]");
  if (status) {
    const label = { applied: "Marked applied. Good luck!", skipped: "Skipped", ranked: "Back in Ranked" }[status.dataset.status];
    return act(() => api(P(`/jobs/${status.dataset.id}/status`), { method: "POST", body: { status: status.dataset.status } }), label);
  }
});
$("list").addEventListener("change", (event) => {
  const box = event.target.closest("[data-select]");
  if (!box) return;
  const id = Number(box.dataset.select);
  box.checked ? state.selected.add(id) : state.selected.delete(id);
  $("buildSelected").hidden = !state.selected.size;
  $("buildSelected").textContent = `Write applications for ${state.selected.size} selected`;
});
$("buildSelected").onclick = () => act(
  () => api(P("/build"), { method: "POST", body: { job_ids: [...state.selected] } }),
  (r) => { state.selected.clear(); return r.message; });

// --- Job detail ---------------------------------------------------------------------------------------------

async function showDetail(id) {
  const job = await api(P(`/jobs/${id}`));
  const f = job.filter_result;
  const r = job.rank_result;
  const list = (items) => items.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const findings = (items) => list(items.map((x) => `${x.message}: “${x.evidence}”`));
  let html = `<h2>${esc(job.title)}</h2><div class="where">${esc(job.company)}${job.location ? ", " + esc(job.location) : ""}
    · <a href="${esc(job.url)}" target="_blank" rel="noopener">Open posting</a></div>`;
  if (job.status_reason) html += `<p class="why drop">${esc(job.status_reason)}</p>`;
  if (r) {
    const s = r.score;
    html += `<h3>AI ranking</h3><p class="why">${esc(r.judgement.reason)}</p>
      <div class="breakdown"><span>Fit <b>${s.fit}</b></span><span>Location <b>${s.location_bonus >= 0 ? "+" : ""}${s.location_bonus}</b>${s.location_label ? ` (${esc(s.location_label)})` : ""}</span>
      <span>Title match <b>+${s.title_bonus}</b></span><span>Junior reach <b>${s.junior_penalty ? "−" + s.junior_penalty : "0"}</b></span><span>Total <b>${s.total}</b></span></div>
      ${r.judgement.matches.length ? `<h3>Matches</h3>${list(r.judgement.matches)}` : ""}
      ${r.judgement.gaps.length ? `<h3>Gaps</h3>${list(r.judgement.gaps)}` : ""}
      <p class="hint">Judged by ${esc(r.model)}</p>`;
  }
  if (f) {
    if (f.reasons.length) html += `<h3>Rule filter: dropped because</h3>${findings(f.reasons)}`;
    if (f.notes.length) html += `<h3>Rule filter notes</h3>${findings(f.notes)}`;
  }
  html += `<h3>Description</h3><div class="desc">${esc(job.description || "No description was collected for this job.")}</div>`;
  $("detailBody").innerHTML = html;
  $("detail").showModal();
}

// --- Searches ---------------------------------------------------------------------------------------------------

async function loadSearches() {
  const searches = await api(P("/searches"));
  $("searches").innerHTML = searches.length ? searches.map((s) => `
    <li data-search="${s.id}">
      <input type="checkbox" data-field="enabled" ${s.enabled ? "checked" : ""} aria-label="Use this search">
      <a href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.url)}">${esc(s.label)}</a>
      <button class="quiet" data-delete aria-label="Delete ${esc(s.label)}">Delete</button>
      <div class="meta">
        <label>Stage <select data-field="stage">${[1, 2, 3].map((n) => `<option ${s.stage === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        <label>Pages <select data-field="pages">${[1, 2, 3].map((n) => `<option ${s.pages === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      </div>
    </li>`).join("") : `<li class="hint">No searches yet.</li>`;
}
$("searches").addEventListener("change", (event) => {
  const field = event.target.dataset.field;
  if (!field) return;
  const id = event.target.closest("[data-search]").dataset.search;
  const value = field === "enabled" ? event.target.checked : Number(event.target.value);
  act(() => api(P(`/searches/${id}`), { method: "PATCH", body: { [field]: value } }), "Search updated");
});
$("searches").addEventListener("click", (event) => {
  if (!event.target.closest("[data-delete]")) return;
  const id = event.target.closest("[data-search]").dataset.search;
  if (confirm("Delete this search?")) act(() => api(P(`/searches/${id}`), { method: "DELETE" }), "Search deleted");
});
$("searchForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const saved = await act(() => api(P("/searches"), { method: "POST", body: { label: form.get("label"), url: form.get("url") } }));
  if (!saved) return;
  event.target.reset();
  if (saved.suggested_url && confirm(`${saved.hints.join("\n\n")}\n\nUse the improved URL with these filters?`)) {
    await act(() => api(P(`/searches/${saved.id}`), { method: "PATCH", body: { url: saved.suggested_url } }), "Search saved with the suggested filters");
  } else {
    toast("Search saved");
  }
};

// --- Activity ---------------------------------------------------------------------------------------------------------

async function loadEvents() {
  const events = await api(P("/events?limit=30"));
  $("events").innerHTML = events.map((e) =>
    `<li class="${esc(e.level)}"><time datetime="${esc(e.at)}Z">${ago(e.at)}</time>${esc(e.message)}</li>`).join("");
}

// --- Refreshing ---------------------------------------------------------------------------------------------------------

let lastBusy = false;
async function refresh(full = false) {
  if (!state.profile) {
    $("list").innerHTML = `<div class="empty">No valid profile yet. Create one with: python -m roleradar new-profile your-name</div>`;
    return;
  }
  try {
    const summary = await api(P("/summary"));
    const busy = renderSummary(summary);
    const tasks = [loadList(), loadEvents()];
    if (full) tasks.push(loadSearches(), loadChecks());
    await Promise.all(tasks);
    // Poll fast while something runs, slowly otherwise. One last refresh when it stops.
    clearTimeout(state.timer);
    state.timer = setTimeout(() => refresh(lastBusy && !busy), busy ? 2000 : 10000);
    lastBusy = busy;
  } catch (error) {
    toast(error.message, true);
    clearTimeout(state.timer);
    state.timer = setTimeout(() => refresh(true), 10000);
  }
}
// Searches and checks change rarely; reload them when you come back to the tab.
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });

(async () => {
  try {
    await loadProfiles();
  } catch (error) {
    toast(error.message, true);
  }
  await refresh(true);
})();
