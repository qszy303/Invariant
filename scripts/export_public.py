#!/usr/bin/env python3
"""Build a clean public copy of this repo: no git history, no agent instructions, no secrets.

    python scripts/export_public.py                  # writes ../Invariant-public
    python scripts/export_public.py --out /some/dir --exclude CONTRACTS.md

It copies every file git would track (tracked files plus untracked ones that are not ignored),
minus EXCLUDE, then audits the copy and exits non-zero if it finds an API key, a local home-directory
path or a personal email address. It never pushes and never commits; publishing is up to you.

First time:  cd ../Invariant-public && git init -b main && git add . && git commit -m "Initial commit"
             (then add your remote and push)
Later:       re-run with --force (the public repo's .git is kept), then in ../Invariant-public:
             git add -A && git commit -m "Update" && git push

This script is itself excluded from the public copy.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Paths (files or directory prefixes) left out of the public copy.
EXCLUDE = ["agents/", "scripts/export_public.py"]

SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}|ANTHROPIC_API_KEY\s*=\s*['\"]?[A-Za-z0-9_\-]{20,}")
HOME_PATH = re.compile(r"/Users/[A-Za-z0-9._\-]+|/home/[A-Za-z0-9._\-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._\-]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]+")
EMAIL_OK = {"noreply@anthropic.com"}


def repo_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.split("\0") if p and (ROOT / p).is_file())


def excluded(rel: str, extra: list[str]) -> bool:
    return any(rel == e or (e.endswith("/") and rel.startswith(e)) for e in EXCLUDE + extra)


def audit(out: Path, extra: list[str]) -> tuple[list[str], list[str]]:
    """(problems, warnings) found in the exported tree."""
    problems: list[str] = []
    warnings: list[str] = []
    mentions = [e for e in EXCLUDE + extra if e != "scripts/export_public.py"]
    for f in sorted(p for p in out.rglob("*") if p.is_file()):
        rel = f.relative_to(out).as_posix()
        if rel.startswith(".git/"):
            continue  # the public repo's own metadata is yours to set (see README: noreply email)
        if f.suffix == ".xlsx":
            with zipfile.ZipFile(f) as z:
                core = z.read("docProps/core.xml").decode("utf8", "replace") if "docProps/core.xml" in z.namelist() else ""
            who = re.findall(r"<(?:dc:creator|cp:lastModifiedBy)>([^<]*)<", core)
            if any(w and w != "openpyxl" for w in who):
                warnings.append(f"{rel}: workbook author metadata {who}")
            continue
        try:
            text = f.read_text(encoding="utf8")
        except (UnicodeDecodeError, OSError):
            continue
        for rx, what in ((SECRET, "API key"), (HOME_PATH, "local path")):
            for m in rx.finditer(text):
                problems.append(f"{rel}: {what}: {m.group(0)[:40]}")
        for m in EMAIL.finditer(text):
            if m.group(0) not in EMAIL_OK and not rel.endswith("uv.lock"):
                problems.append(f"{rel}: email: {m.group(0)}")
        for e in mentions:
            if e in text:
                warnings.append(f"{rel}: mentions excluded path {e!r}")
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT.parent / "Invariant-public")
    ap.add_argument("--exclude", action="append", default=[],
                    help="extra path or directory/ to leave out (repeatable)")
    ap.add_argument("--force", action="store_true",
                    help="refresh --out if it already exists (its .git, if any, is kept)")
    args = ap.parse_args(argv)
    out = args.out.resolve()
    if out == ROOT or ROOT.is_relative_to(out) or out == Path.home():
        print(f"refusing to write to {out}", file=sys.stderr)
        return 2
    if out.exists() and any(out.iterdir()):
        if not args.force:
            print(f"{out} already exists (use --force to refresh it)", file=sys.stderr)
            return 2
        # refresh the files but keep the public repo's own .git, so each export is a new commit
        for child in out.iterdir():
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    files = [p for p in repo_files() if not excluded(p, args.exclude)]
    for rel in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    skipped = len(repo_files()) - len(files)
    print(f"copied {len(files)} files to {out} (left out {skipped}: {', '.join(EXCLUDE + args.exclude)})")
    problems, warnings = audit(out, args.exclude)
    for w in warnings:
        print("warning:", w)
    for p in problems:
        print("PROBLEM:", p)
    if problems:
        print(f"\n{len(problems)} problem(s): fix them in the source repo and run again.", file=sys.stderr)
        return 1
    print("audit clean: no API keys, home-directory paths or personal emails found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
