"""LLM proposes a fix as structured output (owner: A7).

The model sees the violation, its explanation and the involved row formulas and
returns a `Proposal` through a forced-shape tool. It never computes numbers:
proposals are formulas (or a duplicate-row deletion) that `verify` applies to a
copy and re-checks. Responses are cached on disk by input hash, like A5's.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from proof.fix.explain import Analysis, row_cells
from proof.fix.report import Attempt
from proof.schemas import Violation
from proof.spec.agent import AgentError, DEFAULT_MODEL, default_client

TOOL_NAME = "propose_fix"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "propose.md").read_text()

# A proposer maps a request dict to the raw tool input (a dict).
Proposer = Callable[[dict[str, Any]], dict[str, Any]]


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Propose one fix to the workbook.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["edit_formula", "delete_duplicate_row"]},
                "scope": {"type": "string", "enum": ["cell", "row"]},
                "cell_ref": {"type": "string",
                             "description": "Runway cell, e.g. Runway!D5 (edit_formula)"},
                "old_formula": {"type": "string",
                                "description": "current content of that cell, as shown"},
                "new_formula": {"type": "string", "description": "starts with ="},
                "sheet": {"type": "string", "description": "export tab (delete_duplicate_row)"},
                "row": {"type": "integer", "description": "1-based row to delete"},
                "reason": {"type": "string"},
            },
            "required": ["kind", "reason"],
        },
    }


def involved_rows(an: Analysis, ctx: dict[str, Any]) -> list[str]:
    if ctx.get("row_label"):
        return [ctx["row_label"]]
    if ctx.get("rows_involved"):
        return list(ctx["rows_involved"])
    return [s.row_label for s in an.spec.lines if s.kind in ("inflow", "outflow")]


def build_request(an: Analysis, v: Violation, explanation: str, ctx: dict[str, Any],
                  attempts: list[Attempt]) -> dict[str, Any]:
    rows = []
    for label in involved_rows(an, ctx):
        rows.append({
            "row": label, "note": an.notes.get(label, ""),
            "cells": [{"ref": c.ref, "formula": c.formula or f"(typed) {c.value_cents / 100:g}"}
                      for c in row_cells(an, label)]})
    return {
        "violation": v.model_dump(mode="json"),
        "explanation": explanation,
        "matched_txns": ctx.get("matched_txns", []),
        "problem_txns": ctx.get("problem_txns", []),
        "duplicate_export_rows": ctx.get("duplicate_rows", []) or
        [{"id": i, "rows": r} for i, r in an.duplicates.items()],
        "runway_rows": rows,
        "current_rules": [
            {"row": s.row_label, "rule": s.rule.model_dump(mode="json")}
            for s in an.spec.lines if s.rule is not None],
        "previous_attempts": [
            {"proposal": a.proposal.model_dump(mode="json") if a.proposal else None,
             "outcome": a.outcome, "why": a.detail} for a in attempts],
    }


class LLMProposer:
    """Callable proposer backed by the Anthropic API (injectable client)."""

    def __init__(self, client: Any | None = None, model: str | None = None,
                 cache_dir: str | Path | None = None, use_cache: bool = True):
        self.client, self.use_cache = client, use_cache
        self.model = model or os.environ.get("PROOF_MODEL", DEFAULT_MODEL)
        self.cache_dir = cache_dir
        self.calls = 0  # live API calls made (cache hits don't count)

    def _cache(self, key: str) -> Path:
        d = Path(self.cache_dir or os.environ.get("PROOF_CACHE_DIR", ".proof_cache")) / "fix"
        return d / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        user = json.dumps(request, indent=1, sort_keys=True)
        path = self._cache(json.dumps([self.model, SYSTEM_PROMPT, tool_schema(), user]))
        if self.use_cache and path.exists():
            try:
                return json.loads(path.read_text())
            except ValueError:
                pass
        client = self.client or default_client()
        self.client = client
        self.calls += 1
        response = client.messages.create(
            model=self.model, max_tokens=2048, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            tools=[tool_schema()], tool_choice={"type": "auto"})
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                data = dict(block.input)
                if self.use_cache:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(data, sort_keys=True, indent=1))
                return data
        raise AgentError(f"model did not call {TOOL_NAME}")
