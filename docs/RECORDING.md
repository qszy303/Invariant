# Recording a demo

A ~4 minute screen recording of the app. It works offline on the sample workbooks, so no API key or
Wi-Fi is needed, and it behaves the same every take.

## Before you record

1. `make app` and open <http://127.0.0.1:8501> in Chrome. Use a fresh window, 100% zoom (use
   110-125% if it will be shown on a projector), full screen, bookmarks bar hidden.
2. Quit chat, mail and notification apps, and turn on Do Not Disturb.
3. Leave **Settings** closed. Never show an API key on camera.
4. Practice once. Deep links jump straight to a state, and a reload resets it:
   - `http://127.0.0.1:8501/#sample=slope_clean.xlsx`
   - `http://127.0.0.1:8501/#sample=slope_dup_row.xlsx`
   - `http://127.0.0.1:8501/#sample=slope_missing_vendor.xlsx`
   - `http://127.0.0.1:8501/#sample=slope_broken_rollforward.xlsx`
5. macOS: press Cmd+Shift+5, choose "Record Selected Portion" (or the whole screen), and turn on the
   microphone if you are narrating. Windows: Win+G, or any screen recorder. Save as `.mp4`.

## Suggested script (about 4 minutes)

| Time | On screen | Say |
|---|---|---|
| 0:00 | Start page | "Founders trust spreadsheets with real decisions, and nobody checks the math. Invariant checks a model against the transactions behind it." |
| 0:20 | Clean sample | Everything green. "5 checks, 0 violations, verified by Lean. The checker is proved correct: it accepts exactly the models that satisfy the math." |
| 0:50 | Duplicate-row sample | Red cell. "One cell is wrong, and this is the kind of mistake a human eye misses." |
| 1:10 | Click the red cell | Read the explanation. Point at the two numbers (sheet vs transactions) and at the repeated Ramp row. |
| 1:40 | Proposed fix | "The fix isn't a guess. It was applied to a copy, recalculated and re-checked: the violation is gone and no new one appeared." Click **Apply verified fix**. The bar turns green. |
| 2:10 | Missing-vendor sample | "Five violations, one cause." Show the list, then click **Apply all verified fixes (1)**. |
| 2:40 | Broken-rollforward sample | Point at the dotted typed-number cell. Click the cell to show the formula. Apply the fix. |
| 3:10 | **What's proven** | Read one "proven" line and the "not proven" lines. "We say plainly what the proof does not cover." |
| 3:40 | Close | Show `eval/RESULTS.md`: detection, false positives, Python-vs-Lean agreement, known misses. "And here are the misses." |

## Tips

- Keep one idea per shot and let the page sit for a second after each click.
- If a take goes wrong, reload the page (or use a deep link) and carry on; trim in editing.
- Only the samples demo cleanly without a key. **Find verified fixes** on your own workbook calls the
  model; avoid it on camera unless a key and credits are set up.
- Export as H.264 `.mp4`, 1080p, and watch it once with the sound on before sharing.
