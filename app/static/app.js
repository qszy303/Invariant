// Invariant frontend: no framework, no build step. All text from workbooks goes through
// textContent (never innerHTML), because merchant names etc. are untrusted input.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { s: null, sel: null, cell: null, busy: false };

/* ---------- helpers ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const usd = (cents) => cents === null || cents === undefined ? "" :
  (cents < 0 ? "-" : "") + "$" + (Math.abs(cents) / 100).toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (v) => v === null || v === undefined ? "" :
  Math.round(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
const exact = (v) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function api(path, opts) {
  let res;
  try { res = await fetch(path, opts); }
  catch (e) { throw { code: "network", message: "Cannot reach the Invariant server. Is it still running?" }; }
  if (!res.ok) {
    let body = {};
    try { body = await res.json(); } catch (_) { /* not json */ }
    throw { code: body.error || "error", message: body.message || `Request failed (${res.status})` };
  }
  return res;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}) }).then((r) => r.json());

function banner(msg, kind, action) {
  const b = $("banner");
  b.replaceChildren(msg, action || "");
  b.className = "banner" + (kind === "warn" ? " warn" : "");
  b.hidden = !msg;
}

/* ---------- status chips, settings, proven ---------- */
async function refreshStatus() {
  const st = await (await api("/api/status")).json();
  const lean = $("chip-lean"), key = $("chip-key");
  lean.textContent = st.lean ? "Lean checker ✓" : "Python checker (Lean not built)";
  lean.className = "chip " + (st.lean ? "on" : "off");
  key.textContent = st.offline ? "Offline mode" : st.has_key ? "API key ✓" : "No API key";
  key.className = "chip " + (st.has_key && !st.offline ? "on" : "off");
  $("key-state").textContent = st.offline ? "Offline mode: the model is never called."
    : st.has_key ? "An API key is set for this session." : "No API key is set. Samples still work.";
  return st;
}
function openSettings() { $("settings").showModal(); $("key").focus(); }
$("open-settings").addEventListener("click", openSettings);
$("key-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.id !== "save-key") return;
  const key = $("key").value.trim();
  if (!key) return;
  try { await post("/api/key", { key }); $("key").value = ""; banner(""); await refreshStatus(); }
  catch (err) { banner(err.message); }
});
$("open-proven").addEventListener("click", async () => {
  try {
    const f = await (await api("/api/proven")).json();
    $("theorem").textContent = [f.definition, f.theorem].filter(Boolean).join("\n\n");
    const fill = (id, items) => $(id).replaceChildren(...items.map((t) => h("li", {}, ...inlineCode(t))));
    fill("proven-list", f.proven); fill("not-proven-list", f.not_proven);
  } catch (err) { banner(err.message); }
  $("proven").showModal();
});
function inlineCode(text) {  // `code` -> <code>, everything else plain text
  return text.split("`").map((part, i) => i % 2 ? h("code", {}, part) : part);
}

/* ---------- starting a session ---------- */
async function startSample(file) {
  banner("");
  try { load(await post("/api/session", { sample: file })); }
  catch (e) { banner(e.message); }
}
async function startUpload(file) {
  banner("");
  if (!file) return;
  if (!/\.xlsx$/i.test(file.name)) { banner("Please choose an .xlsx workbook."); return; }
  try {
    const res = await api("/api/upload?name=" + encodeURIComponent(file.name),
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    load(await res.json());
  } catch (e) { banner(e.message); }
}
function load(s) {
  state.s = s; state.cell = null;
  state.sel = s.violations.length ? Math.min(state.sel ?? 0, s.violations.length - 1) : null;
  render();
}
function reset() {
  state.s = null; state.sel = null; state.cell = null; banner("");
  $("start").hidden = false; $("work").hidden = true; $("file").value = "";
  history.replaceState(null, "", location.pathname);
}

const drop = $("drop");
$("file").addEventListener("change", (e) => startUpload(e.target.files[0]));
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => startUpload(e.dataTransfer.files[0]));
$("another").addEventListener("click", reset);

/* ---------- render ---------- */
function render() {
  const s = state.s;
  $("start").hidden = true; $("work").hidden = false;
  $("fname").textContent = s.name;
  $("fcopy").textContent = s.version ? ` · fixed copy v${s.version} (your original is untouched)` : "";
  const fl = $("flash");
  if (s.flash) { fl.textContent = s.flash; fl.hidden = false; } else fl.hidden = true;
  renderSummary(); renderActions(); renderGrid(); renderViolations(); renderDetail();
}

function renderSummary() {
  const { summary: m } = state.s, n = m.violations;
  let verdict;
  if (m.checker === "Lean") verdict = m.lean_agrees ? "verified by Lean ✓" : "Lean and Python disagree!";
  else verdict = "checked by the Python checker (Lean not built)";
  const extra = (m.checker === "Lean" && m.lean_agrees ? "Lean and Python agree byte-for-byte · " : "") + m.seconds + "s";
  const el = $("summary");
  el.className = "summary " + (n ? "bad" : "good");
  el.replaceChildren(`${m.checks} checks · ${n} violation${n === 1 ? "" : "s"} · ${verdict}`, h("small", {}, extra));
}

function renderActions() {
  const s = state.s, n = s.summary.violations;
  const find = $("find"), all = $("apply-all"), dl = $("download");
  find.hidden = !(n && !s.fixes.found);
  find.disabled = state.busy;
  find.replaceChildren(...(state.busy ? [h("span", { class: "spin" }), "Proposing and verifying fixes…"] : ["Find verified fixes"]));
  all.hidden = !s.fixes.todo;
  all.textContent = `Apply all verified fixes (${s.fixes.todo})`;
  dl.hidden = !s.fixes.can_download;
  dl.href = `/api/session/${s.sid}/download`;
  $("actions-hint").textContent = find.hidden ? "" :
    "The model proposes each fix; it is applied to a copy and re-checked before you see it.";
}
$("find").addEventListener("click", async () => {
  state.busy = true; renderActions(); banner("");
  try { load(await post(`/api/session/${state.s.sid}/fixes`)); }
  catch (e) {
    if (e.code === "no_key") banner(e.message, "warn", h("button", { onclick: openSettings }, "Open settings"));
    else banner(e.code === "proposer_failed" ? "The model could not be reached: " + e.message : e.message);
  }
  state.busy = false; renderActions();
});
$("apply-all").addEventListener("click", () => applyFix({ all: true }));
async function applyFix(body) {
  banner("");
  try { load(await post(`/api/session/${state.s.sid}/apply`, body)); }
  catch (e) { banner(e.message); }
}

function renderGrid() {
  const s = state.s, t = $("grid");
  const selV = state.sel !== null ? s.violations[state.sel] : null;
  const head = h("tr", {}, h("th", {}, ""), s.months.map((m) => h("th", {}, m)));
  const body = s.rows.map((row) => {
    const sub = ["Total Outflows", "Ending Cash"].includes(row.label);
    return h("tr", { class: sub ? "sub" : "" }, h("th", {}, row.label), row.cells.map((c, m) => {
      if (!c) return h("td", {}, "");
      const sel = (selV && selV.cell && selV.cell.row === row.label && selV.cell.month === m) ||
        (state.cell && state.cell.row === row.label && state.cell.month === m);
      return h("td", {
        class: [c.s, c.f === null ? "typed" : "", sel ? "selected" : ""].join(" ").trim(),
        tabindex: 0, title: (c.f ? `${c.ref} = ${c.f}` : `${c.ref} is a typed number`) + `  (${exact(c.v)})`,
        onclick: () => clickCell(row.label, m, c),
        onkeydown: (e) => { if (e.key === "Enter") clickCell(row.label, m, c); },
      }, num(c.v));
    }));
  });
  t.replaceChildren(h("thead", {}, head), h("tbody", {}, body));
}
function clickCell(label, m, c) {
  state.cell = { row: label, month: m };
  const i = state.s.violations.findIndex((v) => v.cell && v.cell.row === label && v.cell.month === m);
  if (i >= 0) state.sel = i;
  $("inspector").replaceChildren(h("b", {}, c.ref + "  " + exact(c.v)), c.f ? "   = " + c.f : "   typed number, not a formula");
  renderGrid(); renderViolations(); renderDetail(); revealDetail();
}
function revealDetail() { if (state.s.violations.length) $("detail").scrollIntoView({ behavior: "smooth", block: "nearest" }); }

function renderViolations() {
  const s = state.s;
  $("vlist-wrap").hidden = !s.violations.length;
  $("vlist").replaceChildren(...s.violations.map((v) =>
    h("button", { class: "vitem" + (state.sel === v.i ? " active" : ""),
      onclick: () => { state.sel = v.i; state.cell = null; renderGrid(); renderViolations(); renderDetail(); revealDetail(); } },
      v.fix && v.fix.verified ? h("span", { class: "vfix" }, "verified fix") : null, v.label)));
}

function renderDetail() {
  const s = state.s, el = $("detail");
  if (!s.violations.length) {
    el.replaceChildren(h("div", { class: "allclear" },
      h("div", { class: "big" }, "✓"), h("h2", {}, "All clear"),
      h("p", {}, "Every Runway row ties to its transactions, the books roll forward, and no transaction " +
        "is missed or counted twice."),
      s.version ? h("p", { class: "muted" }, "This is a fixed copy: download it above. Your original file was never changed.") : null));
    return;
  }
  const v = s.violations[state.sel];
  const kids = [h("h2", {}, v.label), h("p", { class: "explain" }, v.explanation)];
  if (v.cell && v.expected_cents !== null) {
    kids.push(h("div", { class: "metrics" },
      h("div", { class: "metric" }, h("div", { class: "k" }, "Sheet shows"), h("div", { class: "v" }, usd(Math.abs(v.actual_cents)))),
      h("div", { class: "metric" }, h("div", { class: "k" }, "Transactions add up to"), h("div", { class: "v" }, usd(Math.abs(v.expected_cents))))));
  }
  if (v.txns.length) {
    kids.push(h("div", { class: "lbl" }, v.txns_title === "contradicting" ? "Contradicting transactions" : "Transactions in this cell (largest 10)"),
      h("table", { class: "tx" },
        h("thead", {}, h("tr", {}, ["id", "date", "merchant", "category"].map((x) => h("th", {}, x)), h("th", { class: "num" }, "amount"))),
        h("tbody", {}, v.txns.map((t) => h("tr", {},
          h("td", {}, t.id), h("td", {}, t.date || ""), h("td", {}, t.counterparty || ""), h("td", {}, t.category || ""),
          h("td", { class: "num" }, usd(t.amount_cents)))))));
  }
  if (v.duplicate_rows.length) {
    kids.push(h("p", { class: "muted" }, "Repeated in the export: " +
      v.duplicate_rows[0].rows.map((r) => `${r.sheet} row ${r.row}`).join(", ")));
  }
  kids.push(h("div", { class: "lbl" }, "Proposed fix"));
  const f = v.fix;
  if (f && f.verified) {
    kids.push(h("div", {}, "Now:"), h("pre", { class: "code" }, f.old),
      h("div", {}, "Change to:"), h("pre", { class: "code" }, f.new), h("p", {}, f.reason),
      h("div", { class: "verified" }, "✓ Verified fix: applied to a copy, recalculated and re-checked." +
        (f.recheck && f.recheck.target_cleared && !f.recheck.new_violations ? " The violation is gone and no new one appears." : "")),
      h("button", { class: "primary", onclick: () => applyFix({ violation: v.i }) }, "Apply verified fix"));
  } else if (f && f.attempts.length) {
    const last = f.attempts[f.attempts.length - 1];
    kids.push(h("div", { class: "nofix" }, "No verified fix: this needs a human. The last try failed because: " +
      (last.detail || "it did not hold up on re-check.")));
  } else if (!s.fixes.found) {
    kids.push(h("p", { class: "muted" }, "Click “Find verified fixes” above. Each proposal is re-checked on a copy before it is offered."));
  } else {
    kids.push(h("p", { class: "muted" }, "No fix was produced for this violation."));
  }
  el.replaceChildren(...kids);
}

/* ---------- boot ---------- */
(async function boot() {
  try {
    await refreshStatus();
    const samples = await (await api("/api/samples")).json();
    $("samples").replaceChildren(...samples.map((x) =>
      h("button", { class: "sample", onclick: () => startSample(x.file) }, h("b", {}, x.label), h("span", {}, x.file))));
    // deep link for sharing or screenshots: #sample=slope_dup_row.xlsx&select=0&apply=all
    const q = new URLSearchParams(location.hash.slice(1));
    if (q.get("sample")) {
      await startSample(q.get("sample"));
      if (q.get("apply") === "all") await applyFix({ all: true });
      if (q.get("select") && state.s && state.s.violations.length) { state.sel = +q.get("select"); render(); }
    }
  } catch (e) { banner(e.message || String(e)); }
})();
