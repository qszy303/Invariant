"""Fix proposer backed by OpenAI (function calling), for use when only an OpenAI key is set.

Same contract as `proof.fix.propose.LLMProposer`: a callable that maps a request dict
to the raw tool input (a dict). It reuses that module's system prompt, request builder
and tool schema, so `verify.fix_workbook` applies and re-checks whatever it returns the
same way: the model never computes numbers, and nothing is shown as verified unless the
re-check passes. Nothing under proof/ is modified.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from proof.fix.propose import SYSTEM_PROMPT, TOOL_NAME, tool_schema
from proof.openai_client import DEFAULT_OPENAI_MODEL, openai_key


class OpenAIProposer:
    def __init__(self, client: Any | None = None, model: str | None = None,
                 cache_dir: str | Path | None = None, use_cache: bool = True):
        self.client, self.use_cache = client, use_cache
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.cache_dir = cache_dir
        self.calls = 0

    def _cache(self, key: str) -> Path:
        d = Path(self.cache_dir or os.environ.get("PROOF_CACHE_DIR", ".proof_cache")) / "fix_openai"
        return d / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _tool(self) -> dict[str, Any]:
        t = tool_schema()
        return {"type": "function",
                "function": {"name": t["name"], "description": t["description"],
                             "parameters": t["input_schema"]}}

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        user = json.dumps(request, indent=1, sort_keys=True)
        path = self._cache(json.dumps([self.model, SYSTEM_PROMPT, self._tool(), user]))
        if self.use_cache and path.exists():
            try:
                return json.loads(path.read_text())
            except ValueError:
                pass
        if self.client is None:
            key = openai_key()
            if not key:
                raise RuntimeError("no OpenAI key set (OPENAI_API_KEY or OPEN_AI_API_KEY)")
            from openai import OpenAI

            self.client = OpenAI(api_key=key)
        self.calls += 1
        resp = self.client.chat.completions.create(
            model=self.model, max_completion_tokens=2048,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user}],
            tools=[self._tool()],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}})
        for call in resp.choices[0].message.tool_calls or []:
            if call.function.name == TOOL_NAME:
                data = json.loads(call.function.arguments)
                if self.use_cache:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(data, sort_keys=True, indent=1))
                return data
        raise RuntimeError(f"model did not call {TOOL_NAME}")
