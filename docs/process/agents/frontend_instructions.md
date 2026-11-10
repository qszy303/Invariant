Rebuild the Proof UI. The current Streamlit app looks bad. Replace it with a
FastAPI backend + one hand-written HTML/CSS/JS page. Keep the existing
Streamlit app untouched as a fallback until the new UI passes acceptance.
Read CONTRACTS.md and agents/A8_ui_demo.md first. Do not change schemas or
any checker/pipeline code; only call it.

HARD CONSTRAINT: the demo must work with Wi-Fi off. No CDNs, no build step.
Vanilla JS, one CSS file, fonts bundled locally in app/static/fonts
(DM Sans + JetBrains Mono, woff2) with system-ui / ui-monospace fallbacks.

BACKEND (app/server.py, run by `make demo` via uvicorn):
- POST /api/check  {demo_file} or multipart upload -> {cellgraph, result,
  needs_review: [line_ids], explanations, fixes, checker: "lean"|"python"}
- POST /api/fix/apply {violation_key} -> re-run pipeline on a COPY of the
  workbook; return new result + {resolved: bool, new_violations: int}
- GET /api/eval   -> eval/results.json
- GET /api/proven -> theorem statements + "what's proven / not proven"
  sections from lean/README.md
- Respect PROOF_DEMO=1 (cached LLM responses, deterministic output).

LAYOUT (desktop, designed for a 1920x1080 projector):
1. Left sidebar, 220px: wordmark "Invariant", nav: Check · Eval · What's proven.
2. Top bar: file name, demo-file picker + Upload, "Run check" button, and a
   badge showing which checker ran ("Verified in Lean 4" or "Python").
3. KPI row, 4 tiles: Checks run · Violations · Needs review ·
   Ending cash: claimed vs verified (e.g. "$1.92M claimed → $1.71M verified").
   That last tile is the money shot: make it the widest.
4. Main panel: the Runway sheet as a grid. Row labels left, Jan–Dec columns,
   tabular monospace numbers, right-aligned. Cell states: violation = red
   tint + red left border; needs review = amber tint; OK = no tint (do NOT
   paint everything green). Hover shows the formula.
5. Right detail drawer (slides in on clicking a flagged cell, 420px):
   invariant name as a chip, the plain-English message, expected vs actual
   vs difference, table of the contradicting transactions (date, source,
   counterparty, amount), proposed fix as old → new formula diff, and a
   primary button "Apply verified fix". On click: spinner, re-check, cell
   clears, toast "Re-verified: 0 new violations". Show the fix as verified
   ONLY if the re-check passed.
6. Eval page: horizontal bar chart (hand-drawn SVG, no chart lib) of
   detection rate per bug type, plus false-positive rate and
   Lean/Python agreement as stat tiles. Include the "known misses" list.
7. What's proven page: theorem statements in a code block, then two lists:
   "Proven in Lean" and "Not verified (LLM spec step, xlsx parser, JSON I/O)".

VISUAL SYSTEM (CSS variables in :root):
--bg #F4F4F1; --surface #FFFFFF; --border #E3E3DE; --text #111111;
--muted #5F5F5A; --accent #F2E205 (fills/highlights only, never text on
white); --danger #C8281E; --danger-bg #FDECEA; --warn #A15C07;
--warn-bg #FEF4E2; --ok #0E7A3B.
Body 16px min, KPI numbers 40px bold, headings DM Sans 600, numbers and
formulas JetBrains Mono with font-variant-numeric: tabular-nums.
Cards: white, 1px border, 12px radius, no gradients, no glow, at most one
subtle shadow. 24px grid gutters. Primary button: black bg, white text;
yellow only for the active nav item and the "verified" highlight.

ACCEPTANCE:
- `PROOF_DEMO=1 make demo`, Wi-Fi off: pick demo file → run → 3 red cells →
  click one → drawer → apply fix → re-verified → cell clears. Repeat 3x, same
  output each time.
- Full check renders in under 3s.
- Take a screenshot of each page with Playwright at 1920x1080 and save to
  demo/screens/; review them yourself and fix anything misaligned,
  overflowing, or low-contrast before reporting done.

DON'T: add auth, accounts, dark mode, animations beyond the drawer slide and
the spinner, stock photos, or any feature not listed above.

REFERENCES. Use them only for what's listed:
- agents/reference_pic_1.png (dark sales dashboard): take the STRUCTURE only: left
  sidebar nav, row of KPI tiles, large main panel, right-hand rail.
  Ignore its colors, purple gradients, grid/synthwave background, icons,
  charts, avatars, and chat widget.
- agents/reference_pic_2.png (light "Financial Tracking Platform"): take the VISUAL
  TONE only: off-white background, near-black text, large confident sans
  headings, one bold yellow accent used sparingly, thin black outlines,
  lots of whitespace. Ignore its stock photo, marketing copy, decorative
  circles/connector lines, and nav.
Copy no text, logos, or brand names from either. The VISUAL SYSTEM tokens
below override anything in the images.