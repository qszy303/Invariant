"""Drive the v2 UI with Playwright: acceptance flow x3 plus a screenshot of every page.

    PROOF_DEMO=1 uv run python scripts/ui_screens.py

Blocks every non-local request (so the page must work with Wi-Fi off), asserts the flow
gives identical output on each repeat, and writes PNGs to demo/screens/.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "screens"
PORT = 8599
URL = f"http://127.0.0.1:{PORT}"


def wait_up() -> None:
    for _ in range(100):
        try:
            urllib.request.urlopen(URL + "/api/demo-files", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("server did not start")


def snapshot(page) -> str:
    return page.evaluate("""() => JSON.stringify({
      kpis: [...document.querySelectorAll('#kpis .kpi')].map(e => e.innerText),
      cells: [...document.querySelectorAll('#grid td.cell')].map(e => e.className + '|' + e.innerText),
      badge: document.getElementById('checker-badge').innerText })""")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_AI_API_KEY")}
    env["PROOF_DEMO"] = "1"  # and no keys: proves the demo runs from demo/cache alone
    srv = subprocess.Popen([sys.executable, "-m", "app.v2.server", "--port", str(PORT)],
                           cwd=ROOT, env=env)
    blocked: list[str] = []
    try:
        wait_up()
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            ctx = br.new_context(viewport={"width": 1920, "height": 1080})
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(URL)
                      else (blocked.append(r.request.url), r.abort())[1])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))

            page.goto(URL + "/#/check")
            page.wait_for_selector("#demo-select option", state="attached")
            page.screenshot(path=OUT / "0_check_empty.png")

            snaps = []
            for i in range(3):
                page.goto(URL + "/#/check")
                page.reload()
                page.wait_for_selector("#demo-select option", state="attached")
                page.select_option("#demo-select", "slope_dup_row.xlsx")
                t0 = time.perf_counter()
                page.click("#run-btn")
                page.wait_for_selector("#grid td.violated")
                took = time.perf_counter() - t0
                assert took < 3, f"check took {took:.2f}s"
                snaps.append(snapshot(page))
                if i == 0:
                    page.screenshot(path=OUT / "1_check_result.png")
                page.click("#grid td.violated")
                page.wait_for_selector("body.drawer-open")
                page.wait_for_timeout(400)
                if i == 0:
                    page.screenshot(path=OUT / "2_check_drawer.png")
                page.click("#apply-btn")
                page.wait_for_selector(".toast")
                page.wait_for_selector("#grid td.violated", state="detached")
                toast = page.inner_text(".toast")
                assert "Re-verified: 0 new violations" in toast, toast
                snaps.append(snapshot(page))
                if i == 0:
                    page.screenshot(path=OUT / "3_check_fixed.png")
                print(f"run {i + 1}: check {took:.2f}s, {toast!r}")
            assert snaps[0::2] == [snaps[0]] * 3 and snaps[1::2] == [snaps[1]] * 3, \
                "output differed between repeats"
            print("3 repeats identical")

            for name in ("slope_missing_vendor.xlsx", "slope_broken_rollforward.xlsx"):
                if name == "slope_missing_vendor.xlsx":  # hidden from the picker: deep link
                    page.goto(URL + f"/#demo={name}")
                    page.reload()
                else:
                    page.goto(URL + "/#/check")
                    page.reload()
                    page.wait_for_selector("#demo-select option", state="attached")
                    page.select_option("#demo-select", name)
                    page.click("#run-btn")
                page.wait_for_selector("#grid td.cell")
                page.wait_for_timeout(300)
                page.screenshot(path=OUT / f"4_{name.split('.')[0]}.png")
                if name.endswith("rollforward.xlsx"):
                    page.click("#grid td.violated")
                    page.wait_for_timeout(400)
                    page.screenshot(path=OUT / "5_rollforward_drawer.png")
                else:
                    page.click(".chip-btn")
                    page.wait_for_timeout(400)
                    page.screenshot(path=OUT / "5_missing_vendor_drawer.png")

            page.select_option("#demo-select", "slope_hardcoded_rent.xlsx")
            page.click("#run-btn")
            page.wait_for_selector("#grid td.review")
            page.wait_for_timeout(300)
            page.screenshot(path=OUT / "8_needs_review.png")
            page.click("#grid td.review >> nth=0")
            page.wait_for_timeout(400)
            page.screenshot(path=OUT / "9_needs_review_drawer.png")
            page.keyboard.press("Escape")
            page.goto(URL + "/#/eval")
            page.wait_for_selector("#eval-chart svg")
            page.wait_for_timeout(300)
            page.screenshot(path=OUT / "6_eval.png")
            page.goto(URL + "/#/proven")
            page.wait_for_selector("#proven-list li")
            page.wait_for_timeout(300)
            page.screenshot(path=OUT / "7_proven.png")
            br.close()
        assert not blocked, f"page tried to reach the network: {blocked}"
        assert not errors, errors
        print("no external requests; screenshots in", OUT.relative_to(ROOT))
        return 0
    finally:
        srv.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
