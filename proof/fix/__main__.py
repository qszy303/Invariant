"""`python -m proof.fix model.xlsx [--out fixed.xlsx] [--json]`"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from proof.fix.propose import LLMProposer
from proof.fix.verify import fix_workbook
from proof.spec.agent import AgentError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proof.fix")
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="where to write the fixed copy")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        run = fix_workbook(args.workbook, LLMProposer(), out_path=args.out)
    except (AgentError, ValueError) as e:
        print(f"proof.fix: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(run.model_dump_json(indent=1))
    else:
        for i, r in enumerate(run.reports, 1):
            print(f"{i}. [{r.status}] {r.explanation}")
            if r.fix:
                f = r.fix
                if f.kind == "delete_duplicate_row":
                    print(f"     fix: delete {f.sheet} row {f.row}: {f.reason}")
                else:
                    print(f"     fix ({f.scope}) {f.cell_ref}: {f.old_formula} -> {f.new_formula}")
                    print(f"          {f.reason}")
        print(f"\nfixed workbook {'checks ok' if run.final_ok else 'still has violations'}"
              + (f": {run.fixed_path}" if run.fixed_path else ""))
    return 0 if run.final_ok else 1


if __name__ == "__main__":
    sys.exit(main())
