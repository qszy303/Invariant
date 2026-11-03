# A0: Scaffold + contracts as code

**Run first, alone.** Read `CONTRACTS.md` fully before writing anything.

## Goal
Turn CONTRACTS.md into a working repo skeleton every other agent can build on.

## Do
1. `uv` project, `pyproject.toml`, deps from CONTRACTS.md. `Makefile` with `test`, `lean`, `eval`, `demo` (stub targets are fine for now).
2. Create every directory and empty module in the layout, each with a one-line docstring naming its owner agent.
3. `proof/schemas.py`: pydantic v2 models for Transaction, Ledger, Cell, CellGraph, Rule, SpecLine, RuleSpec, CheckLine, CheckTxn, CheckInput, Violation, CheckResult. Use `int` for all cents. Add a `CheckResult.to_canonical_json()` that sorts violations by `(invariant, line_id or "", month_idx if not None else -1)` and dumps with sorted keys, no whitespace. Both checkers must use this exact serialization.
4. Hand-write fixtures in `tests/fixtures/` for a **3-month, ~15-transaction** mini company: `clean/`, `bug_dup_row/`, `bug_broken_rollforward/`. Each folder: `checkinput.json` + `expected_result.json` at minimum (ledger/cellgraph/rulespec optional here; A1/A2 generate those later).
5. `tests/test_schemas.py`: every fixture loads and round-trips.
6. `proof/cli.py`: `proof check <checkinput.json>` that loads input and calls `proof.check.py_checker.check` (import may fail until A3 lands; guard it).

## Acceptance
- `make test` passes.
- Fixture `expected_result.json` values are hand-verified (show the arithmetic in a comment file `tests/fixtures/README.md`).

## Don't
- Implement checker logic, parsing, or Lean. Just the skeleton and contracts.
