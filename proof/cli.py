"""Command line entry point: `proof check <checkinput.json>` (owner: A0)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from proof.schemas import CheckInput


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
    check_p.add_argument("checkinput", type=Path)
    args = parser.parse_args(argv)

    check = _load_checker()
    if check is None:
        print("proof: checker not implemented yet (A3)", file=sys.stderr)
        return 2

    check_input = CheckInput.model_validate_json(args.checkinput.read_text())
    result = check(check_input)
    print(result.to_canonical_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
