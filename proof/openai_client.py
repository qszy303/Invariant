"""Anthropic-shaped client over OpenAI, so the spec agent can run with only an OpenAI key.

`proof.spec.agent.build_rulespec_llm` accepts any object with `client.messages.create(...)`.
This one translates that call (system prompt, one forced-shape tool, Anthropic-style
tool_use / tool_result messages) to OpenAI Chat Completions function calling and returns
objects with the same shape the agent reads (`response.content` blocks with `type`,
`id`, `name`, `input`). It adds a file; the agent itself is untouched.

Key: OPENAI_API_KEY or OPEN_AI_API_KEY. Model: pass `model=` to the agent, or set
PROOF_MODEL (the agent's own switch); `default_model()` gives the OpenAI default.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace as NS
from typing import Any

DEFAULT_OPENAI_MODEL = "gpt-4.1"


def openai_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_AI_API_KEY") or None


def default_model() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""),
        "parameters": t["input_schema"]}} for t in tools or []] or None


def _blocks(content: Any) -> list[Any]:
    return content if isinstance(content, list) else []


def _messages(system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        c = m["content"]
        if m["role"] == "assistant" and isinstance(c, list):
            text = "".join(getattr(b, "text", "") for b in c if getattr(b, "type", "") == "text")
            calls = [{"id": b.id, "type": "function",
                      "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                     for b in c if getattr(b, "type", "") == "tool_use"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif m["role"] == "user" and isinstance(c, list):
            for b in c:  # tool_result blocks -> tool messages
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": str(b.get("content", ""))})
                elif isinstance(b, dict) and b.get("type") == "text":
                    out.append({"role": "user", "content": b["text"]})
        else:
            out.append({"role": m["role"], "content": c})
    return out


class _Messages:
    def __init__(self, client: Any):
        self._c = client

    def create(self, *, model: str, max_tokens: int, system: str | None = None,
               messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               tool_choice: Any = None, **_: Any):
        kw: dict[str, Any] = {}
        oa_tools = _tools(tools)
        if oa_tools:
            kw["tools"] = oa_tools
        resp = self._c.chat.completions.create(
            model=model, max_completion_tokens=max_tokens,
            messages=_messages(system, messages), **kw)
        msg = resp.choices[0].message
        content: list[Any] = []
        if msg.content:
            content.append(NS(type="text", text=msg.content))
        for call in msg.tool_calls or []:
            try:
                args = json.loads(call.function.arguments)
            except ValueError:
                args = {}
            content.append(NS(type="tool_use", id=call.id, name=call.function.name, input=args))
        return NS(content=content)


class OpenAIAsAnthropic:
    """`client.messages.create(...)` backed by OpenAI."""

    def __init__(self, api_key: str | None = None, client: Any | None = None):
        if client is None:
            key = api_key or openai_key()
            if not key:
                raise RuntimeError("no OpenAI key set (OPENAI_API_KEY or OPEN_AI_API_KEY)")
            from openai import OpenAI

            client = OpenAI(api_key=key)
        self.messages = _Messages(client)
