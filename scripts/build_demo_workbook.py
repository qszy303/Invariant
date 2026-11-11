"""Build the Fernway demo workbooks (a larger company with one planted bug).

    python -m scripts.build_demo_workbook [--out samples]

Writes `fernway_dropped_month.xlsx`. The August SaaS cell (Runway!I5) has its SUMIFS date range start at the
next month, so the month sums to zero. This is the same edit as
`eval/mutate.py:dropped_month`, at a fixed cell so the demo story is stable.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from synth.company import generate
from synth.fernway import FERNWAY
from synth.workbook import write_workbook

SEED = 9  # seeds 7, 8 and 11 made GPT-4.1 misread the Other row (it drops bank fees) in the spec or the fix re-check; 9 passes both
BUG_CELL = "I5"  # Runway, August SaaS


def build(out_dir: Path, seed: int = SEED) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    company = generate(seed, FERNWAY)
    with tempfile.TemporaryDirectory() as tmp:
        clean = write_workbook(company, Path(tmp) / "fernway_clean.xlsx")
        wb = load_workbook(clean)
    ws = wb["Runway"]
    old = '">="&I$1'
    formula = ws[BUG_CELL].value
    assert formula.count(old) == 1, formula
    ws[BUG_CELL] = formula.replace(
        old, '">="&DATE(YEAR(I$1),MONTH(I$1)+1,1)')
    bugged = out_dir / "fernway_dropped_month.xlsx"
    wb.save(bugged)
    return bugged


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m scripts.build_demo_workbook")
    ap.add_argument("--out", default="samples")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    print(f"wrote {build(Path(args.out), args.seed)}")


if __name__ == "__main__":
    main()
