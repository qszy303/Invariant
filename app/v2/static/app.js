/* Invariant v2: vanilla JS, no dependencies, no network except this server. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tip = $("tip");
  const state = { data: null, selKey: null, pendingFile: null, busy: false };

  // ---- helpers ---------------------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MINUS = "−";
  const comma = (n) => Math.abs(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const whole = (cents) => { const d = Math.round(cents / 100); return (d < 0 ? MINUS : "") + comma(d); };
  const money = (cents, dp) => {
    const neg = cents < 0, a = Math.abs(cents);
    const d = Math.floor(a / 100), c = String(a % 100).padStart(2, "0");
    return (neg ? MINUS : "") + "$" + comma(d) + (dp ? "." + c : "");
  };
  const api = async (url, opts) => {
    const r = await fetch(url, opts);
    let body = null;
    try { body = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) throw new Error((body && (body.error || body.message)) || `Request failed (${r.status})`);
    return body;
  };
  const pretty = (s) => String(s || "").replace(/_/g, " ");
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  function toast(msg, err) {
    const t = document.createElement("div");
    t.className = "toast" + (err ? " err" : "");
    t.innerHTML = `<span class="dot"></span><span>${esc(msg)}</span>`;
    $("toasts").appendChild(t);
    setTimeout(() => t.remove(), err ? 6000 : 3800);
  }

  // ---- routing ---------------------------------------------------------------------
  function route() {
    const m = location.hash.match(/^#\/(check|eval|proven)/);
    const page = m ? m[1] : "check";
    for (const p of ["check", "eval", "proven"]) $("page-" + p).hidden = p !== page;
    document.querySelectorAll(".nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.page === page));
    if (page !== "check") closeDrawer();
    if (page === "eval") loadEval();
    if (page === "proven") loadProven();
  }
  window.addEventListener("hashchange", route);

  // ---- top bar -----------------------------------------------------------------------
  async function loadDemoFiles() {
    const files = await api("/api/demo-files");
    $("demo-select").innerHTML = '<option value="">Choose a demo file\u2026</option>' + files.map((f) =>
      `<option value="${esc(f.file)}" title="${esc(f.file)}">${esc(f.label)}</option>`).join("");
    const m = location.hash.match(/demo=([\w.\-]+)/);
    if (m && files.some((f) => f.file === m[1])) { $("demo-select").value = m[1]; runCheck(); }
  }

  function setBusy(b) {
    state.busy = b;
    $("run-btn").disabled = b;
    document.querySelector("#run-btn .spin").hidden = !b;
    document.querySelector("#run-btn .btn-label").textContent = b ? "Checking…" : "Run check";
  }

  async function runCheck() {
    if (state.busy) return;
    if (!state.pendingFile && !$("demo-select").value) {
      toast("Upload an .xlsx workbook first (or pick a demo file).", true);
      return;
    }
    setBusy(true);
    try {
      let data;
      if (state.pendingFile) {
        const fd = new FormData(); fd.append("file", state.pendingFile, state.pendingFile.name);
        data = await api("/api/check", { method: "POST", body: fd });
      } else {
        data = await api("/api/check", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ demo_file: $("demo-select").value }) });
      }
      state.selKey = null; closeDrawer(); render(data);
      if (data.fix_error) toast("Could not get fix proposals: " + data.fix_error, true);
    } catch (e) { toast(e.message, true); }
    finally { setBusy(false); }
  }

  $("run-btn").addEventListener("click", runCheck);
  $("upload-btn").addEventListener("click", () => $("upload-input").click());
  $("upload-input").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    state.pendingFile = f;
    $("upload-btn").textContent = f.name.length > 22 ? f.name.slice(0, 20) + "…" : f.name;
    $("file-name").textContent = f.name;
    $("file-sub").textContent = "Uploaded. Press Run check.";
  });
  $("demo-select").addEventListener("change", () => {
    state.pendingFile = null; $("upload-input").value = ""; $("upload-btn").textContent = "Upload";
  });

  // ---- rendering the check ---------------------------------------------------------
  function render(d) {
    state.data = d;
    $("file-name").textContent = d.name;
    $("file-sub").innerHTML = d.version > 0
      ? `Runway · Jan–Dec 2026 · <strong>fixed copy</strong>, your original is untouched`
      : "Runway · Jan–Dec 2026";
    const b = $("checker-badge");
    b.hidden = false;
    b.className = "badge " + d.checker;
    b.innerHTML = d.checker === "lean"
      ? `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8.5l4 4 8-9" fill="none" stroke="#111" stroke-width="2.2"/></svg>Verified in Lean 4`
      : "Python checker";
    const sb = $("spec-badge");
    sb.hidden = false;
    sb.className = "badge spec";
    sb.textContent = d.spec === "llm" ? "Spec: LLM" : "Spec: rules";
    sb.title = d.spec === "llm" ? `Rows read by ${d.spec_model}` : (d.spec_note || "Rows read by the deterministic rules baseline");
    if (d.spec_note && d.spec !== "llm") toast(d.spec_note, true);
    $("empty").hidden = true; $("kpis").hidden = false; $("gridcard").hidden = false;
    renderKpis(d); renderGrid(d); renderLoose(d);
  }

  function renderKpis(d) {
    const k = d.kpi;
    $("kpi-checks").textContent = k.checks;
    const v = $("kpi-viol"); v.textContent = k.violations; v.className = "kpi-num" + (k.violations ? " bad" : "");
    const r = $("kpi-review"); r.textContent = k.needs_review; r.className = "kpi-num" + (k.needs_review ? " warn" : "");
    const c = k.claimed_end_cents, t = k.verified_end_cents;
    if (c == null || t == null) { $("kpi-cash").innerHTML = '<span class="muted">n/a</span>'; $("kpi-cash-foot").textContent = ""; return; }
    const off = c !== t;
    $("kpi-cash").innerHTML =
      `<div class="amt claimed${off ? " off" : ""}"><b>${money(c)}</b><small>claimed</small></div>` +
      `<div class="arrow">→</div>` +
      `<div class="amt verified"><b>${money(t)}</b><small>verified</small></div>`;
    $("kpi-cash-foot").innerHTML = off
      ? `The sheet is off by <span class="gap-neg num">${money(c - t, true)}</span> against its own transactions.`
      : "The sheet matches the transactions behind it.";
  }

  function renderGrid(d) {
    const rows = d.cellgraph.rows, months = d.cellgraph.months;
    let h = "<thead><tr><th></th>" + months.map((m) => `<th>${m}</th>`).join("") + "</tr></thead><tbody>";
    for (const row of rows) {
      const strong = row.kind === "start" || row.kind === "end" || row.kind === "subtotal" || row.kind === "net";
      h += `<tr${strong ? ' class="strong"' : ""}><th class="rl" scope="row">${esc(row.label)}</th>`;
      row.cells.forEach((c, mi) => {
        if (!c) { h += '<td class="cell empty-cell">–</td>'; return; }
        const flagged = c.state === "violated" || c.state === "review";
        const cls = ["cell", c.state === "violated" ? "violated" : c.state === "review" ? "review" : "",
          flagged ? "flag" : "", c.formula ? "" : "typed",
          c.violation_key && c.violation_key === state.selKey ? "sel" : ""].filter(Boolean).join(" ");
        const attrs = flagged ? ` tabindex="0" role="button" data-r="${esc(row.label)}" data-m="${mi}"` : ` data-r="${esc(row.label)}" data-m="${mi}"`;
        h += `<td class="${cls}"${attrs}>${whole(c.value_cents)}</td>`;
      });
      h += "</tr>";
    }
    $("grid").innerHTML = h + "</tbody>";
  }

  function renderLoose(d) {
    const loose = d.result.violations.filter((v) => !v.cell);
    const box = $("loose");
    box.hidden = !loose.length;
    if (!loose.length) return;
    box.innerHTML = `<h3>Not tied to a single cell</h3><p>These transactions are in the exports but in no Runway line, so no one cell is wrong.</p><div class="chips">` +
      loose.map((v) => `<button class="chip-btn${v.key === state.selKey ? " sel" : ""}" data-key="${esc(v.key)}">${esc(v.label)}</button>`).join("") + "</div>";
  }

  // grid + chip interactions
  $("grid").addEventListener("click", (e) => {
    const td = e.target.closest("td.flag"); if (td) openCell(td);
  });
  $("grid").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("flag")) { e.preventDefault(); openCell(e.target); }
  });
  $("loose").addEventListener("click", (e) => {
    const b = e.target.closest(".chip-btn"); if (b) openDrawer(b.dataset.key);
  });

  function cellData(label, m) {
    const row = state.data.cellgraph.rows.find((r) => r.label === label);
    return row && row.cells[+m];
  }
  function openCell(td) {
    const c = cellData(td.dataset.r, td.dataset.m);
    if (c.violation_key) openDrawer(c.violation_key);
    else openDrawer(null, { label: td.dataset.r, m: +td.dataset.m });
  }

  // hover tooltip
  $("grid").addEventListener("mousemove", (e) => {
    const td = e.target.closest("td.cell[data-r]");
    if (!td) { tip.hidden = true; return; }
    const c = cellData(td.dataset.r, td.dataset.m);
    if (!c) { tip.hidden = true; return; }
    tip.innerHTML = `<b>${esc(c.ref)}</b>  ` + (c.formula ? esc(c.formula) : "typed value, no formula");
    tip.hidden = false;
    const w = tip.offsetWidth, x = Math.min(e.clientX + 14, window.innerWidth - w - 12);
    tip.style.left = Math.max(8, x) + "px"; tip.style.top = e.clientY + 18 + "px";
  });
  $("grid").addEventListener("mouseleave", () => { tip.hidden = true; });

  // ---- drawer ------------------------------------------------------------------------
  function closeDrawer() {
    state.selKey = null;
    document.body.classList.remove("drawer-open");
    $("drawer").setAttribute("aria-hidden", "true");
    document.querySelectorAll(".cell.sel, .chip-btn.sel").forEach((n) => n.classList.remove("sel"));
  }

  function sheetVals(v) {
    const flip = v.kind === "outflow";
    const f = (x) => (x == null ? null : flip ? -x : x);
    return { exp: f(v.expected_cents), act: f(v.actual_cents) };
  }

  function openDrawer(key, review) {
    const d = state.data;
    const dr = $("drawer");
    tip.hidden = true;
    let html;
    if (key) {
      const v = d.result.violations.find((x) => x.key === key);
      if (!v) return;
      state.selKey = key;
      const fix = d.fixes[key];
      const { exp, act } = sheetVals(v);
      const where = v.cell ? `${v.cell.row} · ${d.cellgraph.months[v.cell.month]}` : v.label.split(" — ")[0];
      html = head(`<span class="chip bad">${esc(pretty(v.invariant))}</span>`, where) + `<div class="d-body">` +
        `<p class="d-msg">${esc(d.explanations[key] || v.message)}</p>` +
        (exp != null && act != null ? `<div class="evd">
            <div><small>Expected</small><b>${money(exp, true)}</b></div>
            <div><small>Actual</small><b>${money(act, true)}</b></div>
            <div class="diffrow"><small>Difference</small><b>${money(act - exp, true)}</b></div></div>` : "") +
        txnTable(v) + fixBlock(fix) + `</div>` + foot(fix);
    } else {
      const c = cellData(review.label, review.m);
      state.selKey = null;
      html = head(`<span class="chip warn">Needs review</span>`, `${review.label} · ${d.cellgraph.months[review.m]}`) +
        `<div class="d-body"><p class="d-msg">The reading of this row (which transactions belong in it) has low confidence, so Invariant would not stake a verdict on it. A person should confirm the rule.</p>` +
        `<div><div class="sec-title">Formula</div><div class="diff"><div class="plain">${esc(c.formula || "typed value, no formula")}</div></div></div></div>`;
    }
    dr.innerHTML = html;
    dr.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    document.querySelectorAll(".cell.sel, .chip-btn.sel").forEach((n) => n.classList.remove("sel"));
    if (key) {
      const v = d.result.violations.find((x) => x.key === key);
      if (v.cell) {
        const tds = document.querySelectorAll(`td.cell[data-r="${CSS.escape(v.cell.row)}"][data-m="${v.cell.month}"]`);
        tds.forEach((n) => n.classList.add("sel"));
      } else {
        const b = document.querySelector(`.chip-btn[data-key="${CSS.escape(key)}"]`); if (b) b.classList.add("sel");
      }
    } else {
      document.querySelectorAll(`td.cell[data-r="${CSS.escape(review.label)}"][data-m="${review.m}"]`).forEach((n) => n.classList.add("sel"));
    }
    dr.querySelector(".xbtn").addEventListener("click", closeDrawer);
    const ab = dr.querySelector("#apply-btn");
    if (ab) ab.addEventListener("click", () => applyFix(key, ab));
  }

  const head = (chip, title) => `<div class="d-head"><div>${chip}<h2 class="d-title">${esc(title)}</h2></div><button class="xbtn" aria-label="Close">×</button></div>`;

  function txnTable(v) {
    if (!v.txns.length) return "";
    const SHOW = 5, shown = v.txns.slice(0, SHOW);
    const rows = shown.map((t) => `<tr><td>${esc(t.date || "")}</td><td>${esc(t.source || "")}</td><td>${esc(t.counterparty || t.id)}</td><td class="r">${t.amount_cents == null ? "" : money(t.amount_cents, true)}</td></tr>`).join("");
    const hidden = Math.max(v.txn_ids.length, v.txns.length) - shown.length;
    const extra = hidden > 0 ? `<tr><td colspan="4" class="more">+ ${hidden} more</td></tr>` : "";
    const dups = (v.duplicate_rows || []).map((d) =>
      `${esc(d.id)} appears on rows ${d.rows.map((r) => r.row).join(" and ")} of the ${esc(d.rows[0].sheet)} tab.`).join(" ");
    return `<div><div class="sec-title">${esc(v.txns_title)}</div><table class="tx"><thead><tr><th>Date</th><th>Source</th><th>Counterparty</th><th class="r">Amount</th></tr></thead><tbody>${rows}${extra}</tbody></table>${dups ? `<p class="note">${dups}</p>` : ""}</div>`;
  }

  function fixBlock(fix) {
    if (!fix || !fix.old) {
      return `<div><div class="sec-title">Proposed fix</div><div class="vbox no"><span>No verified fix for this one. It needs a person.${fix && fix.note ? " " + esc(fix.note) : ""}</span></div></div>`;
    }
    const verdict = fix.blocked
      ? `<div class="vbox block" style="margin-top:0"><span>${esc(fix.blocked_reason)}</span><small>This row is flagged for review: the reading of what belongs in it is not confident, so a fix checked against that reading can't be trusted.</small></div>`
      : fix.verified
      ? `<div class="vbox"><span>Verified: re-checked on a copy, violation cleared, ${fix.recheck.new_violations} new violations</span></div>`
      : `<div class="vbox no"><span>Not verified. The re-check did not pass, so this will not be applied.</span></div>`;
    const diff = `<div class="diff"><div class="old">${esc(fix.old)}</div><div class="new">${esc(fix.new)}</div></div>${fix.reason ? `<p class="note">${esc(fix.reason)}</p>` : ""}`;
    return fix.blocked
      ? `<div><div class="sec-title">Proposed fix</div>${verdict}<div style="margin-top:12px">${diff}</div></div>`
      : `<div><div class="sec-title">Proposed fix</div>${diff}${verdict}</div>`;
  }

  const foot = (fix) => fix && fix.verified && !fix.blocked
    ? `<div class="d-foot"><button class="btn btn-primary btn-block" id="apply-btn"><span class="spin" hidden></span><span class="btn-label">Apply verified fix</span></button></div>` : "";

  async function applyFix(key, btn) {
    if (state.busy) return;
    state.busy = true;
    btn.disabled = true;
    btn.querySelector(".spin").hidden = false;
    btn.querySelector(".btn-label").textContent = "Re-checking…";
    try {
      const [res] = await Promise.all([
        api("/api/fix/apply", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ violation_key: key, check_id: state.data.check_id }) }),
        new Promise((r) => setTimeout(r, 450)),
      ]);
      closeDrawer(); render(res);
      if (res.resolved) toast(`Re-verified: ${res.new_violations} new violations`);
      else toast("Fix applied, but the violation is still there. Nothing was marked verified.", true);
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false; btn.querySelector(".spin").hidden = true;
      btn.querySelector(".btn-label").textContent = "Apply verified fix";
    } finally { state.busy = false; }
  }

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  // ---- eval page -------------------------------------------------------------------------
  let evalLoaded = false;
  const pct = (x) => (x == null ? "n/a" : Math.round(x * 100) + "%");
  async function loadEval() {
    if (evalLoaded) return;
    try {
      const d = await api("/api/eval");
      const meta = d.meta || {};
      const agentOk = d.metrics.agent && d.metrics.agent.status === "ok" && !/skipped/.test(meta.agent_status || "");
      const src = agentOk ? "agent" : "rules";
      const m = d.metrics[src], L = m.lean && m.lean.overall ? m.lean : m.python;
      const checker = m.lean && m.lean.overall ? "Lean checker" : "Python checker (Lean not built)";
      const nTypes = Object.keys(L.by_bug).length;
      $("eval-sub").textContent = `Synthetic mutation suite: ${L.overall.n} planted bugs across ${nTypes} types, plus ${L.clean.n} clean models. ${checker}.`;
      $("eval-spec").textContent = agentOk
        ? `Spec: LLM agent (${meta.agent_model}${/cache/.test(meta.agent_status) ? ", from cached responses" : ""}).`
        : "Spec: rules baseline (no LLM).";
      drawChart(L.by_bug);
      const fp = L.clean.false_positive_rate;
      $("ev-fp").textContent = pct(fp);
      $("ev-fp-foot").textContent = `${L.clean.flagged} of ${L.clean.n} clean models flagged`;
      const o = L.overall;
      $("ev-loc").textContent = pct(o.localization_accuracy);
      const noCell = Object.entries(L.by_bug).filter(([, b]) => !b.cell_gt_n).map(([k]) => k);
      $("ev-loc-foot").textContent = o.cell_gt_n
        ? `${o.localized_cell} of ${o.cell_gt_n} flagged on a ground-truth cell` + (noCell.length ? ` (${noCell.join(", ")} has no cell)` : "")
        : "not computed: re-run make eval";
      const a = m.python_vs_lean;
      $("ev-agree").textContent = pct(a.rate);
      $("ev-agree-foot").textContent = `${a.agree} of ${a.n} outputs byte-identical`;
      drawRobustness(d.robustness);
      const misses = d.known_misses || [];
      $("eval-misses").innerHTML = misses.length ? misses.map((x) =>
        `<div class="miss"><div class="tag">${esc(x.model)}<small>${esc(x.bug)} · ${esc(x.spec)} spec</small></div><div>${inlineCode(x.why)}</div></div>`).join("")
        : '<p class="muted">None recorded.</p>';
      evalLoaded = true;
    } catch (e) { $("eval-sub").textContent = e.message; }
  }

  function drawRobustness(r) {
    if (!r) { $("rob-sub").textContent = ""; $("eval-rob").innerHTML = '<p class="muted">Not run.</p>'; return; }
    $("rob-sub").textContent = `Hand-made variants the rules baseline can't read, read by ${r.model}. Review threshold ${r.threshold}.`;
    const yn = (v, good) => v ? `<span class="${good ? "yes" : "no-good"}">yes</span>` : `<span class="${good ? "no-good" : "yes"}">no</span>`;
    const rows = Object.entries(r.variants).map(([name, v]) => v.status !== "ok"
      ? `<tr><td>${esc(name)}</td><td class="nr">not run</td><td class="nr">not run</td><td class="nr">not run</td></tr>`
      : `<tr><td>${esc(name)}</td><td>${yn(v.matches_gold, true)}</td><td>${v.flagged ? `<span class="flag">yes</span><small>${esc(v.flagged_lines.join(", "))}</small>` : "no"}</td><td>${yn(v.wrong_and_confident, false)}</td></tr>`).join("");
    $("eval-rob").innerHTML = `<table class="rob"><thead><tr><th>Variant</th><th>Matches gold</th><th>Flagged for review</th><th>Wrong and confident</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function drawChart(byBug) {
    const names = Object.keys(byBug);
    const W = 1100, rowH = 58, bh = 18, top = 6, left = 200, right = 150, H = top + names.length * rowH + 36;
    const plot = W - left - right, x = (p) => left + plot * p;
    const mono = 'font-family="JetBrains Mono, ui-monospace, monospace"';
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Detection and localization rate by bug type">`;
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      s += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - 30}" stroke="#E3E3DE"/>` +
        `<text x="${x(t)}" y="${H - 8}" text-anchor="middle" font-size="16" fill="#5F5F5A">${Math.round(t * 100)}%</text>`;
    }
    names.forEach((n, i) => {
      const b = byBug[n], y = top + i * rowH + 6;
      const det = b.n ? b.detected / b.n : 0;
      s += `<text x="${left - 16}" y="${y + bh + 6}" text-anchor="end" font-size="16" fill="#111" ${mono}>${esc(n)}</text>` +
        `<rect x="${left}" y="${y}" width="${Math.max(2, plot * det)}" height="${bh}" rx="3" fill="#F2E205" stroke="#111" stroke-width="1.5"/>` +
        `<text x="${left + plot + 14}" y="${y + 14}" font-size="15" font-weight="600" fill="#111" ${mono}>${b.detected}/${b.n}</text>`;
      const y2 = y + bh + 6;
      if (b.cell_gt_n) {
        s += `<rect x="${left}" y="${y2}" width="${Math.max(2, plot * b.localized_cell / b.cell_gt_n)}" height="${bh}" rx="3" fill="none" stroke="#111" stroke-width="1.5"/>` +
          `<text x="${left + plot + 14}" y="${y2 + 14}" font-size="15" fill="#5F5F5A" ${mono}>${b.localized_cell}/${b.cell_gt_n}</text>`;
      } else {
        s += `<text x="${left + 6}" y="${y2 + 14}" font-size="15" fill="#5F5F5A">no Runway cell to localize to</text>` +
          `<text x="${left + plot + 14}" y="${y2 + 14}" font-size="15" fill="#5F5F5A" ${mono}>n/a</text>`;
      }
    });
    $("eval-chart").innerHTML = s + "</svg>";
  }

  // ---- proven page ---------------------------------------------------------------------
  let provenLoaded = false;
  const inlineCode = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  async function loadProven() {
    if (provenLoaded) return;
    try {
      const d = await api("/api/proven");
      $("proven-code").textContent = [d.definition, ...d.theorems].filter(Boolean).join("\n\n");
      $("proven-list").innerHTML = d.proven.map((x) => `<li>${inlineCode(x)}</li>`).join("");
      $("notproven-list").innerHTML = d.not_proven.map((x) => `<li>${inlineCode(x)}</li>`).join("");
      provenLoaded = true;
    } catch (e) { $("proven-code").textContent = e.message; }
  }

  // ---- boot ----------------------------------------------------------------------------
  route();
  loadDemoFiles().catch((e) => toast(e.message, true));
})();
