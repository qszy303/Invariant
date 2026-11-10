"""Apply proposed fixes to a COPY, re-run the pipeline, accept only if it holds (owner: A7).

A fix is accepted iff, on the recalculated copy,
  (a) the violation it targeted is gone, and
  (b) no violation appears that was not there before.
Up to `max_attempts` proposals per violation (the first plus two more, each
told why the last one failed), then the violation is returned as "needs human".
The user's original file is never written: every attempt works on a copy under
`work_dir`, and the last accepted copy is the fixed workbook.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from pydantic import ValidationError

from proof.fix.explain import (
    EXPORT_TABS, Analysis, analyze, cell_for, explain, label_of, vkey, violation_cell,
)
from proof.fix.propose import Proposer, build_request
from proof.fix.report import Attempt, FixReport, FixRun, Proposal, Recheck
from proof.ingest.sheet import parse_refs

MAX_ATTEMPTS = 3
MAX_ROUNDS = 6
_CELL = re.compile(r"^(?:Runway!)?\$?([A-Z]{1,2})\$?(\d+)$")
_CONSTANT = re.compile(r"^=\s*[-+]?[\d.,\s]*$")
_MONTH_COLS = range(2, 14)  # B..M


class ProposalError(ValueError):
    """The proposal is malformed or not allowed; the message goes back to the model."""


def _col_letter(i: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(i)


def _check_formula(f: str | None) -> str:
    if not isinstance(f, str) or not f.startswith("="):
        raise ProposalError("new_formula must be a formula starting with '='")
    if _CONSTANT.match(f) or not parse_refs(f, "Runway"):
        raise ProposalError("new_formula must reference cells or ranges; a typed "
                            "constant is not a fix")
    if "[" in f:
        raise ProposalError("external references are not allowed")
    return f


def apply_proposal(src: Path, dst: Path, p: Proposal, an: Analysis) -> tuple[list[str], str | None]:
    """Write `p` into a copy at `dst`. Returns (cells/rows edited, old content)."""
    wb = load_workbook(src)  # formulas, not values
    if p.kind == "delete_duplicate_row":
        if p.sheet not in EXPORT_TABS or not isinstance(p.row, int):
            raise ProposalError("delete_duplicate_row needs an export tab and a row number")
        later = {(r["sheet"], r["row"]) for rows in an.duplicates.values() for r in rows[1:]}
        if (p.sheet, p.row) not in later:
            raise ProposalError(
                f"{p.sheet} row {p.row} is not a later exact repeat of an earlier row; "
                f"only duplicates may be deleted (later repeats: {sorted(later)})")
        old = ", ".join(str(c.value) for c in wb[p.sheet][p.row])
        wb[p.sheet].delete_rows(p.row)
        wb.save(dst)
        return [f"{p.sheet}!row {p.row}"], old

    m = _CELL.match((p.cell_ref or "").replace(" ", ""))
    if not m:
        raise ProposalError(f"cell_ref {p.cell_ref!r} is not a Runway cell like Runway!D5")
    letters, row = m.group(1), int(m.group(2))
    from openpyxl.utils import column_index_from_string
    col = column_index_from_string(letters)
    ws = wb["Runway"]
    if col not in _MONTH_COLS or not (2 <= row <= ws.max_row) or not ws.cell(row, 1).value:
        raise ProposalError("only a Runway month cell (columns B-M of a labelled row) can be edited")
    formula = _check_formula(p.new_formula)
    origin = f"{letters}{row}"
    old = ws[origin].value
    targets = [col]
    if p.scope == "row":
        targets = [c for c in _MONTH_COLS
                   if c == col or (isinstance(ws.cell(row, c).value, str)
                                   and ws.cell(row, c).value.startswith("="))]
    edited = []
    for c in targets:
        dest = f"{_col_letter(c)}{row}"
        ws[dest] = formula if c == col else Translator(formula, origin=origin).translate_formula(dest)
        edited.append(f"Runway!{dest}")
    wb.save(dst)
    return edited, None if old is None else str(old)


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k in Proposal.model_fields}


def _describe(vs) -> str:
    return "; ".join(v.message for v in vs[:3]) + (" ..." if len(vs) > 3 else "")


def fix_workbook(path: str | Path, proposer: Proposer, *, work_dir: str | Path | None = None,
                 out_path: str | Path | None = None,
                 spec_fn: Callable | None = None, max_attempts: int = MAX_ATTEMPTS,
                 max_rounds: int = MAX_ROUNDS) -> FixRun:
    """Explain every violation and try to fix them, never touching `path`."""
    path = Path(path)
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="proof_fix_"))
    work.mkdir(parents=True, exist_ok=True)
    cur = work / "base.xlsx"
    shutil.copyfile(path, cur)
    an = analyze(cur, spec_fn)
    calls0 = getattr(proposer, "calls", 0)

    reports: list[FixReport] = []
    for v in an.result.violations:
        text, ctx = explain(an, v)
        reports.append(FixReport(violation=v, explanation=text, context=ctx,
                                 status="needs_human"))
    index = {vkey(r.violation): i for i, r in enumerate(reports)}
    done: set[tuple] = set()      # keys of reports a verified fix has cleared
    skipped: set[tuple] = set()   # keys we gave up on

    for rnd in range(max_rounds):
        todo = [v for v in an.result.violations if vkey(v) not in skipped]
        if not todo:
            break
        target = todo[0]
        tkey = vkey(target)
        explanation, ctx = explain(an, target)
        before_keys = {vkey(v) for v in an.result.violations}
        attempts: list[Attempt] = []
        accepted: tuple[Proposal, Analysis, Path, Recheck, list[str]] | None = None
        for k in range(max_attempts):
            try:
                raw = proposer(build_request(an, target, explanation, ctx, attempts))
            except Exception as e:  # noqa: BLE001 - no key, network, bad output
                attempts.append(Attempt(outcome="invalid",
                                        detail=f"no proposal: {type(e).__name__}: {e}"))
                break
            try:
                prop = Proposal.model_validate(_clean(raw))
            except ValidationError as e:
                attempts.append(Attempt(outcome="invalid", detail=f"malformed proposal: {e}"))
                continue
            dst = work / f"r{rnd}_a{k}.xlsx"
            try:
                edited, old = apply_proposal(cur, dst, prop, an)
            except ProposalError as e:
                attempts.append(Attempt(proposal=prop, outcome="invalid", detail=str(e)))
                continue
            prop = prop.model_copy(update={"old_formula": old})
            try:
                an2 = analyze(dst, spec_fn)
            except Exception as e:  # noqa: BLE001 - the edit broke the model
                attempts.append(Attempt(proposal=prop, outcome="invalid",
                                        detail=f"the edited workbook no longer computes: "
                                               f"{type(e).__name__}: {e}"))
                continue
            after_keys = {vkey(v) for v in an2.result.violations}
            new = [v for v in an2.result.violations if vkey(v) not in before_keys]
            rec = Recheck(ok=an2.result.ok, target_cleared=tkey not in after_keys,
                          new_violations=new, remaining=len(an2.result.violations),
                          before=len(an.result.violations))
            if rec.target_cleared and not new:
                attempts.append(Attempt(proposal=prop, outcome="accepted", recheck=rec))
                accepted = (prop, an2, dst, rec, edited)
                break
            why = ("the targeted violation is still there" if not rec.target_cleared
                   else f"it introduced {len(new)} new violation(s): {_describe(new)}")
            attempts.append(Attempt(proposal=prop, outcome="rejected", detail=why, recheck=rec))
        ti = index[tkey]
        if accepted is None:
            skipped.add(tkey)
            reports[ti] = reports[ti].model_copy(update={
                "attempts": attempts,
                "note": "no verified fix after " + str(len(attempts)) + " attempt(s)"})
            continue
        prop, an2, dst, rec, edited = accepted
        after_keys = {vkey(v) for v in an2.result.violations}
        for key in before_keys - after_keys:
            i = index.get(key)
            if i is None or key in done:
                continue
            done.add(key)
            r = reports[i]
            vc = violation_cell(an, r.violation)
            ref = vc.ref if vc else (f"Runway!{prop.cell_ref.split('!')[-1]}"
                                     if prop.cell_ref else None)
            before = after = None
            if vc:
                nc = cell_for(an2, vc.row_label, vc.month_idx)
                before, after = vc.value_cents, nc.value_cents if nc else None
            reports[i] = r.model_copy(update={
                "status": "verified", "fix": prop, "recheck": rec, "cell_ref": ref,
                "before_cents": before, "after_cents": after,
                "attempts": attempts if i == ti else [],
                "resolved_by": None if i == ti else ti,
                "note": "" if i == ti else "cleared by the fix for another violation"})
        cur, an = dst, an2
        if an.result.ok:
            break

    fixed = None
    if done:
        fixed = Path(out_path) if out_path else work / "fixed.xlsx"
        fixed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cur, fixed)
    return FixRun(source=str(path), initial_ok=not reports, final_ok=an.result.ok,
                  fixed_path=str(fixed) if fixed else None,
                  final_violations=an.result.violations, reports=reports,
                  llm_calls=getattr(proposer, "calls", 0) - calls0)
