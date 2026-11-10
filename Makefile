.PHONY: test lean eval app demo samples-build

test:
	uv run pytest

lean:
	cd lean && lake build
	@! grep -rnE "\bsorry\b|\badmit\b|^axiom " lean/ProofCheck lean/Main.lean lean/ProofCheck.lean
	cd lean && lake env lean AxiomsCheck.lean

eval:
	uv run python -m eval.run
	uv run python -m eval.fix_run

app:  ## the Invariant web app on http://127.0.0.1:8501 (ANTHROPIC_API_KEY for new fix proposals)
	uv run python -m app.server --open

demo: app  ## alias

samples-build:  ## regenerate samples/ (workbooks, caches, precomputed fixes)
	uv run python -m app.build_samples
