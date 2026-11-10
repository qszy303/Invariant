"""Build the eval suite under eval/suite/ plus a manifest (owner: A6).

Defaults: 5 company seeds x 7 bug types x 3 placements = 105 buggy models, and
20 clean models from other seeds. `python -m eval.suite` rebuilds it.

The manifest (eval/suite/manifest.json) lists every model with its ground
truth. The .xlsx files are generated, deterministic in content, and not
committed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import warnings
from pathlib import Path

from openpyxl import load_workbook

from eval.mutate import BUG_IDS, mutate, runway_values
from synth.company import generate
from synth.workbook import write_workbook

EVAL_DIR = Path(__file__).resolve().parent
SUITE_DIR = EVAL_DIR / "suite"
BUG_SEEDS = tuple(range(101, 106))
CLEAN_SEEDS = tuple(range(201, 221))
PLACEMENTS = 3


def build_suite(out_dir: Path = SUITE_DIR, bug_seeds=BUG_SEEDS,
                clean_seeds=CLEAN_SEEDS, placements: int = PLACEMENTS,
                bugs=BUG_IDS) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    models: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for seed in bug_seeds:
            clean = write_workbook(generate(seed), Path(tmp) / f"clean_{seed}.xlsx")
            values = runway_values(clean)
            for bug in bugs:
                for p in range(placements):
                    wb, truth = mutate(bug, load_workbook(clean), seed, p, values)
                    rel = f"{bug}/s{seed}_p{p}.xlsx"
                    (out_dir / bug).mkdir(exist_ok=True)
                    wb.save(out_dir / rel)
                    models.append({"id": f"{bug}/s{seed}_p{p}", "path": rel,
                                   "kind": "buggy", "seed": seed, "placement": p,
                                   "truth": truth})
        for seed in clean_seeds:
            rel = f"clean/s{seed}.xlsx"
            write_workbook(generate(seed), out_dir / rel)
            models.append({"id": f"clean/s{seed}", "path": rel, "kind": "clean",
                           "seed": seed, "placement": None, "truth": None})
    manifest = {"bug_ids": list(bugs), "bug_seeds": list(bug_seeds),
                "clean_seeds": list(clean_seeds), "placements": placements,
                "models": models}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def load_manifest(out_dir: Path = SUITE_DIR) -> dict:
    return json.loads((Path(out_dir) / "manifest.json").read_text())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.suite")
    ap.add_argument("--out", type=Path, default=SUITE_DIR)
    args = ap.parse_args(argv)
    m = build_suite(args.out)
    buggy = sum(1 for x in m["models"] if x["kind"] == "buggy")
    print(f"wrote {buggy} buggy + {len(m['models']) - buggy} clean models to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
