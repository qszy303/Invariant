"""Deterministic explanations of violations (owner: A7).

Plain templates over facts read straight from the workbook and the check: the
cell's formula, the matched transactions, duplicated export rows. No LLM, and
no number in an explanation is computed by anything but integer arithmetic on
the CheckInput. (An LLM polish pass is possible later; the templates already
say what a founder needs to know.)
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook

from proof.assign import _pattern
from proof.check.py_checker import MONTHS
from proof.ingest.ledger import read_ledger
from proof.ingest.sheet import (
    CachedValuesMissing, load_cellgraph, recalc_values_with_formulas,
)
from proof.assign import build_checkinput
from proof.check.py_checker import check
from proof.schemas import (
    CellGraph, CheckInput, CheckResult, Ledger, RuleSpec, Violation,
)
from proof.spec.agent import normalize_formula, read_row_notes
from proof.spec.rules import build_rulespec

EXPORT_TABS = {"Ramp": 6, "Bank": 4, "Payroll": 5}  # tab -> columns
TOP_TXNS = 10


# ---- one full pipeline run on a workbook ------------------------------------

@dataclass
class Analysis:
    path: Path
    graph: CellGraph
    ledger: Ledger
    spec: RuleSpec
    checkinput: CheckInput
    result: CheckResult
    duplicates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)


def content_hash(path: Path) -> str:
    """Hash of the workbook's parts, ignoring docProps (which hold timestamps)."""
    h = hashlib.sha1()
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if not name.startswith("docProps/"):
                h.update(name.encode())
                h.update(z.read(name))
    return h.hexdigest()


def _values_cache(path: Path) -> Path:
    d = Path(os.environ.get("PROOF_CACHE_DIR", ".proof_cache"))
    return d / "values" / f"{content_hash(path)}.json"


def load_graph(path: Path) -> CellGraph:
    """CellGraph, recalculating (slowly) only for content not seen before.

    Files written by openpyxl have no cached values, so recalculated values are
    kept under PROOF_CACHE_DIR/values by content hash. Same content, same values.
    """
    try:
        return load_cellgraph(path)
    except CachedValuesMissing:
        f = _values_cache(path)
        try:
            values = json.loads(f.read_text())
        except (OSError, ValueError):
            values = recalc_values_with_formulas(path)
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(values))
            except OSError:
                pass  # read-only cache dir: just recompute next time
        return load_cellgraph(path, values)


def analyze(path: str | Path, spec_fn: Callable[[CellGraph, Ledger, Path], RuleSpec]
            | None = None) -> Analysis:
    """workbook -> graph -> spec -> CheckInput -> CheckResult (raises on refusal)."""
    import warnings

    path = Path(path)
    graph = load_graph(path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ledger = read_ledger(path)
    spec = spec_fn(graph, ledger, path) if spec_fn else build_rulespec(graph)
    ci = build_checkinput(spec, ledger, graph)
    return Analysis(path, graph, ledger, spec, ci, check(ci),
                    duplicate_rows(path), read_row_notes(path))


def duplicate_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    """{txn id: [{sheet,row}, ...]} for ids that repeat with identical contents."""
    wb = load_workbook(path, data_only=True)
    seen: dict[str, tuple[tuple, dict]] = {}
    out: dict[str, list[dict[str, Any]]] = {}
    for tab, ncols in EXPORT_TABS.items():
        if tab not in wb.sheetnames:
            continue
        for r, row in enumerate(wb[tab].iter_rows(min_row=2, max_col=ncols,
                                                  values_only=True), start=2):
            if row[0] is None:
                continue
            key = str(row[0])
            first = seen.get(key)
            if first is None:
                seen[key] = (row, {"sheet": tab, "row": r})
            elif first[0] == row:
                out.setdefault(key, [first[1]]).append({"sheet": tab, "row": r})
    return out


# ---- formatting helpers ------------------------------------------------------

def amt(cents: int) -> str:
    cents = abs(cents)
    return f"{cents // 100:,}.{cents % 100:02d}"


def _more_less(actual: int, expected: int, by_magnitude: bool) -> str:
    big = abs(actual) > abs(expected) if by_magnitude else actual > expected
    return f"{amt(actual - expected)} {'more' if big else 'less'}"


def cell_for(an: Analysis, label: str, month: int):
    return next((c for c in an.graph.cells
                 if c.row_label == label and c.month_idx == month), None)


def label_of(an: Analysis, line_id: str | None) -> str | None:
    return next((s.row_label for s in an.spec.lines if s.line_id == line_id), None)


def violation_cell(an: Analysis, v: Violation):
    label = label_of(an, v.line_id)
    if label is None or v.month_idx is None:
        return None
    return cell_for(an, label, v.month_idx)


def vkey(v: Violation) -> tuple:
    """Identity of a violation across re-checks (matched-txn lists may change)."""
    if v.invariant in ("coverage", "no_double_count"):
        return (v.invariant, tuple(v.txn_ids))
    return (v.invariant, v.line_id, v.month_idx)


def row_cells(an: Analysis, label: str):
    return sorted((c for c in an.graph.cells if c.row_label == label),
                  key=lambda c: c.month_idx or 0)


def _txn_info(an: Analysis, tid: str) -> dict[str, Any]:
    t = next((x for x in an.ledger.transactions if x.id == tid), None)
    if t is None:
        return {"id": tid}
    return {"id": t.id, "date": t.date, "counterparty": t.counterparty,
            "category": t.category_hint, "amount_cents": t.amount_cents,
            "source": t.source}


def _odd_fragment(this: str, other: str) -> str:
    sm = difflib.SequenceMatcher(None, other, this, autojunk=False)
    parts = [this[j1:j2] for tag, _, _, j1, j2 in sm.get_opcodes()
             if tag in ("replace", "insert") and this[j1:j2].strip()]
    return " ... ".join(p[:50] for p in parts[:2])


def _formula_hint(an: Analysis, cell) -> str | None:
    """Say how this cell differs from the same row's other months, if it does."""
    peers = [c for c in row_cells(an, cell.row_label) if c.ref != cell.ref]
    pf = [c for c in peers if c.formula]
    if cell.formula is None:
        if len(pf) >= len(peers) / 2 and pf:
            return (f"{cell.ref.split('!')[-1]} is a typed number, not a formula, "
                    f"while the other months in this row are formulas")
        return None
    pats: dict[str, int] = {}
    for c in pf:
        n = normalize_formula(c.formula, c.ref)
        pats[n] = pats.get(n, 0) + 1
    if not pats:
        return None
    common = max(pats, key=pats.get)
    mine = normalize_formula(cell.formula, cell.ref)
    if mine != common and pats[common] >= 2:
        frag = _odd_fragment(mine, common)
        return (f"{cell.ref.split('!')[-1]}'s formula differs from the other months'"
                + (f" (it has: {frag})" if frag else ""))
    return None


# ---- the explanation ----------------------------------------------------------

def explain(an: Analysis, v: Violation) -> tuple[str, dict[str, Any]]:
    """(one-to-two sentence explanation, context dict) for one violation."""
    mon = MONTHS[v.month_idx] if v.month_idx is not None and 0 <= v.month_idx < 12 else "?"
    label = label_of(an, v.line_id)
    cell = violation_cell(an, v)
    ctx: dict[str, Any] = {
        "invariant": v.invariant, "row_label": label, "month": mon,
        "cell_ref": cell.ref if cell else None,
        "formula": cell.formula if cell else None,
        "sheet_value_cents": cell.value_cents if cell else None,
        "expected_cents": v.expected_cents, "actual_cents": v.actual_cents,
        "matched_txns": [], "problem_txns": [], "duplicate_rows": [],
        "row_formulas": [],
    }
    if label:
        ctx["row_formulas"] = [
            {"ref": c.ref, "formula": c.formula or f"(typed) {c.value_cents / 100:g}"}
            for c in row_cells(an, label)]
    hints: list[str] = []

    if v.invariant == "traceability":
        matched = sorted((t for t in an.checkinput.txns if t.id in set(v.txn_ids)),
                         key=lambda t: -abs(t.amount_cents))
        ctx["matched_txns"] = [_txn_info(an, t.id) for t in matched[:TOP_TXNS]]
        text = (f"{label} in {mon} ({cell.ref.split('!')[-1] if cell else '?'}) is "
                f"{_more_less(v.actual_cents, v.expected_cents, True)} than the "
                f"transactions that belong in it add up to ("
                f"{amt(v.actual_cents)} shown vs {amt(v.expected_cents)}).")
        dups = [(i, an.duplicates[i]) for i in v.txn_ids if i in an.duplicates]
        ctx["duplicate_rows"] = [{"id": i, "rows": rows} for i, rows in dups]
        if dups:
            i, rows = dups[0]
            t = _txn_info(an, i)
            hints.append(
                f"{i} ({t.get('counterparty')}, {amt(t.get('amount_cents', 0))}) appears "
                f"twice in the {rows[0]['sheet']} tab (rows "
                f"{' and '.join(str(r['row']) for r in rows)}), so the sheet counts it twice")
        if cell:
            h = _formula_hint(an, cell)
            if h:
                hints.append(h)
            elif cell.formula and v.actual_cents == 0 and v.expected_cents:
                hints.append("the cell sums to zero, so its date criteria probably skip this month")
        gap = abs(v.actual_cents - v.expected_cents)
        for t in matched:
            if gap and abs(t.amount_cents) * 2 == gap:
                hints.append(f"the gap is exactly twice {t.id}, {amt(t.amount_cents)} "
                             f"({_txn_info(an, t.id).get('counterparty')})")
                break
            if gap and abs(t.amount_cents) == gap and not dups:
                hints.append(f"the gap equals {t.id}, {amt(t.amount_cents)} "
                             f"({_txn_info(an, t.id).get('counterparty')}), counted once extra or missed")
                break
    elif v.invariant == "roll_forward":
        a, e = v.actual_cents, v.expected_cents
        if v.line_id == "start" and v.month_idx:
            prev = MONTHS[v.month_idx - 1]
            text = (f"Starting Cash in {mon} ({cell.ref.split('!')[-1] if cell else '?'}) is "
                    f"{_more_less(a, e, False)} than {prev}'s Ending Cash "
                    f"({amt(a)} vs {amt(e)}).")
            if cell and cell.formula is None:
                hints.append("it is a typed number instead of a link to the prior month's "
                             "Ending Cash")
        else:
            text = (f"Ending Cash in {mon} is {_more_less(a, e, False)} than Starting "
                    f"Cash plus that month's net flows ({amt(a)} vs {amt(e)}).")
    elif v.invariant == "arithmetic":
        text = (f"{label} in {mon} is {_more_less(v.actual_cents, v.expected_cents, False)} "
                f"than the sum of the lines it totals ({amt(v.actual_cents)} vs "
                f"{amt(v.expected_cents)}).")
        if cell:
            h = _formula_hint(an, cell)
            if h:
                hints.append(h)
    elif v.invariant == "coverage":
        t = _txn_info(an, v.txn_ids[0])
        ctx["problem_txns"] = [t]
        covered = sorted({c for s in an.spec.lines if s.rule for c in s.rule.category_in})
        text = (f"Transaction {t['id']} ({t.get('counterparty')}, "
                f"{amt(t.get('amount_cents', 0))}, {mon}) is not counted in any Runway line.")
        ctx["categories_summed"] = covered
        hints.append(f"its category {t.get('category')!r} is not one of the categories the "
                     f"model sums ({', '.join(covered)})")
    elif v.invariant == "no_double_count":
        t = _txn_info(an, v.txn_ids[0])
        ctx["problem_txns"] = [t]
        tx = next(x for x in an.checkinput.txns if x.id == v.txn_ids[0])
        lines = [label_of(an, i) or i for i in tx.assigned_line_ids]
        text = (f"Transaction {t['id']} ({t.get('counterparty')}, "
                f"{amt(t.get('amount_cents', 0))}, {mon}) is counted in "
                f"{len(lines)} lines at once: {', '.join(lines)}.")
        full = next(x for x in an.ledger.transactions if x.id == t["id"])
        for i in tx.assigned_line_ids:
            rule = next(s.rule for s in an.spec.lines if s.line_id == i)
            by_cp = any(_pattern(p).fullmatch(full.counterparty) for p in rule.counterparty_in)
            by_cat = full.category_hint is not None and any(
                _pattern(p).fullmatch(full.category_hint) for p in rule.category_in)
            hints.append(f"{label_of(an, i)} matches it by "
                         f"{'merchant name' if by_cp and not by_cat else 'category'}")
        ctx["rows_involved"] = lines
    else:  # pragma: no cover - InvariantName is closed
        text = v.message
    if hints:
        text += " Likely cause: " + "; ".join(hints[:2]) + "."
    return text, ctx
