# Invariant

**Checks every number in a startup's financial model against the transactions behind it.**
The AI decides what each line means. Lean decides whether the numbers are right. The model never does arithmetic.

Built for the BBS × Ramp AI Hackathon (technical track).

<!-- TODO: screenshot of the Check page with red cells + drawer open -->
![Invariant catching a double-counted subscription](demo/screens/TODO.png)

---

## The problem

A startup's biggest decisions (raising, hiring, spending) run on a spreadsheet: the runway model. Every month someone pastes in exports from Ramp, the bank, and payroll, patches the formulas, and sends it to the board. "Review" means someone eyeballing it. More and more of those formulas are written by AI, which makes models faster to build but no easier to trust.

Spreadsheet errors are silent. One subscription counted in two categories, one month dropped from a `SUMIFS` range, and the runway on the board slide doesn't exist.

## What it does

1. **Load a workbook:** the runway model plus the raw Ramp, Bank, and Payroll export tabs.
2. **Run the check.** You get the headline (ending cash *claimed* vs *verified*) and your own sheet with problems highlighted:
   - **Red:** the number doesn't match the transactions behind it.
   - **Amber:** Invariant isn't confident what this row should contain, so a human should confirm it.
3. **Click a red cell** to see the rule that broke, expected vs actual, and the exact transactions that contradict it.
4. **Apply a verified fix.** Invariant proposes a corrected formula, re-runs the entire check on a copy, and marks the fix verified only if the violation is gone and nothing new broke.

<!-- TODO: link the demo video -->
**Demo video:** TODO

## Quickstart

```bash
uv sync
PROOF_DEMO=1 make demo        # offline, cached; no API key needed
# open http://127.0.0.1:8502

make test                     # unit, property, and Lean-vs-Python differential tests
cd lean && lake build         # build the verified checker
make eval                     # mutation-suite eval -> eval/RESULTS.md
```

To run on new workbooks with the LLM spec agent, set `ANTHROPIC_API_KEY` (and optionally `PROOF_MODEL`).
<!-- TODO: confirm port and commands match the repo -->

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

<!-- TODO: list ONLY what lean/AXIOMS.md confirms. Delete any invariant that isn't fully proven. -->
For each invariant, the Lean checker is proven **sound and complete** against its mathematical spec:
`checkX inp = true ↔ XHolds inp`. No `sorry`, no custom axioms (see `lean/AXIOMS.md`).

- Proven: TODO (e.g. roll-forward, traceability, no double counting, coverage, arithmetic)

**Not verified, and why that's OK:**
- **The LLM spec step.** It can misread a row. That's why it reports confidence and flags uncertain rows, why the eval measures it directly, and why it never computes or compares a number.
- **The xlsx parser and JSON I/O.** Ordinary code, covered by tests.

The proofs guarantee the math is right *given the input*. The eval tests whether the input is right.

## Eval

There are no labeled real-world spreadsheets, so we plant bugs on purpose (mutation testing) in a synthetic seed-stage company ("Lumen Labs", 12 months) and measure what gets caught.

<!-- TODO: fill from eval/RESULTS.md -->
| Metric | Result |
|---|---|
| Detection (105 planted bugs, 7 types) | 105 / 105 |
| Localized to the right cell | TODO |
| False positives (20 clean models) | 0 / 20 |
| Lean vs Python checker, byte-identical output | 125 / 125 |
| LLM spec on messy sheets (4 variants) | TODO |

Bug types: duplicated row, dropped month in a range, new vendor missing from criteria, vendor matched by two categories, flipped sign, formula overwritten by a stale number, broken roll-forward.

**Caveat:** these are bugs we designed, in a company we generated. Real spreadsheets will be harder. Full results and every known miss are in [`eval/RESULTS.md`](eval/RESULTS.md).

## Design decisions

<!-- TODO: rewrite this section in your own words. Reviewers will read it as you. -->
- **The LLM never does arithmetic.** It only answers "what should this line contain?" Every number is compared by exact integer equality in the checker. A hallucination can mislabel a row; it can't invent a sum.
- **Why Lean.** A checker is only useful if you trust it more than the spreadsheet. Proving each check equivalent to its spec means a "pass" means the property holds, not "the tests I thought of passed."
- **A Python twin of the checker.** Simple, readable, and the oracle for a differential test against Lean on every fixture plus random inputs. If they ever disagree, one of them is wrong.
- **Integer cents everywhere.** No floating-point rounding in a tool whose job is exact equality.
- **Fixes are verified before they're offered.** A proposed formula runs through the whole pipeline again; "verified fix" is only shown if the re-check passes.
- **Confidence gating over guessing.** An uncertain read becomes an amber cell for a human, not a confident wrong answer.
- **Offline demo mode.** Cached LLM responses make the demo deterministic and runnable without network or an API key.

## Limitations

- **Forecast months.** Future months are assumptions with no transactions behind them. Invariant can check their structure (roll-forward, arithmetic) but not whether the assumptions are reasonable.
- **Timing and accruals.** A charge on Mar 31 may settle Apr 2; accrual accounting books costs when incurred. Real use needs tolerance rules for both.
- **Messy real workbooks.** Cross-workbook links, pivot tables, and manual adjustments aren't handled yet.
- **Exports, not integrations.** Transactions come from export tabs inside the workbook.
<!-- TODO: if the demo uses the rules baseline instead of the LLM spec, say so here -->

## Roadmap

1. Live connectors: Ramp API, Plaid for bank data, payroll providers.
2. Tolerance rules for settlement timing and accruals.
3. Assumption checks for forecast months (e.g. flag a hire in the plan with no payroll line).
4. A verification layer for AI-generated finance work: accounting firms reviewing client models, or alongside agent-built workpapers in tools like Ramp.

## How I built this

<!-- TODO: your words. Keep it short and specific. -->
I designed the architecture, wrote the interface contracts and acceptance criteria for each component, and built it with Claude Code agents, one per component, each in its own branch. Every component had to pass its acceptance tests before merging, and the Lean checker had to match the Python checker byte-for-byte. I reviewed the checker and the proofs myself.

The contracts, plan, and agent briefs are in [`docs/process/`](docs/process/).

## Repo layout

```
proof/        pipeline: ingest, spec (rules + LLM agent), assign, check, fix
lean/         Lean 4 verified checker + proofs
synth/        synthetic company + runway model generator
eval/         mutation suite + metrics
app/          FastAPI backend + UI
docs/process/ contracts, build plan, agent briefs
```
