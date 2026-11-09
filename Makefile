.PHONY: test lean eval demo

test:
	uv run pytest

lean:
	cd lean && lake build
	@! grep -rnE "\bsorry\b|\badmit\b|^axiom " lean/ProofCheck lean/Main.lean lean/ProofCheck.lean
	cd lean && lake env lean AxiomsCheck.lean

eval:
	@echo "eval: not implemented yet (A6)"

demo:
	@echo "demo: not implemented yet (A8)"
