"""Keep secrets and personal paths out of the repo (skipped outside a git checkout)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"scripts/export_public.py", "tests/test_hygiene.py"}
KEY = re.compile("sk-ant-" + r"[A-Za-z0-9_\-]{20,}")
HOME = re.compile("/Us" + r"ers/[A-Za-z0-9._\-]+|/ho" + r"me/[A-Za-z0-9._\-]+")


def _files() -> list[Path]:
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.split("\0")
            if p and p not in SKIP and (ROOT / p).is_file() and not p.endswith((".xlsx", ".lock"))]


def test_no_api_keys_or_home_paths_in_repo():
    bad = []
    for f in _files():
        try:
            text = f.read_text(encoding="utf8")
        except UnicodeDecodeError:
            continue
        for rx in (KEY, HOME):
            m = rx.search(text)
            if m:
                bad.append(f"{f.relative_to(ROOT)}: {m.group(0)[:30]}")
    assert not bad, bad


def test_env_files_are_ignored():
    gi = (ROOT / ".gitignore").read_text().split()
    assert ".env" in gi and ".proof_cache/" in gi
