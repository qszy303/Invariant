# Proof: build plan

Demo day: **Thu Nov 19, Ramp SF.** Code freeze: **Mon Nov 16.**

## How to use these files

1. Create an empty repo, put `PLAN.md` and `CONTRACTS.md` at the root and the briefs in `agents/`.
2. Run **Agent 0** alone first. It scaffolds the repo and commits the contracts as code (schemas, fixtures). Everything else depends on it.
3. Run each later agent in its **own branch/worktree**. Give it: `CONTRACTS.md` + its brief. Tell it: *"Follow CONTRACTS.md exactly. If you think a contract is wrong, stop and tell me instead of changing it."*
4. You are the integrator. Merge, run `make test`, and read every line of the checker and Lean code yourself. Ramp engineers will ask you how it works; "the agent wrote it" is not an answer.

## Phases

| Phase | Dates | Agents (parallel within a phase) | Exit criteria |
|---|---|---|---|
| 0 | Nov 1–2 | **A0** scaffold + contracts | `make test` runs; fixtures load; schemas validate |
| 1 | Nov 2–5 | **A1** synthetic data · **A2** sheet parser + ledger · **A3** Python checker | CLI catches 3 hand-planted bugs end to end (rule-based spec, no LLM yet) |
| 2 | Nov 5–10 | **A4** Lean checker · **A5** LLM spec agent · **A6** mutation generator + eval | Lean and Python checkers agree on all fixtures; eval prints detection/false-positive numbers |
| 3 | Nov 10–15 | **A7** explain + fix loop · **A8** UI + demo mode | Upload → red cells → click → contradicting txns → accept fix → re-verify → green |
| 4 | Nov 16–18 | You | Freeze, record backup demo video, rehearse 3×, write slides |

## Dependency graph

```
A0 ──┬── A1 ──┐
     ├── A2 ──┼── (Phase 1 integration) ──┬── A4 ──┐
     └── A3 ──┘                           ├── A5 ──┼── A7 ── A8
                                          └── A6 ──┘
```

A3 and A4 code against the same JSON contract, so A4 can start as soon as A0 is merged if you want more parallelism.

## Cut list (cut in this order if behind)

1. UI → a clean notebook/CLI demo
2. Auto-fix → explanation only
3. Lean soundness proofs → only roll-forward + no-double-count proven (say so honestly)
4. **Never cut:** eval numbers, the Python checker, the backup video

## Guardrails for every agent

- Money is **integer cents**, never floats.
- No network calls except the Anthropic API in A5/A7.
- Lean: `lake build` must pass with **zero `sorry`** (`grep -rn sorry lean/` must be empty) and no new `axiom`s.
- Every module ships with tests; `make test` stays green on `main`.
- Don't edit files owned by another agent (ownership listed in CONTRACTS.md).

## Open questions for you (answer before Phase 2)

- How much Lean can you read? If little, schedule an evening to go through A4's output line by line.
- Presentation slot length (sets demo length).
- Anthropic API key + budget for A5/A6 eval runs.
