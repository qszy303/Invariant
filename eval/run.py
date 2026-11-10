"""Run the full pipeline over the suite and score it (owner: A6).

    uv run python -m eval.run            # = `make eval`

For every model in eval/suite/manifest.json:

    xlsx -> CellGraph (recalculated) -> RuleSpec -> CheckInput -> CheckResult

with both spec sources (`rules` baseline, `agent` LLM) and both checkers
(Python, and Lean when the binary is built). Scoring never changes the checker
or the mutations; it only compares the result with the ground truth in the
manifest.

Outputs: eval/results.json (everything, per model) and eval/RESULTS.md (tables
plus a known-misses section). The agent half needs ANTHROPIC_API_KEY or a warm
cache (PROOF_CACHE_DIR, default .proof_cache); without either it is reported as
skipped, never silently dropped. Recalculated sheets are cached under
eval/.cache by content hash so reruns are quick.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from eval.mutate import cell_ref
from eval.suite import EVAL_DIR, SUITE_DIR, build_suite, load_manifest
from proof.assign import build_checkinput
from proof.check import lean_bridge
from proof.check.py_checker import check as py_check
from proof.ingest.ledger import read_ledger
from proof.ingest.sheet import load_cellgraph, recalc_values_with_formulas
from proof.schemas import CellGraph, CheckResult, Ledger, RuleSpec
from proof.spec.agent import (
    DEFAULT_MODEL, AgentError, build_rulespec_llm, confidence_min, export_headers,
    needs_review, read_row_notes, spec_accuracy,
)
from proof.spec.rules import build_rulespec
from synth.workbook import ROW

CACHE_DIR = EVAL_DIR / ".cache"
SOURCES = ("rules", "agent")


# ---- inputs ----------------------------------------------------------------

def content_hash(path: Path) -> str:
    """Hash of the workbook's parts, ignoring docProps (which hold timestamps)."""
    h = hashlib.sha1()
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if name.startswith("docProps/"):
                continue
            h.update(name.encode())
            h.update(z.read(name))
    return h.hexdigest()


def graph_for(path: Path, cache_dir: Path | None = None) -> CellGraph:
    f = (cache_dir or CACHE_DIR) / "values" / f"{content_hash(path)}.json"
    if f.exists():
        values = json.loads(f.read_text())
    else:
        values = recalc_values_with_formulas(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(values))
    return load_cellgraph(path, values)


# ---- scoring ---------------------------------------------------------------

def violation_cell(v, label_by_line: dict[str, str]) -> str | None:
    label = label_by_line.get(v.line_id or "")
    if label not in ROW or v.month_idx is None:
        return None
    return f"Runway!{cell_ref(label, v.month_idx)}"


def score(result: CheckResult, truth: dict | None,
          label_by_line: dict[str, str]) -> dict[str, Any]:
    """Compare one CheckResult with the injected bug (or none, for clean)."""
    vs = result.violations
    out: dict[str, Any] = {
        "detected": not result.ok, "n_violations": len(vs),
        "invariants": sorted({v.invariant for v in vs}),
    }
    if truth is None:
        return out
    expected = set(truth["expected_invariants"])
    cells = set(truth["runway_cells"])
    ids = set(truth["txn_ids"])

    def hits(v) -> bool:
        return bool(ids & set(v.txn_ids)) or violation_cell(v, label_by_line) in cells

    out["correct_invariant"] = any(v.invariant in expected for v in vs)
    out["localized"] = any(v.invariant in expected and hits(v) for v in vs)
    out["off_target"] = sum(1 for v in vs if v.invariant not in expected or not hits(v))
    return out


def lean_json(ci) -> str | None:
    if not lean_bridge.is_available():
        return None
    return lean_bridge.check_json(ci)


# ---- one model -------------------------------------------------------------

def run_checkers(spec: RuleSpec, ledger: Ledger, graph: CellGraph, truth,
                 use_lean: bool) -> dict[str, Any]:
    """assign + both checkers for one spec; raises on pipeline refusal."""
    t0 = time.perf_counter()
    ci = build_checkinput(spec, ledger, graph)
    res = py_check(ci)
    labels = {sl.line_id: sl.row_label for sl in spec.lines}
    out: dict[str, Any] = {"python": score(res, truth, labels), "lean": None,
                           "agree": None}
    if use_lean:
        raw = lean_json(ci)
        if raw is not None:
            out["lean"] = score(CheckResult.model_validate_json(raw), truth, labels)
            out["agree"] = raw == res.to_canonical_json()
    out["check_s"] = round(time.perf_counter() - t0, 4)
    return out


def agent_spec(graph, ledger, path) -> RuleSpec:
    return build_rulespec_llm(
        graph, ledger, headers=export_headers(path), notes=read_row_notes(path))


def run_suite(manifest: dict, suite_dir: Path, *, agent: bool = True,
              lean: bool = True, workers: int = 6, limit: int | None = None,
              agent_fn=None, log=print) -> dict[str, Any]:
    models = manifest["models"][:limit] if limit else manifest["models"]
    prepared: dict[str, dict] = {}
    t0 = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for i, m in enumerate(models, 1):
            path = suite_dir / m["path"]
            p: dict[str, Any] = {"entry": m, "path": path, "error": None}
            try:
                p["graph"] = graph_for(path)
                p["ledger"] = read_ledger(path)
            except Exception as e:  # noqa: BLE001 - recorded, not hidden
                p["error"] = f"{type(e).__name__}: {e}"
            prepared[m["id"]] = p
            if i % 20 == 0:
                log(f"  loaded {i}/{len(models)} ({time.perf_counter() - t0:.0f}s)")

    # baseline specs (also the reference for spec accuracy)
    for p in prepared.values():
        p["spec"] = {}
        if p["error"]:
            continue
        t = time.perf_counter()
        try:
            p["spec"]["rules"] = build_rulespec(p["graph"])
        except Exception as e:  # noqa: BLE001
            p["spec_error"] = {"rules": f"{type(e).__name__}: {e}"}
        p["spec_s"] = {"rules": round(time.perf_counter() - t, 4)}

    # agent specs: I/O bound, so threaded
    if agent:
        def ask(p):
            t = time.perf_counter()
            try:
                return p, (agent_fn or agent_spec)(
                    p["graph"], p["ledger"], p["path"]), None, t
            except Exception as e:  # noqa: BLE001
                return p, None, f"{type(e).__name__}: {e}", t

        todo = [p for p in prepared.values() if not p["error"]]
        log(f"  agent specs for {len(todo)} models ({workers} workers)")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for k, (p, spec, err, t) in enumerate(pool.map(ask, todo), 1):
                p["spec_s"]["agent"] = round(time.perf_counter() - t, 3)
                if spec is not None:
                    p["spec"]["agent"] = spec
                else:
                    p.setdefault("spec_error", {})["agent"] = err
                if k % 20 == 0:
                    log(f"  agent {k}/{len(todo)}")

    results = []
    for m in models:
        p = prepared[m["id"]]
        row: dict[str, Any] = {"id": m["id"], "kind": m["kind"], "seed": m["seed"],
                               "placement": m["placement"],
                               "bug_id": (m["truth"] or {}).get("bug_id"),
                               "load_error": p["error"], "sources": {}}
        for src in SOURCES:
            if src == "agent" and not agent:
                row["sources"][src] = {"status": "skipped", "error": "--no-agent"}
                continue
            if p["error"]:
                row["sources"][src] = {"status": "error", "error": p["error"]}
                continue
            spec = p["spec"].get(src)
            if spec is None:
                err = p.get("spec_error", {}).get(src, "no spec")
                status = ("skipped" if src == "agent" and "ANTHROPIC_API_KEY" in err
                          else "error")
                row["sources"][src] = {"status": status, "error": err}
                continue
            try:
                r = run_checkers(spec, p["ledger"], p["graph"], m["truth"], lean)
            except Exception as e:  # noqa: BLE001
                row["sources"][src] = {"status": "error",
                                       "error": f"{type(e).__name__}: {e}"}
                continue
            r.update(status="ok", error=None, spec_s=p["spec_s"].get(src))
            if src == "agent":
                gold = p["spec"].get("rules")
                r["needs_review"] = needs_review(spec)
                if gold is not None:
                    acc = spec_accuracy(spec, gold, p["ledger"])
                    r["spec_accuracy"] = {
                        "accuracy": acc["accuracy"],
                        "assignment_equal": acc["assignment_equal"],
                        "wrong_lines": [k for k, v in acc["per_line"].items() if not v]
                        if isinstance(acc["per_line"], dict) else [],
                    }
            row["sources"][src] = r
        results.append(row)
    return {"models": results}


# ---- metrics ---------------------------------------------------------------

def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def compute_metrics(models: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src in SOURCES:
        rows = [(m, m["sources"][src]) for m in models]
        ran = [(m, s) for m, s in rows if s["status"] != "skipped"]
        if not ran:
            out[src] = {"status": "skipped"}
            continue
        block: dict[str, Any] = {"status": "ok"}
        for checker in ("python", "lean"):
            def sc(s):
                return s.get(checker) if s["status"] == "ok" else None

            by_bug: dict[str, dict[str, int]] = {}
            tot = {"n": 0, "detected": 0, "correct_invariant": 0, "localized": 0,
                   "errors": 0}
            for m, s in ran:
                if m["kind"] != "buggy":
                    continue
                b = by_bug.setdefault(m["bug_id"], dict.fromkeys(tot, 0))
                for t in (b, tot):
                    t["n"] += 1
                    if s["status"] == "error":
                        t["errors"] += 1
                        continue
                    r = sc(s)
                    if r is None:
                        continue
                    t["detected"] += r["detected"]
                    t["correct_invariant"] += r["correct_invariant"]
                    t["localized"] += r["localized"]
            clean = [(m, s) for m, s in ran if m["kind"] == "clean"]
            flagged = sum(1 for _, s in clean if sc(s) and sc(s)["detected"])
            if all(sc(s) is None for _, s in ran if s["status"] == "ok"):
                block[checker] = {"status": "unavailable"}
                continue
            for d in (*by_bug.values(), tot):
                d["detection_rate"] = _rate(d["detected"], d["n"])
                d["localization_rate"] = _rate(d["localized"], d["n"])
                d["correct_invariant_rate"] = _rate(d["correct_invariant"], d["n"])
            block[checker] = {
                "overall": tot, "by_bug": by_bug,
                "clean": {"n": len(clean), "flagged": flagged,
                          "false_positive_rate": _rate(flagged, len(clean)),
                          "violations": sum(sc(s)["n_violations"] for _, s in clean
                                            if sc(s)),
                          "errors": sum(1 for _, s in clean if s["status"] == "error")},
                "off_target_violations_on_buggy": sum(
                    sc(s)["off_target"] for m, s in ran
                    if m["kind"] == "buggy" and sc(s)),
            }
        ag = [s for _, s in ran if s["status"] == "ok" and "agree" in s
              and s["agree"] is not None]
        block["python_vs_lean"] = {"n": len(ag), "agree": sum(1 for s in ag if s["agree"]),
                                   "rate": _rate(sum(1 for s in ag if s["agree"]), len(ag))}
        times = [s["check_s"] for _, s in ran if s["status"] == "ok"]
        spec_t = [s["spec_s"] for _, s in ran if s["status"] == "ok" and s.get("spec_s")]
        block["runtime_s"] = {
            "check_median": round(statistics.median(times), 4) if times else None,
            "spec_median": round(statistics.median(spec_t), 3) if spec_t else None}
        block["errors"] = sum(1 for _, s in ran if s["status"] == "error")
        if src == "agent":
            accs = [s["spec_accuracy"] for _, s in ran
                    if s["status"] == "ok" and "spec_accuracy" in s]
            wrong = [s for _, s in ran if s["status"] == "ok"
                     and "spec_accuracy" in s
                     and not s["spec_accuracy"]["assignment_equal"]]
            block["spec_accuracy"] = {
                "n": len(accs),
                "mean_line_accuracy": round(statistics.mean(a["accuracy"] for a in accs), 4)
                if accs else None,
                "assignment_equal": sum(1 for a in accs if a["assignment_equal"]),
                "assignment_equal_rate": _rate(
                    sum(1 for a in accs if a["assignment_equal"]), len(accs)),
                "wrong_specs": len(wrong),
                "wrong_specs_with_flagged_lines": sum(1 for s in wrong if s["needs_review"]),
            }
        out[src] = block
    return out


# ---- report ----------------------------------------------------------------

# Why a bug went undetected, by bug id. Filled in only after looking at real
# results; anything not listed prints as unexplained so it cannot hide.
REASONS: dict[str, str] = {}

# Notes on individual misses, checked against results.json by hand.
MISS_NOTES: dict[tuple[str, str], str] = {
    ("agent", "overlap_category/s105_p2"):
        "the agent misread the `Other` row (the one carrying the extra merchant term); "
        "it flagged that line for review (confidence below the threshold), so the wrong "
        "spec was not confident. The sheet-vs-spec gap then showed up as traceability "
        "on `Other`, not as the double count",
}


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def render_fix_section(fix: dict, manifest: dict) -> list[str]:
    m = fix['metrics']
    o = m['overall']
    L = ['## Verified fixes (A7)', '']
    if o['ran'] == 0:
        return L + ['Skipped: no `ANTHROPIC_API_KEY` and no cached proposals.', '']
    L += [f"Each buggy model is explained, a fix is proposed by `{fix['meta']['proposer_model']}`, "
          'applied to a copy, recalculated and re-checked; it is accepted only if the targeted '
          'violation is gone and no new one appears (up to 3 proposals per violation). '
          '"Restored clean" is a separate check the loop never sees: the fixed copy recomputes '
          "to exactly the clean model's Runway values.", '',
          '| bug | n | not run | fixed (of those run) | first try | restored clean | needs human | proposals per model |',
          '|---|---|---|---|---|---|---|---|']
    for bug in manifest['bug_ids']:
        b = m['by_bug'].get(bug)
        if b:
            rc = f"{b['restored_clean']}/{b['restored_clean_of']}" if b['restored_clean_of'] else 'n/a'
            L.append(f"| `{bug}` | {b['n']} | {b['errors'] + b['skipped']} | {b['fixed']} ({_pct(b['fix_rate'])}) | {b['first_try']} "
                     f"| {rc} | {b['needs_human']} | {b['mean_proposals']} |")
    rc = f"{o['restored_clean']}/{o['restored_clean_of']}" if o['restored_clean_of'] else 'n/a'
    L += [f"| **all** | {o['n']} | {o['errors'] + o['skipped']} | **{o['fixed']} ({_pct(o['fix_rate'])})** | {o['first_try']} "
          f"| {rc} | {o['needs_human']} | {o['mean_proposals']} |", '']
    if o['errors'] or o['skipped']:
        errs = sorted({r['error'] for r in fix['models'] if r['status'] == 'error'})
        L += [f"{o['errors'] + o['skipped']} of {o['n']} models were not run (errors {o['errors']}, "
              f"skipped {o['skipped']}); they are excluded from the rates above, not counted as misses."]
        L += [f"First error: `{errs[0][:200]}`"] if errs else []
        L += ['']
    bad = [r for r in fix['models'] if r['status'] == 'ok' and (not r['fixed'] or r['restored_clean'] is False)]
    L += ['Fix misses:', '']
    if bad:
        L += ['| model | fixed | restored clean | needs human |', '|---|---|---|---|']
        L += [f"| `{r['id']}` | {r['fixed']} | {r['restored_clean']} | {r['needs_human']} |" for r in bad]
    else:
        L += ['None.']
    return L + ['']


def render_markdown(results: dict, manifest: dict, fix: dict | None = None) -> str:
    metrics = results["metrics"]
    models = results["models"]
    nb = sum(1 for m in models if m["kind"] == "buggy")
    nc = len(models) - nb
    L = ["# Proof eval results", "",
         f"{nb} buggy models ({len(manifest['bug_seeds'])} seeds x "
         f"{len(manifest['bug_ids'])} bug types x {manifest['placements']} placements) "
         f"and {nc} clean models. Generated by `make eval`; numbers below come from "
         "`eval/results.json`.", ""]
    meta = results["meta"]
    L += [f"- Agent model: `{meta['agent_model']}`; low-confidence threshold "
          f"{meta['confidence_min']}",
          f"- Lean checker: {'ran' if meta['lean'] else 'not available (run `make lean`)'}",
          f"- Agent spec source: {meta['agent_status']}", ""]
    for src in SOURCES:
        blk = metrics[src]
        title = {"rules": "Baseline spec (`rules.py`)", "agent": "LLM spec agent"}[src]
        L += [f"## {title}", ""]
        if blk["status"] == "skipped":
            L += ["Skipped: no `ANTHROPIC_API_KEY` and no cached responses.", ""]
            continue
        for checker in ("python", "lean"):
            c = blk.get(checker)
            if not c or c.get("status") == "unavailable":
                continue
            L += [f"### {checker.capitalize()} checker", "",
                  "| bug | n | detected | right invariant | localized | pipeline errors |",
                  "|---|---|---|---|---|---|"]
            for bug in manifest["bug_ids"]:
                b = c["by_bug"].get(bug)
                if b:
                    L.append(f"| `{bug}` | {b['n']} | {b['detected']} ({_pct(b['detection_rate'])}) "
                             f"| {b['correct_invariant']} | {b['localized']} | {b['errors']} |")
            o = c["overall"]
            L += [f"| **all** | {o['n']} | **{o['detected']} ({_pct(o['detection_rate'])})** "
                  f"| {o['correct_invariant']} | {o['localized']} | {o['errors']} |", ""]
            cl = c["clean"]
            L += [f"False positives on clean models: **{cl['flagged']} of {cl['n']}** "
                  f"({_pct(cl['false_positive_rate'])}), {cl['violations']} violations in total.",
                  f"Violations on buggy models that were not the injected bug "
                  f"(wrong invariant or wrong place): {c['off_target_violations_on_buggy']}.", ""]
        pl = blk["python_vs_lean"]
        if pl["n"]:
            L += [f"Python vs Lean byte-identical output: {pl['agree']} of {pl['n']} "
                  f"({_pct(pl['rate'])}).", ""]
        rt = blk["runtime_s"]
        L += [f"Median runtime per model: check {rt['check_median']}s"
              + (f", spec {rt['spec_median']}s" if rt["spec_median"] else "") + ".", ""]
        if src == "agent" and blk.get("spec_accuracy"):
            a = blk["spec_accuracy"]
            L += [f"Spec accuracy vs the baseline reading of the same sheet ({a['n']} models "
                  f"where the baseline could read it): mean line accuracy "
                  f"{_pct(a['mean_line_accuracy'])}, identical assignment on "
                  f"{a['assignment_equal']} ({_pct(a['assignment_equal_rate'])}); "
                  f"{a['wrong_specs']} wrong specs, {a['wrong_specs_with_flagged_lines']} of them "
                  "had at least one line flagged for review.", ""]
    L += ["## Known misses", ""]
    misses = []
    for m in models:
        for src in SOURCES:
            s = m["sources"][src]
            if m["kind"] != "buggy" or s["status"] == "skipped":
                continue
            if s["status"] == "error":
                misses.append((src, m["id"], m["bug_id"], f"pipeline refused: {s['error']}"))
            elif not s["python"]["detected"]:
                misses.append((src, m["id"], m["bug_id"],
                               REASONS.get(m["bug_id"], "unexplained: investigate")))
            elif not s["python"]["correct_invariant"] or not s["python"]["localized"]:
                why = ("detected, but " + ("wrong invariant" if not
                                           s["python"]["correct_invariant"]
                                           else "wrong place"))
                note = MISS_NOTES.get((src, m["id"]))
                misses.append((src, m["id"], m["bug_id"],
                               why + (f": {note}" if note else "")))
    if misses:
        L += ["| spec | model | bug | why |", "|---|---|---|---|"]
        L += [f"| {a} | `{b}` | `{c}` | {d} |" for a, b, c, d in misses]
    else:
        L += ["None: every injected bug was detected, with the right invariant and place."]
    if fix is not None:
        L += [""] + render_fix_section(fix, manifest)
    L += ["", "## Scope and limits", "",
          "- Every mutation edits the workbook (a formula, a typed cell or an export row).",
          "- The Ledger is read from the export tabs, so an edit that keeps the export and "
          "the Runway consistent with each other is invisible by design (for example "
          "flipping a refund's sign inside Ramp itself). `sign_flip` is therefore injected "
          "in the Runway formula.",
          "- A typed number that happens to equal the true value is not a bug and is not "
          "flagged; `stale_hardcode` types a value 2-8% off.",
          "- `overlap_category` fires `no_double_count`; `traceability` does not, because "
          "the spec is read from the same (buggy) formulas, so the sheet and its own "
          "spec agree.",
          "- The suite is synthetic (one company shape, five seeds). Detection rates "
          "say what the checker does on this family, not on arbitrary real models.", ""]
    return "\n".join(L)


def write_report(out_dir: Path, suite_dir: Path = SUITE_DIR) -> None:
    """Regenerate RESULTS.md from results.json (+ fix_results.json if present)."""
    out_dir = Path(out_dir)
    manifest = load_manifest(suite_dir)
    res = json.loads((out_dir / "results.json").read_text())
    fp = out_dir / "fix_results.json"
    fix = json.loads(fp.read_text()) if fp.exists() else None
    (out_dir / "RESULTS.md").write_text(render_markdown(res, manifest, fix))


# ---- entry point -----------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.run")
    ap.add_argument("--suite", type=Path, default=SUITE_DIR)
    ap.add_argument("--regen", action="store_true", help="rebuild the suite first")
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument("--no-lean", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None, help="first N models (debug)")
    ap.add_argument("--out", type=Path, default=EVAL_DIR)
    args = ap.parse_args(argv)

    if args.regen or not (args.suite / "manifest.json").exists():
        print("building suite ...")
        build_suite(args.suite)
    manifest = load_manifest(args.suite)
    use_lean = not args.no_lean and lean_bridge.is_available()
    print(f"{len(manifest['models'])} models; lean={'on' if use_lean else 'off'}; "
          f"agent={'off' if args.no_agent else 'on'}")
    res = run_suite(manifest, args.suite, agent=not args.no_agent, lean=use_lean,
                    workers=args.workers, limit=args.limit)
    res["metrics"] = compute_metrics(res["models"])
    agent_blk = res["metrics"]["agent"]
    res["meta"] = {
        "agent_model": os.environ.get("PROOF_MODEL", DEFAULT_MODEL),
        "confidence_min": confidence_min(), "lean": use_lean,
        "agent_status": ("skipped" if agent_blk["status"] == "skipped" else
                         "ran" + ("" if os.environ.get("ANTHROPIC_API_KEY")
                                  else " (from cache only)")),
        "suite": {k: manifest[k] for k in
                  ("bug_ids", "bug_seeds", "clean_seeds", "placements")},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    write_report(args.out, args.suite)
    print(f"wrote {args.out / 'results.json'} and {args.out / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
