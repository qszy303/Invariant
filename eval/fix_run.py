"""Fix success rate on the eval suite (owner: A6, uses A7's loop).

    uv run python -m eval.fix_run [--limit N] [--workers 4]

For every buggy model: explain + propose + verify on copies (never the suite
file), with the live proposer (cached by input hash in PROOF_CACHE_DIR).
Per model it records
  fixed          the fixed copy re-checks `ok` (the loop's own acceptance test)
  restored_clean the fixed copy recomputes to exactly the clean model's Runway
                 values: an independent check the loop does not see. Not
                 applicable to missing_vendor, whose correct fix adds a
                 category the clean model never had.
Writes eval/fix_results.json and refreshes eval/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from eval.run import CACHE_DIR, graph_for, write_report
from eval.suite import EVAL_DIR, SUITE_DIR, load_manifest

WORK = CACHE_DIR / "fix_work"
NOT_COMPARABLE = {"missing_vendor"}


def _values(graph) -> dict[str, int]:
    return {c.ref: c.value_cents for c in graph.cells}


def one(args: tuple[str, str, dict]) -> dict[str, Any]:
    """Worker: fix one suite model. Top-level so it can be sent to a process."""
    suite, model_id, entry = args
    import warnings

    from proof.fix.propose import LLMProposer
    from proof.fix.verify import fix_workbook
    from synth.company import generate
    from synth.workbook import write_workbook

    warnings.simplefilter("ignore")
    path = Path(suite) / entry["path"]
    work = WORK / model_id.replace("/", "__")
    shutil.rmtree(work, ignore_errors=True)
    out: dict[str, Any] = {"id": model_id, "bug_id": entry["truth"]["bug_id"]}
    t0 = time.perf_counter()
    try:
        proposer = LLMProposer()
        run = fix_workbook(path, proposer, work_dir=work)
    except Exception as e:  # noqa: BLE001 - recorded, not hidden
        out.update(status="error", error=f"{type(e).__name__}: {e}")
        return out
    out["seconds"] = round(time.perf_counter() - t0, 1)
    firsts = [r for r in run.reports if r.resolved_by is None]
    details = " ".join(a.detail for r in run.reports for a in r.attempts)
    if "ANTHROPIC_API_KEY" in details:
        out.update(status="skipped", error="no API key and no cached proposals")
        shutil.rmtree(work, ignore_errors=True)
        return out
    broke = [a.detail for r in run.reports for a in r.attempts
             if a.detail.startswith("no proposal:")]
    if broke:  # the proposer itself failed (billing, network, ...): not a fix verdict
        out.update(status="error", error=broke[0][:300])
        shutil.rmtree(work, ignore_errors=True)
        return out
    attempts = [len(r.attempts) for r in firsts if r.attempts]
    out.update(
        status="ok", violations=len(run.reports), fixed=run.final_ok,
        verified=sum(1 for r in run.reports if r.status == "verified"),
        needs_human=sum(1 for r in run.reports if r.status == "needs_human"),
        attempts=sum(attempts), llm_calls=run.llm_calls,
        first_try=bool(attempts) and all(
            r.attempts[0].outcome == "accepted" for r in firsts
            if r.attempts and r.status == "verified"),
        fixes=[r.fix.model_dump(mode="json") for r in firsts if r.fix])
    out["restored_clean"] = None
    if run.fixed_path and entry["truth"]["bug_id"] not in NOT_COMPARABLE:
        clean = write_workbook(generate(entry["seed"]), work / "clean.xlsx")
        out["restored_clean"] = _values(graph_for(Path(run.fixed_path))) == _values(graph_for(clean))
    shutil.rmtree(work, ignore_errors=True)
    return out


def _rate(n, d):
    return round(n / d, 4) if d else None


def summarize(rows: list[dict], bug_ids: list[str]) -> dict[str, Any]:
    def block(rs: list[dict]) -> dict[str, Any]:
        ok = [r for r in rs if r["status"] == "ok"]
        comp = [r for r in ok if r["restored_clean"] is not None]
        return {
            "n": len(rs), "ran": len(ok),
            "errors": sum(1 for r in rs if r["status"] == "error"),
            "skipped": sum(1 for r in rs if r["status"] == "skipped"),
            "fixed": sum(1 for r in ok if r["fixed"]),
            "fix_rate": _rate(sum(1 for r in ok if r["fixed"]), len(ok)),
            "first_try": sum(1 for r in ok if r["first_try"]),
            "needs_human": sum(r["needs_human"] for r in ok),
            "restored_clean": sum(1 for r in comp if r["restored_clean"]),
            "restored_clean_of": len(comp),
            "mean_proposals": round(statistics.mean(r["attempts"] for r in ok), 2) if ok else None,
        }
    return {"overall": block(rows),
            "by_bug": {b: block([r for r in rows if r["bug_id"] == b]) for b in bug_ids}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.fix_run")
    ap.add_argument("--suite", type=Path, default=SUITE_DIR)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=EVAL_DIR)
    args = ap.parse_args(argv)
    manifest = load_manifest(args.suite)
    models = [m for m in manifest["models"] if m["kind"] == "buggy"]
    if args.limit:
        models = models[:: max(1, len(models) // args.limit)][: args.limit]
    print(f"fixing {len(models)} buggy models ({args.workers} workers)")
    jobs = [(str(args.suite), m["id"], m) for m in models]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, r in enumerate(pool.map(one, jobs), 1):
            rows.append(r)
            if i % 10 == 0:
                print(f"  {i}/{len(jobs)}")
    res = {"models": rows, "metrics": summarize(rows, manifest["bug_ids"]),
           "meta": {"proposer_model": os.environ.get("PROOF_MODEL", "claude-sonnet-5-5")}}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "fix_results.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    write_report(args.out, args.suite)
    o = res["metrics"]["overall"]
    print(f"fixed {o['fixed']}/{o['n']}; restored clean values {o['restored_clean']}/"
          f"{o['restored_clean_of']}; errors {o['errors']}; skipped {o['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
