# A1: Synthetic company + clean runway model

Owns: `synth/`. Read `CONTRACTS.md` (demo company section) first.

## Goal
Generate "Slope": realistic exports and a **correct** runway workbook built from them, deterministic given a seed.

## Do
1. `synth/company.py`: generate 12 months (Jan–Dec 2026) of transactions with a fixed seed:
   - ~8 employees, 2 hires mid-year, one departure. Payroll semimonthly with employer taxes.
   - ~20 SaaS vendors (Figma, Notion, AWS, GitHub, Slack, Linear, etc.), some annual, some monthly, AWS usage-variable.
   - 2–3 contractors, irregular invoices via Ramp bill-pay.
   - Rent monthly from Bank. Stripe payouts in Bank growing ~8%/month from ~$15k.
   - A few refunds (positive Ramp amounts), one vendor that first appears in month 7 (needed later for `missing_vendor`).
   - Starting cash ~$2.4M, burn ~$140–180k/month.
2. `synth/workbook.py`: write an `.xlsx` with tabs `Ramp`, `Bank`, `Payroll`, `Runway` exactly as CONTRACTS.md specifies. The `Runway` tab uses **real formulas** (`SUMIFS`, references to prior Ending Cash, `SUM` for subtotals), not pasted values. Category rows must cover every transaction exactly once.
3. Make it look human-built: a header color, number formatting, a "Notes" column, one frozen pane. Judges will see it on screen.
4. `synth/__main__.py`: `python -m synth --seed 7 --out data/slope_clean.xlsx`.
5. Compute expected values in Python independently and verify the workbook's formulas match them (recalculate with LibreOffice headless `soffice --convert-to xlsx` or the `formulas` package; note which in the README).

## Acceptance
- Workbook opens cleanly in Excel/Google Sheets with no `#REF!`/`#VALUE!`.
- Test: independent Python totals == recalculated sheet values for every Runway cell.
- 3 hand-planted bug variants saved as `data/slope.xlsx`, `..._missing_vendor.xlsx`, `..._broken_rollforward.xlsx` (Phase 1 exit test).

## Don't
- Parse workbooks back (A2) or write checker logic (A3).
