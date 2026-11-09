"""A5 robustness set and acceptance run: `python -m proof.spec.robustness`.

Four hand-made variants of the clean Slope workbook that the deterministic
baseline cannot (or should not) read, plus the clean workbook itself. Gold is
`rules.build_rulespec` on the clean workbook; success is assignment equality
(same matched-transaction set per line), not text equality.

  renamed_rows       Runway labels renamed ("SaaS" -> "Software & Tools", ...)
  merged_categories  Ramp's five "other" categories merged into one "Opex"
  hardcoded_row      Rent typed in as numbers, with a note instead of a formula
  reordered_columns  Ramp tab columns reordered, formulas follow the new letters

Needs a live model (ANTHROPIC_API_KEY) unless responses are already cached.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook

from proof.ingest.ledger import read_ledger
from proof.ingest.sheet import load_cellgraph, recalc_values_with_formulas
from proof.schemas import CellGraph, Ledger, RuleSpec
from proof.spec.agent import (
    AgentError, build_rulespec_llm, confidence_min, export_headers, read_row_notes, spec_accuracy,
)
from proof.spec.rules import build_rulespec
from synth.company import OTHER_RAMP_CATEGORIES, generate
from synth.workbook import LAST_ROW, ROW, write_workbook

RENAMES = {
    "Revenue": "Cash In (Stripe)", "Payroll": "People Costs",
    "SaaS": "Software & Tools", "Contractors": "Contract Labor",
    "Rent": "Office Rent", "Other": "Misc Opex", "Total Outflows": "Total Spend",
}
RAMP_COL_MOVE = {"A": "A", "B": "B", "C": "D", "D": "E", "E": "C", "F": "F"}


def _runway_formula_cells(ws):
    for row in ws.iter_rows(min_row=2, max_row=12, min_col=2, max_col=13):
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                yield c


def make_renamed_rows(src: Path, dst: Path, values: dict) -> None:
    wb = load_workbook(src)
    ws = wb["Runway"]
    for r in range(2, 12):
        label = ws.cell(r, 1).value
        ws.cell(r, 1).value = RENAMES.get(label, label)
    wb.save(dst)


def make_merged_categories(src: Path, dst: Path, values: dict) -> None:
    wb = load_workbook(src)
    ramp = wb["Ramp"]
    for row in ramp.iter_rows(min_row=2):
        if row[4].value in OTHER_RAMP_CATEGORIES:
            row[4].value = "Opex"
    ws = wb["Runway"]
    for m in range(12):
        c = ws.cell(1, 2 + m).column_letter
        rd = (f'Ramp!$B$2:$B${LAST_ROW},">="&{c}$1,Ramp!$B$2:$B${LAST_ROW},'
              f'"<"&DATE(YEAR({c}$1),MONTH({c}$1)+1,1)')
        bd = rd.replace("Ramp!$B", "Bank!$B")
        ws[f"{c}{ROW['Other']}"] = (
            f'=-(SUMIFS(Ramp!$D$2:$D${LAST_ROW},Ramp!$E$2:$E${LAST_ROW},"Opex",{rd})'
            f'+SUMIFS(Bank!$D$2:$D${LAST_ROW},Bank!$C$2:$C${LAST_ROW},"Bank fee*",{bd}))')
    wb.save(dst)


def make_hardcoded_row(src: Path, dst: Path, values: dict) -> None:
    wb = load_workbook(src)
    ws = wb["Runway"]
    r = ROW["Rent"]
    for m in range(12):
        c = ws.cell(1, 2 + m).column_letter
        ws[f"{c}{r}"] = round(values[f"{c}{r}"], 2)
    ws.cell(r, 14).value = ("Typed in from the lease schedule; paid by bank "
                            "transfer to the landlord")
    wb.save(dst)


def make_reordered_columns(src: Path, dst: Path, values: dict) -> None:
    wb = load_workbook(src)
    ramp = wb["Ramp"]
    old = [[c.value for c in row[:6]] for row in ramp.iter_rows(min_row=1)]
    for i, row in enumerate(old, start=1):
        new = [None] * 6
        for j, v in enumerate(row):
            new[ord(RAMP_COL_MOVE["ABCDEF"[j]]) - 65] = v
        for j, v in enumerate(new):
            ramp.cell(i, j + 1).value = v
    pat = re.compile(r"Ramp!\$([A-F])\$2:\$([A-F])\$%d" % LAST_ROW)
    for cell in _runway_formula_cells(wb["Runway"]):
        cell.value = pat.sub(
            lambda m: f"Ramp!${RAMP_COL_MOVE[m[1]]}$2:${RAMP_COL_MOVE[m[2]]}${LAST_ROW}",
            cell.value)
    wb.save(dst)


VARIANTS: dict[str, Callable[[Path, Path, dict], None]] = {
    "renamed_rows": make_renamed_rows,
    "merged_categories": make_merged_categories,
    "hardcoded_row": make_hardcoded_row,
    "reordered_columns": make_reordered_columns,
}


class Case:
    def __init__(self, name: str, path: Path, graph: CellGraph, ledger: Ledger):
        self.name, self.path, self.graph, self.ledger = name, path, graph, ledger


def build_cases(workdir: Path, seed: int = 7) -> tuple[RuleSpec, Ledger, list[Case]]:
    """(gold spec, gold ledger, [clean + 4 variants])."""
    workdir.mkdir(parents=True, exist_ok=True)
    clean = write_workbook(generate(seed), workdir / "slope_clean.xlsx")
    values = recalc_values_with_formulas(clean)
    clean_graph = load_cellgraph(clean, values)
    clean_ledger = read_ledger(clean)
    cases = [Case("clean", clean, clean_graph, clean_ledger)]
    for name, make in VARIANTS.items():
        p = workdir / f"slope_{name}.xlsx"
        make(clean, p, values)
        graph = load_cellgraph(p, recalc_values_with_formulas(p))
        # reordered_columns breaks the fixed-layout ledger reader; same txns anyway
        ledger = clean_ledger if name == "reordered_columns" else read_ledger(p)
        cases.append(Case(name, p, graph, ledger))
    return build_rulespec(clean_graph), clean_ledger, cases


def run_acceptance(client: Any | None, workdir: Path, seed: int = 7, *,
                   built: tuple[RuleSpec, Ledger, list[Case]] | None = None,
                   **kw: Any) -> dict[str, Any]:
    gold, gold_ledger, cases = built or build_cases(workdir, seed)
    results: dict[str, Any] = {}
    for c in cases:
        spec = build_rulespec_llm(
            c.graph, c.ledger, headers=export_headers(c.path),
            notes=read_row_notes(c.path), client=client, **kw)
        acc = spec_accuracy(spec, gold, c.ledger, gold_ledger)
        conf = {ln.line_id: ln.confidence for ln in spec.lines}
        # a miss is acceptable only if every unmatched predicted line is low-confidence
        flagged = all(conf[i] < confidence_min() for i in acc["extra_pred_lines"])
        results[c.name] = {**acc, "failures_flagged_low_confidence":
                           acc["assignment_equal"] or flagged,
                           "pred_confidence": conf}
    variants_ok = [n for n in VARIANTS if results[n]["assignment_equal"]]
    results["_summary"] = {
        "clean_matches_baseline": results["clean"]["assignment_equal"],
        "variants_matching": variants_ok,
        "variants_failing": [n for n in VARIANTS if n not in variants_ok],
        "all_failures_flagged": all(r["failures_flagged_low_confidence"]
                                    for k, r in results.items() if k != "_summary"),
        "passed": results["clean"]["assignment_equal"] and len(variants_ok) >= 3
                  and all(r["failures_flagged_low_confidence"]
                          for k, r in results.items() if k != "_summary"),
    }
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proof.spec.robustness")
    ap.add_argument("--workdir", type=Path, default=Path("data/robustness"))
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = run_acceptance(None, args.workdir, use_cache=not args.no_cache)
    except AgentError as e:
        print(f"proof.spec.robustness: {e}", file=sys.stderr)
        return 2
    for name, r in res.items():
        if name == "_summary":
            continue
        print(f"{name:18} assignment_equal={r['assignment_equal']!s:5} "
              f"accuracy={r['accuracy']:.2f} wrong={[k for k, v in r['per_line'].items() if not v]} "
              f"flagged_ok={r['failures_flagged_low_confidence']}")
    s = res["_summary"]
    print(f"\nclean matches baseline: {s['clean_matches_baseline']}")
    print(f"variants matching ({len(s['variants_matching'])}/4): {s['variants_matching']}")
    print(f"variants failing: {s['variants_failing']}")
    print(f"ACCEPTANCE: {'PASS' if s['passed'] else 'FAIL'}")
    return 0 if s["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
