# A6: Mutation generator + eval

Owns: `eval/`. Read `CONTRACTS.md` (bug taxonomy) first.

## Goal
Honest numbers for the slide: how many planted bugs Proof catches, how often it cries wolf, and how accurate the spec agent is.

## Do
1. `eval/mutate.py`: one function per bug id in the taxonomy. Each takes a clean workbook (openpyxl) + RNG and returns `(mutated_workbook, ground_truth)` where ground truth = `{bug_id, cells_affected, expected_invariants}`. Mutations must edit the **workbook** (formulas/rows), not the CheckInput, so the whole pipeline is tested. Recalculate after mutating (same method as A1).
2. Generate a suite: 5 seeds × 7 bug types × 3 placements = 105 buggy models, plus 20 clean models (different seeds). Save under `eval/suite/` with a manifest.
3. `eval/run.py`: run the full pipeline with **both** spec sources (`rules` baseline and `agent`) and both checkers (Python, and Lean if available). Record per model: detected?, correct invariant?, correct cell localized?, false positives, runtime.
4. Metrics: detection rate by bug type, localization accuracy, false-positive rate on clean models, spec-agent accuracy (via A5's `spec_accuracy`), Python/Lean agreement rate. Output `eval/results.json` and a markdown table `eval/RESULTS.md`.
5. `make eval` runs it end to end; use A5's cache so reruns are cheap.

## Acceptance
- Numbers are reproducible from a clean checkout with `make eval`.
- RESULTS.md includes a "known misses" section listing every undetected bug and why. Don't hide them; a candid limitations slide reads as more credible to engineers.

## Don't
- Tune the checker or the mutations to make numbers look better. If a mutation is genuinely undetectable by design (e.g., a wrong number that's consistent everywhere), keep it and label it as out of scope.
