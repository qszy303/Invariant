# Fixtures

Hand-built 3-month (Jan-Mar 2026) mini "Slope" company, 15 transactions.
Every folder has `ledger.json`, `cellgraph.json`, `rulespec.json`,
`checkinput.json`, `expected_result.json`. `expected_result.json` is stored in
`CheckResult.to_canonical_json()` form (plus a trailing newline).
All amounts below are dollars; the JSON holds cents.

## Transactions (same in all three fixtures)

| id | month | amount | line |
|---|---|---|---|
| bank-0001 / 0003 / 0005 | Jan / Feb / Mar | +5,000 / +6,000 / +7,000 (Stripe) | revenue |
| bank-0002 / 0004 / 0006 | Jan / Feb / Mar | -2,000 each (Landlord LLC) | rent |
| payroll-0001 / 0002 / 0003 | Jan / Feb / Mar | -3,300 each (3,000 gross + 300 taxes) | payroll |
| ramp-0001 | Jan | -150 Figma | saas |
| ramp-0002 | Jan | -500 Upwork | contractors |
| ramp-0003 | Feb | -150 Figma | saas |
| ramp-0004 | Feb | -80 Notion | saas |
| ramp-0005 | Mar | -150 Figma | saas |
| ramp-0006 | Mar | -600 Upwork | contractors |

`other` has no transactions, so it is 0 in every month.

## clean

Per-line sums (CheckInput sign: outflows negative):

| line | Jan | Feb | Mar |
|---|---|---|---|
| revenue | 5,000 | 6,000 | 7,000 |
| payroll | -3,300 | -3,300 | -3,300 |
| saas | -150 | -150 + -80 = -230 | -150 |
| contractors | -500 | 0 | -600 |
| rent | -2,000 | -2,000 | -2,000 |
| other | 0 | 0 | 0 |

Total outflows = payroll + saas + contractors + rent + other:

- Jan: -3,300 - 150 - 500 - 2,000 = **-5,950**
- Feb: -3,300 - 230 - 0 - 2,000 = **-5,530**
- Mar: -3,300 - 150 - 600 - 2,000 = **-6,050**

Net = revenue + total outflows:

- Jan: 5,000 - 5,950 = **-950**
- Feb: 6,000 - 5,530 = **470**
- Mar: 7,000 - 6,050 = **950**

Roll-forward (start Jan = 10,000 hardcoded):

- Jan: end = 10,000 - 950 = **9,050**
- Feb: start = 9,050; end = 9,050 + 470 = **9,520**
- Mar: start = 9,520; end = 9,520 + 950 = **10,470**

Every line equals the sum of its matched transactions, each txn is assigned to
exactly one line, and start[m] = end[m-1]. Expected: `ok = true`, no violations.

## bug_dup_row

The Figma Mar row (ramp-0005) is pasted twice in the Ramp tab, so the SaaS Mar
SUMIFS counts it twice. The ledger holds the transaction once.

- SaaS Mar in the sheet: -150 + -150 = **-300**; matched txns sum to -150.
- Total outflows Mar: -3,300 - 300 - 600 - 2,000 = -6,200 (consistent with the lines).
- Net Mar: 7,000 - 6,200 = 800; End Mar: 9,520 + 800 = **10,320** (consistent).

So roll_forward and arithmetic hold. The only violation is traceability on
`saas`, Mar (`month_idx` 2): `expected_cents` -15000, `actual_cents` -30000,
`txn_ids` ["ramp-0005"].

## bug_broken_rollforward

Feb Starting Cash is a typed 9,000 instead of `=Jan Ending Cash` (9,050).
Every other cell is internally consistent with that wrong start.

- Feb: start 9,000; end = 9,000 + 470 = **9,470** (equation holds).
- Mar: start = 9,470 (linked correctly); end = 9,470 + 950 = **10,420** (equation holds).

Only the link start[1] = end[0] fails. The single violation is roll_forward on
`start`, Feb (`month_idx` 1): `expected_cents` 905000 (= end[0]),
`actual_cents` 900000, `txn_ids` [].

## Message templates (the checkers must match these exactly)

`CheckInput` carries only `line_id`, not `row_label`, so messages use `line_id`.
Month names come from `month_idx` (0 = Jan, ..., 11 = Dec). `AMT` is the absolute
difference formatted as `1,234.56`.

- traceability: `{line_id} in {Mon} is {AMT} {more|less} than its matched transactions`
  (`more` if |actual| > |expected|, otherwise `less`)
- roll_forward link: `start in {Mon} is {AMT} {more|less} than end of {PrevMon}`
  (`more` if actual > expected, otherwise `less`)
- roll_forward equation: `{end_line_id} in {Mon} is {AMT} {more|less} than {start_line_id} plus net flows`
  (line_id is the `end` line; `more` if actual > expected)
- arithmetic: `{line_id} in {Mon} is {AMT} {more|less} than the sum of its outflow lines`
  for `subtotal` lines; for `net` lines the ending is `inflow and outflow lines`
  (`more` if actual > expected)
- no_double_count: `transaction {id} ({AMT}) in {Mon} is assigned to {n} lines: {sorted ids joined by ", "}`
  (`line_id` null, `txn_ids` [id], expected/actual null)
- coverage: `transaction {id} ({AMT}) in {Mon} is not assigned to any line`
  (same shape as no_double_count)

Other conventions (from A3, Python checker):
- A `month_idx` outside 0..11 prints as `month N`.
- No `$` in any message.
- Lines are identified by `kind`: one `start`, one `end`; flows are all
  `inflow` and `outflow` lines.
- Violations from per-txn checks are emitted in ascending txn id order; traceability
  `txn_ids` are sorted ascending.
