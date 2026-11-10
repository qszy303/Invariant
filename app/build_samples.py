"""Build the sample bundle: `python -m app.build_samples` (owner: A8).

Writes samples/*.xlsx (the clean Slope model and the three Phase-1 bug variants,
seed 7), then for each bug variant runs the full explain + propose + verify
loop and stores the result under samples/precomputed/<content hash>.json. The
recalculated-values cache and the LLM proposal cache go to samples/cache, so the
sample workbooks work with no network and no API key.

If samples/cache already has the proposals, no API key is needed here either;
otherwise set ANTHROPIC_API_KEY once.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from app.core import SAMPLE_FILES, SAMPLES_DIR, precomputed_path
from synth.company import generate
from synth.workbook import write_workbook

BUGS = {"slope_dup_row.xlsx": "dup_row", "slope_missing_vendor.xlsx": "missing_vendor",
        "slope_broken_rollforward.xlsx": "broken_rollforward"}


def main() -> int:
    os.environ["PROOF_CACHE_DIR"] = str(SAMPLES_DIR / "cache")
    from app.core import compute_fixrun
    from proof.fix.explain import analyze

    company = generate(7)
    SAMPLES_DIR.mkdir(exist_ok=True)
    write_workbook(company, SAMPLES_DIR / "slope_clean.xlsx")
    analyze(SAMPLES_DIR / "slope_clean.xlsx")  # warm the values cache
    for name, bug in BUGS.items():
        path = write_workbook(company, SAMPLES_DIR / name, bug)
        with tempfile.TemporaryDirectory() as tmp:
            run = compute_fixrun(path, tmp)
        if not run.final_ok:
            print(f"{name}: no verified fix (final_ok False); not stored", file=sys.stderr)
            return 1
        out = precomputed_path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # the fixed copy lived in a temp dir; the app re-derives it by applying the fix
        out.write_text(run.model_copy(update={"fixed_path": None, "source": f"samples/{name}"}).model_dump_json(indent=1))
        print(f"{name}: {len(run.reports)} violation(s), verified fix stored -> {out.name}")
    print(f"sample bundle in {SAMPLES_DIR} ({len(SAMPLE_FILES)} workbooks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
