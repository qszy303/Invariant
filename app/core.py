"""Logic behind the Invariant web app, with no web-framework import so it can be tested (owner: A8).

check_view   workbook -> everything the page shows (check result, which checker
             ran, per-cell status, explanations)
fix lookups  precomputed FixRun for a known file, or a live one
apply_fix    apply a verified proposal to a COPY and return its path

Normal use: the model proposes fixes live (ANTHROPIC_API_KEY). The sample
workbooks in samples/ ship with their fix reports and caches, so they work
without a key. PROOF_OFFLINE=1 additionally forbids any model call: caches are
read only from samples/cache.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from proof.check import lean_bridge
from proof.fix.explain import (
    Analysis, analyze, content_hash, explain, vkey, violation_cell,
)
from proof.fix.report import FixReport, FixRun, Proposal
from proof.fix.verify import apply_proposal
from proof.schemas import CheckResult, Violation
from proof.spec.agent import confidence_min

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "samples"
SAMPLE_FILES = {  # file name -> label shown in the app
    "slope_clean.xlsx": "Clean model (no bugs)",
    "slope_dup_row.xlsx": "A Ramp row pasted twice",
    "slope_missing_vendor.xlsx": "A new vendor nobody counted",
    "slope_broken_rollforward.xlsx": "Starting cash typed over the link",
}
N_CHECKS = 5  # roll_forward, arithmetic, traceability, no_double_count, coverage
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def offline() -> bool:
    return os.environ.get("PROOF_OFFLINE") == "1"


def setup_cache_env() -> None:
    """Point the value/proposal caches somewhere stable and seed the sample caches."""
    if offline():
        os.environ["PROOF_CACHE_DIR"] = str(SAMPLES_DIR / "cache")
        return
    d = Path(os.environ.setdefault("PROOF_CACHE_DIR", str(ROOT / ".proof_cache")))
    if (SAMPLES_DIR / "cache").is_dir():  # makes the sample workbooks instant
        shutil.copytree(SAMPLES_DIR / "cache", d, dirs_exist_ok=True)


def has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def cache_dir() -> Path:
    return Path(os.environ.get("PROOF_CACHE_DIR", ".proof_cache"))


def dollars(cents: int | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


# ---- the check ---------------------------------------------------------------

@dataclass
class View:
    analysis: Analysis
    checker: str                      # "Lean" or "Python"
    lean_agrees: bool | None          # None when Lean is not built
    seconds: float
    violations: list[Violation]
    explanations: list[tuple[str, dict[str, Any]]]
    status: dict[tuple[str, int], str]   # (row label, month) -> ok | violated | review
    review_lines: list[str] = field(default_factory=list)
    n_checks: int = N_CHECKS


def cell_status(an: Analysis, violations: list[Violation]) -> tuple[dict, list[str]]:
    bad = set()
    for v in violations:
        c = violation_cell(an, v)
        if c is not None:
            bad.add((c.row_label, c.month_idx))
    low = {s.row_label for s in an.spec.lines if s.confidence < confidence_min()}
    status = {}
    for c in an.graph.cells:
        key = (c.row_label, c.month_idx)
        status[key] = ("violated" if key in bad else
                       "review" if c.row_label in low else "ok")
    return status, sorted(low)


def check_view(path: str | Path, spec_fn: Callable | None = None) -> View:
    t0 = time.perf_counter()
    an = analyze(path, spec_fn)
    violations = an.result.violations
    lean_agrees = None
    checker = "Python"
    if lean_bridge.is_available():
        raw = lean_bridge.check_json(an.checkinput)
        lean_agrees = raw == an.result.to_canonical_json()
        checker = "Lean"
    status, low = cell_status(an, violations)
    return View(an, checker, lean_agrees, round(time.perf_counter() - t0, 2), violations,
                [explain(an, v) for v in violations], status, low)


def grid(view: View) -> tuple[list[str], list[list[float | None]], list[list[str]]]:
    """(row labels, sheet values in dollars by month, status by month) for the Runway."""
    labels = []
    for c in view.analysis.graph.cells:
        if c.row_label not in labels:
            labels.append(c.row_label)
    values: list[list[float | None]] = []
    marks: list[list[str]] = []
    by = {(c.row_label, c.month_idx): c for c in view.analysis.graph.cells}
    for label in labels:
        values.append([by[(label, m)].value_cents / 100 if (label, m) in by else None
                       for m in range(12)])
        marks.append([view.status.get((label, m), "ok") for m in range(12)])
    return labels, values, marks


def violation_label(view: View, i: int) -> str:
    v = view.violations[i]
    c = violation_cell(view.analysis, v)
    if c is not None:
        return f"{c.row_label} · {MONTHS[c.month_idx]} — {v.invariant.replace('_', ' ')}"
    mon = MONTHS[v.month_idx] if v.month_idx is not None else ""
    ids = ", ".join(v.txn_ids[:1])
    return f"{ids} · {mon} — {v.invariant.replace('_', ' ')}"


def violation_for_cell(view: View, label: str, month: int) -> int | None:
    for i, v in enumerate(view.violations):
        c = violation_cell(view.analysis, v)
        if c is not None and (c.row_label, c.month_idx) == (label, month):
            return i
    return None


# ---- fixes -----------------------------------------------------------------

def precomputed_path(path: str | Path) -> Path:
    return SAMPLES_DIR / "precomputed" / f"{content_hash(Path(path))}.json"


def load_fixrun(path: str | Path) -> FixRun | None:
    """Precomputed fix reports for exactly this workbook content, if shipped."""
    p = precomputed_path(path)
    return FixRun.model_validate_json(p.read_text()) if p.exists() else None


def compute_fixrun(path: str | Path, workdir: str | Path) -> FixRun:
    """Live (or cache-served) explain + propose + verify on copies."""
    from proof.fix.propose import LLMProposer
    from proof.fix.verify import fix_workbook

    return fix_workbook(path, LLMProposer(cache_dir=cache_dir()), work_dir=workdir)


def report_for(fixrun: FixRun | None, v: Violation) -> FixReport | None:
    if fixrun is None:
        return None
    return next((r for r in fixrun.reports if vkey(r.violation) == vkey(v)), None)


def apply_fix(view: View, proposal: Proposal, dst: str | Path) -> Path:
    """Apply `proposal` to a copy of the current workbook at `dst`; the source is untouched."""
    dst = Path(dst)
    apply_proposal(view.analysis.path, dst, proposal, view.analysis)
    return dst


def fix_text(p: Proposal) -> tuple[str, str]:
    """(old, new) display text for a proposal."""
    if p.kind == "delete_duplicate_row":
        return (f"{p.sheet} row {p.row} (an exact repeat of an earlier row)", "(row deleted)")
    where = f"{p.cell_ref}" + (" and the rest of its row" if p.scope == "row" else "")
    return (f"{where}: {p.old_formula}", p.new_formula or "")


def unique_fixes(fixrun: FixRun | None) -> list[Proposal]:
    """Verified fixes in the order they were verified (siblings share one fix)."""
    if fixrun is None:
        return []
    return [r.fix for r in fixrun.reports
            if r.status == "verified" and r.fix is not None and r.resolved_by is None]


def apply_sequence(path: str | Path, proposals: list[Proposal], workdir: str | Path,
                   start: int) -> tuple[Path, int]:
    """Apply fixes one after another to successive copies; returns (last copy, count)."""
    cur = Path(path)
    for k, p in enumerate(proposals):
        nxt = Path(workdir) / f"v{start + k + 1}.xlsx"
        apply_proposal(cur, nxt, p, analyze(cur))
        cur = nxt
    return cur, len(proposals)


def key_missing(run: FixRun | None) -> bool:
    """True if a fix run produced nothing only because there is no API key."""
    return bool(run) and any("ANTHROPIC_API_KEY" in a.detail
                             for r in run.reports for a in r.attempts)


def lean_facts() -> dict[str, Any]:
    """What the Lean proofs cover, read from lean/README.md and Spec.lean."""
    import re

    out: dict[str, Any] = {"proven": [], "not_proven": [], "definition": "", "theorem": ""}
    try:
        md = (ROOT / "lean" / "README.md").read_text()
        for key, title in (("proven", "What is proven"), ("not_proven", "What is NOT proven")):
            part = next(p for p in re.split(r"^## ", md, flags=re.M) if p.startswith(title))
            body = part.split("\n", 1)[1]
            items, cur = [], []
            for line in body.splitlines():
                if line.startswith("- "):
                    if cur:
                        items.append(" ".join(cur))
                    cur = [line[2:].strip()]
                elif line.strip() and cur:
                    cur.append(line.strip())
                elif line.strip() and not cur:
                    items.append(line.strip())
            if cur:
                items.append(" ".join(cur))
            out[key] = items
    except (OSError, StopIteration):
        pass
    try:
        src = (ROOT / "lean" / "ProofCheck" / "Spec.lean").read_text()
        out["definition"] = re.search(r"def TraceabilityHolds.*?\n\n", src, re.S).group(0).strip()
        out["theorem"] = re.search(r"theorem checkTraceability_iff.*?:= by", src, re.S).group(0).strip()
    except (OSError, AttributeError):
        pass
    return out


def violation_cell_pos(view: View, v: Violation) -> dict[str, Any] | None:
    """{"row": label, "month": idx} of the Runway cell a violation sits on, if any."""
    c = violation_cell(view.analysis, v)
    return None if c is None else {"row": c.row_label, "month": c.month_idx}
