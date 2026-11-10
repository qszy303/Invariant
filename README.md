# Invariant

Does your financial model's math add up?

Invariant checks a spreadsheet financial model (a startup cash-runway model) against the
transactions it claims to summarize. It finds the numbers that don't tie out, explains each one in
plain language, and proposes a fix that it re-checks on a copy of your workbook before offering it.
The checker behind every verdict is written in Lean 4 and proved correct, with a Python reference
implementation that must produce byte-identical output.

What it catches (each has an invariant that fires):

| Problem in the sheet | Invariant |
|---|---|
| a row pasted twice, a SUMIFS date range that skips a month, a refund counted as spend, a formula overwritten by a typed number | traceability |
| a new vendor missing from every SUMIFS criteria list | coverage |
| one vendor matched by two rows | no double count |
| Starting Cash typed over the link to last month's Ending Cash | roll forward |

## Quick start

Requirements: Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). The Lean checker is optional
(install [`elan`](https://github.com/leanprover/elan), then `make lean`); without it the app uses the
Python checker and says so.

```sh
uv sync
make lean      # optional: build the proved Lean checker
make app       # opens http://127.0.0.1:8501
```

In the app, pick one of the four sample workbooks, or drop in your own `.xlsx`. Click a red cell for
the explanation, the transactions that contradict it, and a verified fix. **Apply verified fix** works
on a copy; your original is never changed, and the fixed copy can be downloaded.

### API key

Checking, explaining and the Lean/Python verdict run entirely locally. Only **fix proposals** for
a new workbook call the model, using your own Anthropic API key. The four samples ship with their
fixes and need no key.

```sh
export ANTHROPIC_API_KEY=...     # or paste it under Settings in the app (kept in memory only)
```

The key is read from the environment. Never commit it; `.env` files are git-ignored.

### Command line

```sh
uv run proof check model.xlsx          # check; exit code 1 if any invariant fails
uv run python -m proof.fix model.xlsx --out fixed.xlsx   # explain and fix (needs a key)
```

## How it works

```
model.xlsx --ingest--> CellGraph (Runway formulas + values)   proof/ingest/sheet.py
           \--ingest--> Ledger   (Ramp, Bank, Payroll rows)    proof/ingest/ledger.py
CellGraph --spec-----> RuleSpec  (what each row should contain) proof/spec/rules.py  (deterministic)
                                                                proof/spec/agent.py  (LLM, optional)
RuleSpec + Ledger + CellGraph --assign--> CheckInput            proof/assign.py
CheckInput --check--> CheckResult   proof/check/py_checker.py   (Python reference)
                                    lean/                       (proved Lean checker)
CheckResult --fix---> explanation, proposed fix, re-check       proof/fix/
```

The five invariants (see `CONTRACTS.md` for the exact statements): roll forward, arithmetic,
traceability, no double count, coverage.

### What's proven, and what isn't

Lean proves that each Boolean checker accepts exactly the inputs that satisfy the mathematical
statement of its invariant (sound and complete). It does **not** prove that the xlsx parser, the
ledger ingest, the spec translation (including the LLM) or the line assignment faithfully represent
your workbook: the proofs say the numbers in a `CheckInput` are consistent with each other. The
full list is in [`lean/README.md`](lean/README.md).

## Evaluation

`make eval` plants seven kinds of bug into 105 synthetic models (5 company seeds x 7 bugs x 3
placements), runs 20 clean models, and reports detection, localization, false positives, spec-agent
accuracy, Python/Lean agreement and fix success. Results, including every known miss, are in
[`eval/RESULTS.md`](eval/RESULTS.md). It needs an API key for the LLM parts unless responses are
cached.

The suite is synthetic (one company shape), so the numbers describe the checker on this family of
models, not on arbitrary real workbooks.

## Limits

- Built around a fixed workbook shape: `Runway`, `Ramp`, `Bank`, `Payroll` tabs with the layout in
  `CONTRACTS.md`. Formulas the deterministic reader doesn't understand are refused rather than guessed.
- The Ledger is read from the export tabs, so an edit that keeps the exports and the Runway
  consistent with each other cannot be detected.
- Fix proposals come from an LLM; every one is re-checked before it is offered, but "the checker is
  satisfied" is not the same as "this is what you meant".

## Repository layout

```
proof/      pipeline: ingest, spec, assign, checkers, explain/fix  (the Python package is named `proof`)
lean/       the Lean 4 checker and its proofs
synth/      synthetic company and workbook generator
eval/       bug injectors, suite, metrics, RESULTS.md
app/        web app: API server (server.py), logic (core.py), frontend (static/)
samples/    sample workbooks with precomputed fixes (the app works offline on these)
tests/      pytest suite (`make test`)
docs/       recording a demo: docs/RECORDING.md
```

## Recording a demo

See [`docs/RECORDING.md`](docs/RECORDING.md).
