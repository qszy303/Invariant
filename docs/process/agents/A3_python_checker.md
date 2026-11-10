# A3: Python reference checker

Owns: `proof/check/py_checker.py`. Read `CONTRACTS.md` (Invariants + CheckResult) first.

## Goal
The reference implementation of the invariants. Small, obviously correct, and the oracle the Lean checker is tested against.

## Do
1. `check(inp: CheckInput) -> CheckResult` implementing `roll_forward`, `arithmetic`, `traceability`, `no_double_count`, `coverage` exactly as defined.
2. Every violation carries a witness: `line_id`, `month_idx`, `txn_ids` (the matched txns for traceability; the offending txn for double-count/coverage), `expected_cents`, `actual_cents`, and a plain-English `message` with dollar formatting (`$1,300.00`) and month names.
3. Keep it pure and boring: no I/O, no dependencies beyond schemas. Target under ~200 lines. Sophie needs to be able to explain every line.
4. Serialize with `CheckResult.to_canonical_json()`.
5. Tests: all fixtures in `tests/fixtures/` match `expected_result.json`. Plus property tests (`hypothesis`): a CheckInput built consistently from random txns is always `ok`; perturbing any single value by a nonzero amount always produces ≥1 violation.

## Acceptance
- `make test` green, including hypothesis tests at default settings.
- A short docstring at the top stating each invariant in one line of math.

## Don't
- Parse files, call Lean, or add "smart" heuristics. Exact integer equality only.
