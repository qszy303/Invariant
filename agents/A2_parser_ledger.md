# A2: Sheet parser, ledger, baseline spec, assignment

Owns: `proof/ingest/`, `proof/spec/rules.py`, `proof/assign.py`. Read `CONTRACTS.md` first.

## Goal
Get from an `.xlsx` to a `CheckInput` with **no LLM**, so the checker can be demoed end to end in Phase 1.

## Do
1. `ingest/sheet.py`: openpyxl, load twice (`data_only=False` for formulas, `data_only=True` for cached values). Produce `CellGraph` for the `Runway` tab: ref, row_label, month_idx, value_cents (round half-even to cents), formula, deps (parse referenced ranges from the formula; a simple regex for `Sheet!A1:B2` / `A1` refs is enough). If cached values are missing, fail with a clear message telling the user to open/save the file once (or recalc via LibreOffice).
2. `ingest/ledger.py`: read `Ramp`, `Bank`, `Payroll` tabs into `Ledger`. Normalize signs per CONTRACTS.md (Payroll gross + employer taxes = one negative txn per row). Compute `month_idx` from date.
3. `spec/rules.py`: deterministic `RuleSpec` by **reading the SUMIFS criteria** in each Runway row's formula (sources, category/merchant criteria). Confidence 1.0. This is the baseline the LLM agent (A5) is compared against.
4. `assign.py`: given RuleSpec + Ledger + CellGraph, assign each txn to every line whose rule matches it (so overlaps surface as `len > 1`, gaps as `len == 0`), and build `CheckInput` with outflow lines negated.

   Important: assignment encodes what each line **should** contain; the sheet values are what it **actually** contains. Traceability compares the two.
5. Wire `proof check model.xlsx` in `cli.py` to run ingest → rules → assign → checker.

## Acceptance
- On `data/lumen_clean.xlsx`: CheckInput produced, checker (once A3 lands) returns `ok: true`.
- On the 3 bug workbooks: the expected invariant fires (see bug taxonomy).
- Unit tests for the formula-ref regex and the SUMIFS criteria extractor, including wildcards and multi-criteria.

## Don't
- Call any LLM. Change schemas.
