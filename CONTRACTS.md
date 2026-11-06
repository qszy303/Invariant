# Proof: shared contracts

Every agent follows this file exactly. Changes to it need to be manually approved.

## Stack

- Python 3.11+, `uv` for deps, `pytest`, `openpyxl`, `pydantic` v2, `anthropic` SDK, `streamlit`
- Lean 4 + Lake, **core only** (no Mathlib, keeps builds fast); JSON via `Lean.Data.Json`
- `Makefile` targets: `make test`, `make lean`, `make eval`, `make demo`

## Repo layout and ownership

```
proof/
  schemas.py          A0   pydantic models for every JSON below
  ingest/sheet.py     A2   xlsx -> CellGraph
  ingest/ledger.py    A2   raw export tabs -> Ledger
  spec/rules.py       A2   deterministic RuleSpec (baseline, no LLM)
  spec/agent.py       A5   LLM: CellGraph -> RuleSpec
  assign.py           A2   RuleSpec + Ledger + CellGraph -> CheckInput
  check/py_checker.py A3   CheckInput -> CheckResult
  check/lean_bridge.py A4  CheckInput -> CheckResult via Lean binary
  fix/                A7   explanations + proposed fixes + re-verify
  cli.py              A0   `proof check model.xlsx`
lean/                 A4   Lake project `ProofCheck`
synth/                A1   synthetic company + clean model generator
eval/                 A6   mutation generator + metrics
app/                  A8   Streamlit UI
tests/fixtures/       A0 seeds, everyone adds
```

## The demo company (A1 generates; everyone assumes this shape)

"Slope", seed-stage SaaS, 12 months: **Jan–Dec 2026**, months indexed `0..11`.

Workbook tabs:
- `Ramp`: card/bill-pay export: `id, date, merchant, amount, category, memo`
- `Bank`: `id, date, description, amount` (Stripe payouts in, rent/transfers out)
- `Payroll`: `id, pay_date, employee, gross, employer_taxes`
- `Runway`: the model. Row labels in column A, months in columns B–M (row 1 = month headers). Rows:
  `Starting Cash, Revenue, Payroll, SaaS, Contractors, Rent, Other, Total Outflows, Net Cash Flow, Ending Cash`
  Category rows use `SUMIFS` over the export tabs. Starting Cash for month 0 is a hardcoded input; later months reference prior Ending Cash.

## Sign convention

`amount_cents`: **positive = money in, negative = money out**, in the Ledger and CheckInput. The `Runway` sheet shows outflow rows as positive numbers; A2's `assign.py` converts.

## Schemas (JSON; pydantic models in `proof/schemas.py`)

**Transaction / Ledger**
```json
{"id": "ramp-0042", "source": "ramp|bank|payroll", "date": "2026-03-14",
 "month_idx": 2, "amount_cents": -129900, "counterparty": "Figma",
 "memo": "", "category_hint": "Software"}
```
Ledger = `{"transactions": [Transaction, ...]}`

**CellGraph**
```json
{"cells": [{"ref": "Runway!D5", "sheet": "Runway", "row_label": "SaaS",
  "month_idx": 2, "value_cents": 412300, "formula": "=SUMIFS(Ramp!D:D, ...)",
  "deps": ["Ramp!D2:D400", "Ramp!E2:E400"]}]}
```

**RuleSpec** (what each line *should* contain)
```json
{"lines": [{"line_id": "saas", "row_label": "SaaS",
  "kind": "start|inflow|outflow|subtotal|net|end",
  "rule": {"sources": ["ramp"], "counterparty_in": ["Figma", "Notion"],
           "category_in": ["Software"]},
  "confidence": 0.93, "rationale": "SUMIFS over Ramp category=Software"}]}
```
`rule` is null for `start/subtotal/net/end`. `confidence` is 1.0 for the deterministic baseline.

**CheckInput** (the only thing the checkers see)
```json
{"months": 12,
 "lines": [{"line_id": "saas", "kind": "outflow", "values_cents": [ ...12 ints... ]}],
 "txns": [{"id": "ramp-0042", "month_idx": 2, "amount_cents": -129900,
           "assigned_line_ids": ["saas"]}]}
```
`values_cents` for outflow lines are **negative** (converted from the sheet).

**CheckResult** (Python and Lean must emit byte-identical JSON for the same input; sort violations by `invariant, line_id, month_idx`)
```json
{"ok": false,
 "violations": [{"invariant": "traceability", "line_id": "saas", "month_idx": 2,
   "txn_ids": ["ramp-0042"], "expected_cents": -282200, "actual_cents": -412300,
   "message": "SaaS in Mar is 1,300.00 more than its matched transactions"}]}
```

## Invariants (the four checks, in both checkers)

1. **roll_forward**: for every month m: `end[m] = start[m] + Σ inflow[m] + Σ outflow[m]`, and for m ≥ 1: `start[m] = end[m-1]`.
2. **traceability**: for every inflow/outflow line L and month m: `values[L][m] = Σ amount of txns with month m and L ∈ assigned_line_ids`.
3. **no_double_count**: every txn has `len(assigned_line_ids) ≤ 1`.
4. **coverage**: every txn has `len(assigned_line_ids) ≥ 1`.

(Subtotal/net rows: also check `total_outflows[m] = Σ outflow lines[m]` and `net[m] = Σ inflow + Σ outflow` under invariant name `arithmetic`.)

## Bug taxonomy (A1 seeds 3 by hand; A6 generates all programmatically)

| id | what goes wrong in the sheet | invariant that should fire |
|---|---|---|
| `dup_row` | a Ramp row pasted twice | traceability |
| `dropped_month` | a SUMIFS date range skips a month | traceability |
| `missing_vendor` | new vendor not in a SUMIFS criteria list | coverage |
| `overlap_category` | vendor matched by both SaaS and Contractors | no_double_count + traceability |
| `sign_flip` | refund counted as spend | traceability |
| `stale_hardcode` | a formula overwritten with a typed number | traceability |
| `broken_rollforward` | Starting Cash not linked to prior Ending Cash | roll_forward |

## Fixtures (A0 creates, all agents use)

`tests/fixtures/clean/` and `tests/fixtures/bug_<id>/` each contain `ledger.json`, `cellgraph.json`, `rulespec.json`, `checkinput.json`, `expected_result.json`. A0 hand-writes a small 3-month clean case plus `dup_row` and `broken_rollforward`. A1/A6 add generated ones later.
