"""xlsx -> CellGraph (owner: A2).

The workbook is loaded twice: once for formulas (`data_only=False`) and once
for cached values (`data_only=True`). Files written by openpyxl have no cached
values; open and save them once in Excel/LibreOffice, or use
`recalc_values_with_formulas` (needs the optional `formulas` package).
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from proof.schemas import Cell, CellGraph

RUNWAY = "Runway"


class CachedValuesMissing(ValueError):
    """A formula cell has no cached value (the file was never calculated)."""


# ---- formula reference parsing ---------------------------------------------

_STRING = re.compile(r'"(?:[^"]|"")*"')
_SHEET = r"(?:'(?P<q>[^']+)'|(?P<u>[A-Za-z_][\w.]*))!"
_CELL = r"\$?[A-Z]{1,3}\$?\d+"
_COL = r"\$?[A-Z]{1,3}"
_REF = re.compile(
    rf"(?<![\w.$'])(?:{_SHEET})?"
    rf"(?P<a>{_CELL}(?::{_CELL})?|{_COL}:{_COL})(?![\w(!])"
)


def _norm(a: str) -> str:
    return a.replace("$", "")


def parse_refs(formula: str, current_sheet: str = RUNWAY) -> list[str]:
    """Referenced cells/ranges as `Sheet!A1` / `Sheet!A1:B2` (no `$`).

    String literals are ignored, unqualified refs get `current_sheet`, refs are
    de-duplicated in order of first appearance. Whole-column refs (`D:D`) are
    kept as `Sheet!D:D`.
    """
    text = _STRING.sub('""', formula)
    seen: dict[str, None] = {}
    for m in _REF.finditer(text):
        sheet = m.group("q") or m.group("u") or current_sheet
        seen.setdefault(f"{sheet}!{_norm(m.group('a'))}", None)
    return list(seen)


# ---- values ----------------------------------------------------------------

def to_cents(value) -> int:
    """Dollars (int/float/str/Decimal) -> integer cents, round half even."""
    if not isinstance(value, (int, str, Decimal)):
        value = repr(float(value))  # also handles numpy floats
    d = Decimal(str(value))
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def recalc_values_with_formulas(path: str | Path) -> dict[str, float]:
    """Compute Runway values with the optional `formulas` package.

    Returns {"B2": 123.4, ...}. Slow (a few seconds) but needs no Excel.
    """
    import warnings

    try:
        import formulas
    except ImportError as e:  # pragma: no cover
        raise CachedValuesMissing(
            "no cached values and the optional `formulas` package is not installed"
        ) from e
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
    out: dict[str, float] = {}
    for k, v in sol.items():
        if f"{RUNWAY.upper()}'!" in k.upper() and ":" not in k.split("!")[-1]:
            out[k.split("!")[-1]] = v.value[0][0]
    return out


def load_cellgraph(
    path: str | Path, values: dict[str, float] | None = None
) -> CellGraph:
    """Parse the `Runway` tab into a CellGraph.

    `values` optionally supplies computed values (ref like "B5") for formula
    cells instead of the file's cached ones.
    """
    path = Path(path)
    wf = load_workbook(path, data_only=False)
    wv = load_workbook(path, data_only=True)
    if RUNWAY not in wf.sheetnames:
        raise ValueError(f"{path.name}: no {RUNWAY!r} tab (found {wf.sheetnames})")
    sf, sv = wf[RUNWAY], wv[RUNWAY]

    # Month columns: those whose row-1 header is a date; fallback B..M.
    month_cols = [
        c for c in range(2, sf.max_column + 1)
        if hasattr(sf.cell(1, c).value, "month")
    ] or list(range(2, 14))

    cells: list[Cell] = []
    missing: list[str] = []
    for r in range(2, sf.max_row + 1):
        label = sf.cell(r, 1).value
        if label is None or str(label).strip() == "":
            continue
        for idx, c in enumerate(month_cols):
            raw = sf.cell(r, c).value
            if raw is None:
                continue
            addr = f"{get_column_letter(c)}{r}"
            formula = raw if isinstance(raw, str) and raw.startswith("=") else None
            if formula is not None:
                cached = values[addr] if values and addr in values else sv.cell(r, c).value
                if cached is None:
                    missing.append(addr)
                    continue
            else:
                cached = raw
            if isinstance(cached, str):
                raise ValueError(f"{RUNWAY}!{addr}: non-numeric value {cached!r}")
            cells.append(Cell(
                ref=f"{RUNWAY}!{addr}", sheet=RUNWAY, row_label=str(label).strip(),
                month_idx=idx, value_cents=to_cents(cached), formula=formula,
                deps=parse_refs(formula, RUNWAY) if formula else [],
            ))
    if missing:
        raise CachedValuesMissing(
            f"{path.name}: {len(missing)} formula cells on {RUNWAY} have no cached "
            f"values (e.g. {missing[0]}). The file was never calculated: open it in "
            "Excel or LibreOffice and save it once, or recalculate it with "
            "`soffice --headless --convert-to xlsx`."
        )
    return CellGraph(cells=cells)
