# lean/

Lake project `ProofCheck` (core Lean 4.35, no Mathlib). `make lean` builds it and prints the axioms
used by each theorem (saved in `AXIOMS.md`). The binary is `.lake/build/bin/proofcheck`; it reads
CheckInput JSON (file argument or stdin) and prints the canonical CheckResult JSON, byte-identical
to `CheckResult.to_canonical_json()` (no trailing newline).

- `ProofCheck/Spec.lean`: data model, the five invariants as `Prop`s, `Bool` checkers, proofs.
- `ProofCheck/Report.lean`: violation generation, message templates, canonical JSON.
- `Main.lean`: JSON parsing and the CLI.

## What is proven

For each invariant (roll_forward, arithmetic, traceability, no_double_count, coverage),
`checkX inp = true ↔ XHolds inp`: the Boolean checker accepts exactly the inputs satisfying the
mathematical statement in `CONTRACTS.md` (sound and complete). Also proven: the witness finders for
no_double_count and coverage return a transaction that truly violates the spec (and return none
exactly when the spec holds), and the reported no_double_count / coverage violations are exactly the
violating transactions (`*_nil_iff`, `noDoubleCountViolations_sound`).

## What is NOT proven

- That the violation lists for roll_forward, arithmetic and traceability correspond to their `Prop`s.
  Only the Bool checkers are proven; those violation generators are tested differentially against
  Python, not proven.
- Message text, sorting, and JSON string escaping (checked only by the differential test).
- The JSON parser (`Lean.Data.Json`) and the field extraction in `Main.lean`.
- The LLM spec translation (RuleSpec), the xlsx parser, the ledger ingest, and the line assignment
  that produce the CheckInput. The proofs say the numbers in a CheckInput are consistent, not that
  the CheckInput faithfully represents the workbook.
- Months are list positions; a values list shorter than `months` reads as 0 in Lean (the bridge and
  Python validate lengths first, and `Main` rejects mismatches).
