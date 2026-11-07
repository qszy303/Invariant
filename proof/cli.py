"""Command line entry point: `proof check <checkinput.json>` (owner: A0)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from proof.schemas import CheckInput


def checkinput_from_xlsx(path: Path) -> CheckInput:
    """ingest -> rules -> assign (no LLM)."""
    from proof.assign import build_checkinput
    from proof.ingest.ledger import read_ledger
    from proof.ingest.sheet import (
        CachedValuesMissing, load_cellgraph, recalc_values_with_formulas,
    )
    from proof.spec.rules import build_rulespec

    try:
        graph = load_cellgraph(path)
    except CachedValuesMissing as first:
        try:
            values = recalc_values_with_formulas(path)
        except CachedValuesMissing:
            raise first from None
        print("proof: no cached values, recalculated with `formulas`", file=sys.stderr)
        graph = load_cellgraph(path, values)
    return build_checkinput(build_rulespec(graph), read_ledger(path), graph)


def _load_checker():
    """Return the Python checker's `check`, or None until A3 lands."""
    try:
        from proof.check.py_checker import check
    except (ImportError, AttributeError):
        return None
    return check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proof")
    sub = parser.add_subparsers(dest="command", required=True)
    check_p = sub.add_parser("check", help="check a CheckInput JSON file")
    check_p.add_argument("checkinput", type=Path,
                         help="model.xlsx, or a CheckInput JSON file")
    args = parser.parse_args(argv)

    check = _load_checker()
    if check is None:
        print("proof: checker not implemented yet (A3)", file=sys.stderr)
        return 2

    if args.checkinput.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            check_input = checkinput_from_xlsx(args.checkinput)
        except ValueError as e:
            print(f"proof: {e}", file=sys.stderr)
            return 2
    else:
        check_input = CheckInput.model_validate_json(args.checkinput.read_text())
    result = check(check_input)
    print(result.to_canonical_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
