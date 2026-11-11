.PHONY: test lean eval demo samples-build demo-workbook screens

test:
	uv run pytest

lean:
	cd lean && lake build
	@! grep -rnE "\bsorry\b|\badmit\b|^axiom " lean/ProofCheck lean/Main.lean lean/ProofCheck.lean
	cd lean && lake env lean AxiomsCheck.lean

eval:
	uv run python -m eval.run
	uv run python -m eval.fix_run

demo:  ## the web app on http://127.0.0.1:8502 (FastAPI + one static page); PROOF_DEMO=1 = cached, offline
	uv run python -m app.v2.server --open

screens:  ## Playwright screenshots of every page at 1920x1080 -> demo/screens/
	PROOF_DEMO=1 uv run python scripts/ui_screens.py

demo-workbook:  ## rebuild samples/fernway_dropped_month.xlsx (the larger demo company, one planted bug)
	uv run python -m scripts.build_demo_workbook

samples-build:  ## regenerate samples/ (workbooks, caches, precomputed fixes)
	uv run python -m app.build_samples
