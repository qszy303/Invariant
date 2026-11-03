# A4: Lean 4 verified checker

Owns: `lean/`, `proof/check/lean_bridge.py`. Read `CONTRACTS.md` first, then `proof/check/py_checker.py` if it exists.

## Goal
A Lean implementation of the same invariants, **with proofs that each check is sound and complete**, compiled to a binary that reads CheckInput JSON and emits CheckResult JSON identical to Python's.

## Do
1. Lake project `ProofCheck`, Lean 4 core only (no Mathlib). Pin the toolchain in `lean-toolchain`.
2. Model: `structure Line (kind, values : List Int)`, `structure Txn (month : Nat, amount : Int, assigned : List String)`, `structure Input`.
3. Define each invariant twice:
   - a `Prop` spec (the mathematical statement), e.g. `RollForwardHolds inp : Prop`
   - a `Bool` checker, e.g. `checkRollForward inp : Bool`
4. Prove, for each of the five invariants: `theorem checkX_iff : checkX inp = true ↔ XHolds inp`. Priority order if time is short: `no_double_count`, `coverage`, `roll_forward`, `traceability`, `arithmetic`.
5. Witness functions that return the failing line/month/txn when a check fails; prove at least for `no_double_count` that a returned witness genuinely violates the spec.
6. `Main.lean`: parse JSON with `Lean.Data.Json`, run checks, emit canonical JSON (same key order, sorting, and message text as Python; copy the message templates from py_checker).
7. `lean_bridge.py`: build path discovery, subprocess call, parse result.
8. Differential test `tests/test_lean_vs_python.py`: every fixture plus 500 random CheckInputs (reuse A3's hypothesis strategy) produce byte-identical JSON.

## Acceptance
- `lake build` succeeds. `grep -rn "sorry\|admit" lean/` is empty. No new `axiom`.
- `#print axioms checkRollForward_iff` (and the others) shows only standard axioms (`propext`, `Quot.sound`, `Classical.choice` at most). Put the output in `lean/AXIOMS.md`.
- Differential test passes.
- `lean/README.md` explaining in plain English what is proven and, explicitly, what is **not** (the LLM spec translation, the xlsx parser, the JSON parser).

## Don't
- Pull in Mathlib "just for one lemma". If stuck on a proof for over an hour, simplify the data representation (e.g. index months by list position) rather than fighting it.
