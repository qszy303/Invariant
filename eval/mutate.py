"""Bug injectors, one per id in the CONTRACTS.md taxonomy (owner: A6).

Every mutator edits the *workbook* (a Runway formula, a typed cell, or an
export row), never a CheckInput, so the whole pipeline is what gets tested.

    mutator(wb, rng, values, placement) -> (wb, truth)

* `wb`        openpyxl workbook of a clean Slope model (mutated in place).
* `rng`       random.Random; use `make_rng(seed, bug_id, placement)`.
* `values`    Runway values of the clean model in dollars, e.g. {"D5": 4123.0}
              (see `runway_values`). Needed to pick non-trivial cells and to
              size a stale number; the mutation itself stays a workbook edit.
* `placement` 0, 1 or 2: three different places for the same bug, so one
              seed does not test one lucky spot.

`truth` is the ground truth the scorer uses:
    bug_id              taxonomy id
    cells_affected      workbook cells/rows that were edited
    expected_invariants invariants the taxonomy says should fire (any one counts)
    runway_cells        Runway cells where the symptom should show (may be empty)
    txn_ids             ledger ids a correct violation should point at (may be empty)
    note                one line saying what was done

Recalculation: files saved by openpyxl carry no cached values, so after
mutating, the pipeline recalculates with the `formulas` package (same method
A1's tests use); `runway_values` does the same for the clean model.

Known limit, by design: edits that make the export tabs and the Runway agree
(e.g. flipping a refund's sign in Ramp itself) are undetectable, because the
Ledger *is* the export tabs. `sign_flip` is therefore injected in the Runway
formula, where a model author would actually make that mistake.
"""

from __future__ import annotations

import random
from copy import copy
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from synth.workbook import LAST_ROW, ROW

BUG_IDS = (
    "dup_row", "dropped_month", "missing_vendor", "overlap_category",
    "sign_flip", "stale_hardcode", "broken_rollforward",
)
FLOW_LINES = ("SaaS", "Contractors", "Other")  # rows fed by Ramp categories
NEW_VENDORS = ("Airtable", "Supabase", "Postman", "Webflow", "Tailscale")
UNCOVERED_CATEGORY = "Tools"  # no Runway row sums this Ramp category

Truth = dict[str, Any]


def make_rng(seed: int, bug_id: str, placement: int) -> random.Random:
    return random.Random(f"{seed}:{bug_id}:{placement}")  # str seeds are stable


def col(month_idx: int) -> str:
    return get_column_letter(2 + month_idx)


def cell_ref(label: str, month_idx: int) -> str:
    return f"{col(month_idx)}{ROW[label]}"


def runway_values(path) -> dict[str, float]:
    """Recalculated Runway values (dollars) of a saved workbook."""
    from proof.ingest.sheet import recalc_values_with_formulas

    return recalc_values_with_formulas(path)


def line_for_category(category: str) -> str:
    if category == "Software":
        return "SaaS"
    if category == "Contractors":
        return "Contractors"
    return "Other"


def _ramp_rows(wb: Workbook) -> list[dict[str, Any]]:
    ws = wb["Ramp"]
    out = []
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value is None:
            continue
        out.append({
            "row": r, "id": ws.cell(r, 1).value, "date": ws.cell(r, 2).value,
            "merchant": ws.cell(r, 3).value, "amount": ws.cell(r, 4).value,
            "category": ws.cell(r, 5).value,
        })
    return out


def _ramp_dates(c: str) -> str:
    return (f'Ramp!$B$2:$B${LAST_ROW},">="&{c}$1,'
            f'Ramp!$B$2:$B${LAST_ROW},"<"&DATE(YEAR({c}$1),MONTH({c}$1)+1,1)')


def _vendor_sumifs(vendor: str, c: str) -> str:
    return (f'SUMIFS(Ramp!$D$2:$D${LAST_ROW},Ramp!$C$2:$C${LAST_ROW},'
            f'"{vendor}",{_ramp_dates(c)})')


def _truth(bug_id: str, *, cells, invariants, runway_cells=(), txn_ids=(),
           note: str) -> Truth:
    return {"bug_id": bug_id, "cells_affected": list(cells),
            "expected_invariants": list(invariants),
            "runway_cells": list(runway_cells), "txn_ids": list(txn_ids),
            "note": note}


# ---- the seven mutators -----------------------------------------------------

def dup_row(wb, rng, values, placement):
    """A Ramp expense row is pasted twice (same id, same contents)."""
    line = FLOW_LINES[placement % 3]
    cands = [r for r in _ramp_rows(wb)
             if r["amount"] < 0 and line_for_category(r["category"]) == line]
    v = rng.choice(cands)
    ws = wb["Ramp"]
    ws.insert_rows(v["row"] + 1)
    for c in range(1, 7):
        src, dst = ws.cell(v["row"], c), ws.cell(v["row"] + 1, c)
        dst.value = src.value
        dst._style = copy(src._style)
    m = v["date"].month - 1
    return wb, _truth(
        "dup_row", cells=[f"Ramp!A{v['row'] + 1}:F{v['row'] + 1}"],
        invariants=["traceability"], runway_cells=[f"Runway!{cell_ref(line, m)}"],
        txn_ids=[v["id"]],
        note=f"{v['id']} ({v['merchant']}) pasted twice; {line} counts it twice")


def dropped_month(wb, rng, values, placement):
    """One cell's SUMIFS lower date bound is the next month, so it sums nothing."""
    line = FLOW_LINES[placement % 3]
    months = [m for m in range(12)
              if abs(values.get(cell_ref(line, m), 0)) > 0]
    m = rng.choice(months)
    ref = cell_ref(line, m)
    c = col(m)
    ws = wb["Runway"]
    old = f'">="&{c}$1'
    assert old in ws[ref].value, ws[ref].value
    ws[ref] = ws[ref].value.replace(old, f'">="&DATE(YEAR({c}$1),MONTH({c}$1)+1,1)')
    return wb, _truth(
        "dropped_month", cells=[f"Runway!{ref}"], invariants=["traceability"],
        runway_cells=[f"Runway!{ref}"],
        note=f"{line} {ref}: date range starts at the next month, so the month sums to 0")


def missing_vendor(wb, rng, values, placement):
    """A new vendor shows up in Ramp under a category no SUMIFS criterion lists."""
    vendor = rng.choice(NEW_VENDORS)
    start = (1, 5, 9)[placement % 3] + rng.randint(0, 1)
    amount = -float(rng.randrange(25_000, 90_000, 100)) / 100
    ws = wb["Ramp"]
    template = [ws.cell(2, c) for c in range(1, 7)]
    ids = []
    for k, m in enumerate(range(start, 12)):
        r = ws.max_row + 1
        rid = f"ramp-x{placement}{k:03d}"
        ids.append(rid)
        vals = [rid, template[1].value.replace(month=m + 1, day=12), vendor,
                amount, UNCOVERED_CATEGORY, "monthly subscription"]
        for c, v in enumerate(vals, 1):
            ws.cell(r, c).value = v
            ws.cell(r, c)._style = copy(template[c - 1]._style)
    first = ws.max_row - len(ids) + 1
    return wb, _truth(
        "missing_vendor", cells=[f"Ramp!A{first}:F{ws.max_row}"],
        invariants=["coverage"], txn_ids=ids,
        note=f"{vendor} charged monthly from {start + 1:02d}/2026 as category "
             f"{UNCOVERED_CATEGORY!r}, which no Runway row sums")


def overlap_category(wb, rng, values, placement):
    """A row also sums, by vendor, a vendor that another row already owns by category."""
    target, source_cat = [("Contractors", "Software"), ("SaaS", "Contractors"),
                          ("Other", "Software")][placement % 3]
    rows = _ramp_rows(wb)
    vendors = sorted({r["merchant"] for r in rows if r["category"] == source_cat})
    vendor = rng.choice(vendors)
    ws = wb["Runway"]
    cells = []
    for m in range(12):
        ref = cell_ref(target, m)
        ws[ref] = f"{ws[ref].value}-{_vendor_sumifs(vendor, col(m))}"
        cells.append(f"Runway!{ref}")
    ids = [r["id"] for r in rows if r["merchant"] == vendor]
    return wb, _truth(
        "overlap_category", cells=cells,
        invariants=["no_double_count", "traceability"], txn_ids=ids,
        note=f"{target} row also sums {vendor} ({source_cat}) by merchant, so "
             f"{vendor} lands in two lines")


def sign_flip(wb, rng, values, placement):
    """A refund is counted as spend: the cell adds the refund back twice."""
    refunds = sorted((r for r in _ramp_rows(wb) if r["amount"] > 0),
                     key=lambda r: (r["date"], r["id"]))
    v = refunds[placement % len(refunds)]
    line = line_for_category(v["category"])
    m = v["date"].month - 1
    ref = cell_ref(line, m)
    ws = wb["Runway"]
    ws[ref] = f"{ws[ref].value}+2*{v['amount']!r}"
    return wb, _truth(
        "sign_flip", cells=[f"Runway!{ref}"], invariants=["traceability"],
        runway_cells=[f"Runway!{ref}"], txn_ids=[v["id"]],
        note=f"{line} {ref}: refund {v['id']} ({v['merchant']}, {v['amount']}) is "
             f"added as spend instead of netted out")


def stale_hardcode(wb, rng, values, placement):
    """A flow cell's formula is overwritten with a typed number from an old snapshot."""
    line = FLOW_LINES[placement % 3]
    months = [m for m in range(12) if values.get(cell_ref(line, m), 0) > 1000]
    m = rng.choice(months)
    ref = cell_ref(line, m)
    clean = values[ref]
    typed = round(clean * (1 - rng.uniform(0.02, 0.08)))
    if typed == round(clean):
        typed -= 100
    wb["Runway"][ref] = typed
    return wb, _truth(
        "stale_hardcode", cells=[f"Runway!{ref}"], invariants=["traceability"],
        runway_cells=[f"Runway!{ref}"],
        note=f"{line} {ref}: formula replaced by typed {typed} (live value {clean:,.2f})")


def broken_rollforward(wb, rng, values, placement):
    """A later month's Starting Cash is typed over the link to prior Ending Cash."""
    m = rng.randint(*[(1, 3), (4, 7), (8, 11)][placement % 3])
    end_prev = values[cell_ref("Ending Cash", m - 1)]
    offset = rng.choice([-1, 1]) * rng.randrange(500, 20_000)
    ref = cell_ref("Starting Cash", m)
    wb["Runway"][ref] = round(end_prev) + offset
    return wb, _truth(
        "broken_rollforward", cells=[f"Runway!{ref}"], invariants=["roll_forward"],
        runway_cells=[f"Runway!{ref}"],
        note=f"{ref}: Starting Cash typed as {round(end_prev) + offset} instead of "
             f"linking to prior Ending Cash ({offset:+d} off)")


MUTATORS: dict[str, Callable[..., tuple[Workbook, Truth]]] = {
    "dup_row": dup_row, "dropped_month": dropped_month,
    "missing_vendor": missing_vendor, "overlap_category": overlap_category,
    "sign_flip": sign_flip, "stale_hardcode": stale_hardcode,
    "broken_rollforward": broken_rollforward,
}
assert tuple(MUTATORS) == BUG_IDS


def mutate(bug_id: str, wb: Workbook, seed: int, placement: int,
           values: dict[str, float]) -> tuple[Workbook, Truth]:
    return MUTATORS[bug_id](wb, make_rng(seed, bug_id, placement), values, placement)
