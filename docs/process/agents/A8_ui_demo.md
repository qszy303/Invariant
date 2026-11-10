# A8: UI + demo mode

Owns: `app/`. Read `CONTRACTS.md` and `PLAN.md` (demo flow) first.

## Goal
A clean, fast UI for a ~4 minute live demo, plus an offline demo mode that cannot fail on stage.

## Do
1. Streamlit app (`make demo`). Flow:
   - Upload `.xlsx` (or pick a preloaded demo file).
   - Show the Runway grid as a table that looks like the spreadsheet. Cells color-coded: green verified, red violated, amber needs-review (low-confidence spec).
   - Summary bar: "4 checks · 3 violations · verified by Lean ✓" (show which checker ran).
   - Click a red cell → side panel: explanation, the contradicting transactions table, proposed fix (old → new formula), button **Apply verified fix** → re-runs → cell turns green.
   - A small "What's proven" expander linking to `lean/README.md` content and showing one theorem statement.
2. Demo mode (`PROOF_DEMO=1`): uses cached LLM responses and precomputed results so it runs with no network and identical output every time.
3. Performance: full check on the demo file under 3 seconds with cache.
4. Visual polish: one accent color, readable at projector size (min 16px body), no Streamlit default clutter (hide menu/footer). Dark text on light background for the projector.

## Acceptance
- Full demo flow works with Wi-Fi off.
- Screen-record the flow once and save as `demo/backup.mp4` (Sophie will re-record the final version).

## Don't
- Add features beyond the demo flow. No auth, accounts, or real integrations.
