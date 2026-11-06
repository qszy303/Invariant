# A5: LLM spec agent

Owns: `proof/spec/agent.py`, `proof/spec/prompts/`. Read `CONTRACTS.md` and `proof/spec/rules.py` first.

## Goal
Replace the deterministic baseline with an LLM that produces a `RuleSpec` from **any** reasonable runway sheet, including ones without clean SUMIFS (hardcoded values, messy labels, helper columns). This is what makes Proof work on real teams' spreadsheets.

## Do
1. Input to the model: the CellGraph (labels, formulas, deps), the export tab headers, a sample of ~30 ledger rows, and the list of distinct counterparties/categories. Don't send the full ledger.
2. Use the Anthropic SDK with tool use / structured output to return a `RuleSpec` that validates against the pydantic schema. Model name from env `PROOF_MODEL`. Retry once on validation failure with the error fed back.
3. Each line gets `confidence` and a one-sentence `rationale`. Lines below `PROOF_CONFIDENCE_MIN` (default 0.8) are marked `needs_review` in the output and surfaced to the UI, not silently checked.
4. Cache responses on disk keyed by a hash of the input (demo must work offline and deterministically).
5. Robustness set: create 4 variants of the clean workbook with A1's generator or by hand: renamed rows ("Software & Tools" vs "SaaS"), merged categories, a row with a hardcoded value + note, columns in a different order. Agent should still produce a spec equivalent to `rules.py` on the clean variants.
6. `spec_accuracy(pred, gold) -> metrics`: per-line exact match on kind + matched-txn set. Used by A6.

## Acceptance
- On `slope_clean.xlsx`, the agent's spec yields the same assignment as `rules.py` (assignment equality, not text equality).
- ≥3 of 4 robustness variants match the gold assignment; failures are flagged low-confidence rather than wrong-and-confident. Report which.

## Don't
- Let the LLM compute or compare numbers. It only says what each line **should** contain; the checker does all arithmetic.
