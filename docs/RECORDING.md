# Recording a demo

A ~4 minute screen recording of the app. It runs offline from `demo/cache`, so no API key or Wi-Fi is
needed, and it behaves the same every take.

## Before you record

1. `PROOF_DEMO=1 make demo` and open <http://127.0.0.1:8502> in Chrome. (`make demo` opens it for you.)
   Use a fresh window, 100% zoom (110-125% if it will be shown on a projector), full screen,
   bookmarks bar hidden.
2. Quit chat, mail and notification apps, and turn on Do Not Disturb.
3. Keep API keys out of view: `.env` and the terminal that started the server stay off screen.
4. Practice once. Deep links jump straight to a file and run the check; a reload resets it:
   - `http://127.0.0.1:8502/#demo=slope_clean.xlsx`
   - `http://127.0.0.1:8502/#demo=slope_dup_row.xlsx`
   - `http://127.0.0.1:8502/#demo=slope_broken_rollforward.xlsx`
   - `http://127.0.0.1:8502/#demo=slope_hardcoded_rent.xlsx`
   (`slope_missing_vendor.xlsx` still loads by deep link but is not in the picker: the cached GPT-4.1
   spec misreads its Other row. Don't record it.)
5. macOS: press Cmd+Shift+5, choose "Record Selected Portion" (or the whole screen), and turn on the
   microphone if you are narrating. Windows: Win+G, or any screen recorder. Save as `.mp4`.

## Suggested script (about 4 minutes)

| Time | On screen | Say |
|---|---|---|
| 0:00 | Check page, nothing loaded | "Founders trust spreadsheets with real decisions, and nobody checks the math. Invariant checks a model against the transactions behind it." |
| 0:20 | Clean file → **Run check** | Nothing is highlighted. "5 checks, 0 violations, Verified in Lean 4. The checker is proved correct: it accepts exactly the models that satisfy the math." Point at the **Spec: LLM** badge. |
| 0:50 | Duplicate-row file → **Run check** | One red cell. The ending-cash tile shows claimed vs verified. "A few hundred dollars off, the kind of mistake an eye misses." |
| 1:10 | Click the red cell (SaaS · Jun) | Read the explanation. Point at expected vs actual and at the note that the same charge appears on two Ramp rows. |
| 1:40 | Proposed fix | "The fix isn't a guess. It was applied to a copy, recalculated and re-checked: the violation is gone and no new one appeared." Click **Apply verified fix**. The cell clears and the toast reads "Re-verified: 0 new violations". |
| 2:20 | Broken-rollforward file | Hover the dotted typed-number cell (Starting Cash, Feb) to show it is a number, not a formula. Click it, then **Apply verified fix**. |
| 2:50 | Hardcoded-rent file | The Rent row is pink: "The model wasn't confident what this row should contain, so it says so instead of guessing." Click a pink cell. |
| 3:10 | **What's proven** | Read one "proven" line and the "not verified" lines. "We say plainly what the proof does not cover." |
| 3:30 | **Eval** | "105 planted bugs, all detected; 84% localized to the exact cell; 0 false positives on 20 clean models." Point at the "synthetic suite" note and the known miss. |

## Tips

- Keep one idea per shot and let the page sit for a second after each click.
- If a take goes wrong, reload the page (or use a deep link) and carry on; trim in editing.
- Live runs on your own workbook call a model and take a few seconds longer; stay on the demo files on
  camera unless a key and credits are set up.
- Export as H.264 `.mp4`, 1080p, and watch it once with the sound on before sharing. Save it as
  `demo/backup.mp4`.
