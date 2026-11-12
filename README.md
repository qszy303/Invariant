# Invariant

**Checks every number in a startup's financial model against the transactions behind it**
The AI decides what each line means; Lean decides whether the numbers are right; the model never does arithmetic.

![Invariant catching a month dropped from a SUMIFS range](demo/screens/10_fernway.png)

---

## The problem

A startup's biggest decisions (raising, hiring, spending) all rely on a spreadsheet: the runway model. Every so often, someone or an agent exports data from Ramp, the bank, and the payroll into the spreadsheet, patching the formulas and sending it to the board. "Review" means someone eyeballing it. Nowadays, more and more of these formulas and additions are written by AI, which makes the models faster to build but not easier to trust.

Spreadsheet errors are silent. One subscription counted in two categories, one month dropped from a `SUMIFS` range, and the runway on the board slide doesn't exist. But a wrong number could mean thousands or millions go unaccounted for, or allow a company to burn through capital it doesn't have.

## What Invariant does

1. **Load a workbook:** the runway model plus the raw Ramp, Bank, and Payroll export tabs.
2. **Run the check.** You get the headline (ending cash *claimed* vs *verified*) and your own sheet with problems highlighted:
   - **Red:** the number doesn't match the transactions behind it.
   - **Pink:** Invariant isn't confident what this row should contain, so a human should confirm it.
3. **Click a red cell** to see the rule that broke, expected vs actual, and the exact transactions that contradict it.
4. **Apply a verified fix.** Invariant proposes a corrected formula, re-runs the entire check on a copy, and marks the fix verified only if the violation is gone and nothing new broke.

<!-- TODO: link the demo video -->
**Demo video:** https://drive.google.com/file/d/1ge1uQwbyu9b2gQpaRBKklpNqS7y37pIs/view?usp=sharing

## Quickstart

```bash
uv sync
PROOF_DEMO=1 make demo        # offline, cached; no API key needed
# open http://127.0.0.1:8502 and pick samples/fernway_dropped_month.xlsx (the demo file:
# a synthetic company with one planted bug, a month dropped from the SaaS SUMIFS range)

make test                     # unit, property, and Lean-vs-Python differential tests
cd lean && lake build         # build the verified checker
make eval                     # mutation-suite eval -> eval/RESULTS.md
```

To run on new workbooks with the LLM spec agent, put a key in `.env` (git-ignored; it is read at startup) and run `make demo` *without* `PROOF_DEMO=1`:

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Claude, the model the eval ran on
# or, if there is no Anthropic key:
OPEN_AI_API_KEY=sk-...         # OpenAI (default model gpt-4.1; override with OPENAI_MODEL)
```

The Python package is named `proof` (the project's working name); the product is Invariant.

`PROOF_SPEC=rules` switches the spec step to the deterministic rules baseline (no model). `make screens` retakes the 1920x1080 screenshots in `demo/screens/`.

## How it works

```
 .xlsx ──► parse ──► cell graph ─┐
                                 ├──► spec (LLM) ──► assign txns ──► CHECK (Lean) ──► explain + fix ──► re-check
 exports ──► ledger (cents) ─────┘    "what each        to lines        5 invariants      (LLM proposes,
                                       line should                                         checker decides)
                                       contain"
```

| Step | What happens | Verified? |
|---|---|---|
| Parse | openpyxl reads the Runway sheet into cells, formulas, and dependencies | No (tested) |
| Ledger | Ramp / Bank / Payroll exports normalized to one transaction list, integer cents | No (tested) |
| Spec | An LLM reads row labels and formulas and states what each line *should* contain, with a confidence score. Below 0.8 → flagged amber, not guessed. | No (evaluated) |
| Assign | Each transaction is matched to every line whose rule covers it | No (tested) |
| **Check** | Five invariants, implemented in Lean 4 with proofs | **Yes** |
| Fix | LLM proposes a formula; the full pipeline re-runs; accepted only if the check passes | Re-checked by the verified checker |

### The invariants

1. **Roll-forward:** ending cash = starting cash + inflows + outflows, and each month starts where the last one ended.
2. **Traceability:** every line equals the sum of the transactions that belong to it.
3. **No double counting:** no transaction is counted on more than one line.
4. **Coverage:** no transaction is missing from the model.
5. **Arithmetic:** subtotals and net cash flow add up.

## What's proven, and what isn't

For each invariant, the Lean checker is proven **sound and complete** against its mathematical spec:
`checkX inp = true ↔ XHolds inp`. No `sorry`, no custom axioms (see `lean/AXIOMS.md`).

- **Proven (`checkX_iff`, all five):** roll-forward, arithmetic, traceability, no double counting, coverage. Each depends only on the standard axioms `propext` and `Quot.sound`.
- **Also proven:** the witness finders for no-double-counting and coverage return a transaction that really violates the rule (and none exactly when the rule holds), and the reported no-double-counting and coverage violations are exactly the violating transactions (`noDoubleCountViolations_sound`, `*_nil_iff`; these also use `Classical.choice`).
- **Not proven in Lean:** that the violation *lists* for roll-forward, arithmetic and traceability correspond to their rules. Only their yes/no checkers are proven; the lists are tested against the Python twin instead. Message text and sorting are also only tested.

**Not verified, and why that's OK:**
- **The LLM spec step.** It can misread a row. That's why it reports confidence and flags uncertain rows, why the eval measures it directly, and why it never computes or compares a number.
- **The xlsx parser and JSON I/O.** Ordinary code, covered by tests.

The proofs guarantee the math is right *given the input*. The eval tests whether the input is right.

## Eval

There are no labeled real-world spreadsheets, so we plant bugs on purpose (mutation testing) in a synthetic seed-stage company ("Slope", 12 months) and measure what gets caught.

| Metric | Result |
|---|---|
| Detection (105 planted bugs, 7 types) | 105 / 105 |
| Localized to the right cell | 76 / 90 (84%) |
| False positives (20 clean models) | 0 / 20 |
| Lean vs Python checker, byte-identical output | 125 / 125 |
| LLM spec on messy sheets (4 variants) | 4 / 4 read correctly; 2 flagged for review; 0 wrong and confident |

*Localized to the right cell* is strict: a flagged violation must sit on a ground-truth Runway cell. `missing_vendor` has no Runway cell (the uncounted transactions are in no row), so its 15 bugs are excluded rather than counted as misses; `overlap_category` scores 1 / 15 because a double count flags transactions, not a cell.

**Which model produced what.** The 105 / 125-model suite used the LLM spec from `claude-sonnet-5-5` (replayed from its cache). The 4-variant messy-sheet result was run with `gpt-4.1`. The live demo (`demo/cache`) also reads rows with `gpt-4.1`, so demo output is not the same model as the suite numbers.

Bug types: duplicated row, dropped month in a range, new vendor missing from criteria, vendor matched by two categories, flipped sign, formula overwritten by a stale number, broken roll-forward.

**Caveat:** these are bugs we designed, in a company we generated. Real spreadsheets will be harder. Full results and every known miss are in [`eval/RESULTS.md`](eval/RESULTS.md).

## Design decisions

<!-- TODO: rewrite this section in your own words. Reviewers will read it as you. -->
- **The LLM never does arithmetic.** It only determines what each line contains. Every number is compared by exact integer equality in the checker. A hallucination can mislabel a row; it can't invent a sum.
- **Why Lean.** A checker is only useful if you trust it more than the spreadsheet. Proving each check equivalent to its spec means a "pass" means the property holds, and a verification language is stronger than running through test cases.
- **A Python implementation of the checker.** Another checker, written in Python. It has the same functionality as the Lean checker but it's written in simpler code. If the two checkers disagree, this is flagged as an issue.
- **Integer cents everywhere.** No floating-point rounding in a tool whose job is exact equality.
- **Fixes are verified before they're offered.** A proposed formula runs through the whole pipeline again; "verified fix" is only shown if the re-check passes.
- **Confidence gating over guessing.** An uncertain read becomes an amber cell for a human, not a confident wrong answer.
- **Offline demo mode.** Cached LLM responses make the demo deterministic and runnable without network or an API key.

## Limitations

- **Forecast months.** Future months are assumptions with no transactions behind them. Invariant can check their structure (roll-forward, arithmetic) but not whether the assumptions are reasonable.
- **Timing and accruals.** A charge on Mar 31 may settle Apr 2; accrual accounting books costs when incurred. Real use needs tolerance rules for both.
- **Messy real workbooks.** Cross-workbook links, pivot tables, and manual adjustments aren't handled yet.
- **Exports, not integrations.** Transactions come from export tabs inside the workbook.
- **The demo reads rows with the LLM spec (`gpt-4.1`), not the rules baseline.** That model misreads the `Other` row of `slope_missing_vendor.xlsx` (it leaves out bank fees). Invariant flags that row for review and blocks "Apply verified fix" on it, so that file is left out of the demo picker and listed under known misses in `eval/RESULTS.md`.

## Roadmap

1. Live connectors: Ramp API, Plaid for bank data, payroll providers
2. Tolerance rules for settlement timing and accruals
3. Assumption checks for forecast months (e.g. flag a hire in the plan with no payroll line)
4. A verification layer for AI-generated finance work: accounting firms reviewing client models, or alongside agent-built workpapers in tools like Ramp

## How I built this

I designed Invariant’s pipeline and wrote CONTRACTS.md to define the data each component receives and returns, the five invariants, and how amounts are represented in integer cents. I used Claude to plan the work and Claude Code agents to implement most of it, giving each agent a spec and acceptance criteria.

I got the checker working with deterministic rules before adding the LLM. I compared Lean’s output against a Python reference checker on fixtures and randomized inputs, then tested whether Invariant flagged the right cell on synthetic workbooks with planted bugs.

One bug changed how I handled fixes: the system could approve a formula against the same misread row that prompted the fix. I blocked automatic fixes on rows flagged for human review.

The planning docs and agent briefs are in [`docs/process/`](docs/process/)
[if present].

## Repo layout

```
proof/        pipeline: ingest, spec (rules + LLM agent), assign, check, fix
lean/         Lean 4 verified checker + proofs
synth/        synthetic company + runway model generator
eval/         mutation suite + metrics
app/          FastAPI backend + UI
```
