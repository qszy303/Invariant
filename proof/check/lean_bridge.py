"""CheckInput -> CheckResult via the Lean binary (owner: A4)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from proof.schemas import CheckInput, CheckResult

LEAN_DIR = Path(__file__).resolve().parents[2] / "lean"
BINARY = LEAN_DIR / ".lake" / "build" / "bin" / "proofcheck"


class LeanUnavailable(RuntimeError):
    """The Lean checker binary has not been built (run `make lean`)."""


def find_binary() -> Path:
    """Locate the binary: $PROOFCHECK_BIN, else lean/.lake/build/bin/proofcheck."""
    env = os.environ.get("PROOFCHECK_BIN")
    path = Path(env) if env else BINARY
    if not path.is_file():
        raise LeanUnavailable(f"Lean checker not built at {path}; run `make lean`")
    return path


def is_available() -> bool:
    try:
        find_binary()
    except LeanUnavailable:
        return False
    return True


def check_json(inp: CheckInput) -> str:
    """Raw canonical CheckResult JSON emitted by the Lean binary (no trailing newline)."""
    proc = subprocess.run(
        [str(find_binary())],
        input=inp.model_dump_json(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"proofcheck failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def check(inp: CheckInput) -> CheckResult:
    return CheckResult.model_validate_json(check_json(inp))
