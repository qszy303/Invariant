"""`python -m synth --seed 7 --out data/slope_clean.xlsx`

With `--variants`, also writes the three hand-planted bug workbooks next to
the clean one: slope.xlsx (dup_row), slope_missing_vendor.xlsx,
slope_broken_rollforward.xlsx.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from synth.company import generate
from synth.workbook import write_workbook

VARIANTS = {
    "slope.xlsx": "dup_row",
    "slope_missing_vendor.xlsx": "missing_vendor",
    "slope_broken_rollforward.xlsx": "broken_rollforward",
}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m synth")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/slope_clean.xlsx")
    ap.add_argument("--variants", action="store_true",
                    help="also write the 3 bug variants beside --out")
    args = ap.parse_args(argv)
    company = generate(args.seed)
    out = write_workbook(company, args.out)
    print(f"wrote {out}")
    if args.variants:
        for name, bug in VARIANTS.items():
            p = write_workbook(company, out.parent / name, bug)
            print(f"wrote {p} ({bug})")


if __name__ == "__main__":
    main()
