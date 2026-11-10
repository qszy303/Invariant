# A7: Explain + fix + re-verify loop

Owns: `proof/fix/`. Read `CONTRACTS.md`, the checker, and A5's agent first.

## Goal
Turn each violation into something a founder understands and a fix that is **checked before it's offered**.

## Do
1. `fix/explain.py`: for each violation, gather context: the cell's formula, row label, month, the matched txns (top 10 by amount), and any txns causing overlap/gap. Produce a one-to-two sentence explanation. Use deterministic templates first; LLM only to polish wording (optional, cached).
2. `fix/propose.py`: LLM proposes a corrected formula (or a corrected rule for coverage/overlap cases) as structured output `{cell_ref, old_formula, new_formula, reason}`.
3. `fix/verify.py`: apply the proposed formula to a **copy** of the workbook, recalculate, re-run the full pipeline, and accept the fix only if (a) the target violation is gone and (b) no new violations appear. Otherwise try up to 2 more proposals, then return "needs human".
4. Return a `FixReport` per violation: explanation, accepted fix (if any), before/after values, and the re-check result. The UI shows "verified fix" only when the re-check passed.

## Acceptance
- On the 3 Phase-1 bug workbooks, a verified fix is produced for each and the fixed workbook checks `ok: true`.
- On A6's suite, report fix success rate by bug type (add to RESULTS.md).

## Don't
- Write fixes into the user's original file. Always a copy.
