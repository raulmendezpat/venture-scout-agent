"""Shared configuration helpers and the Claude API wrapper (same wrapper as job-scout-agent)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(rel: str) -> dict:
    with open(ROOT / rel, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_config() -> dict:
    return load_yaml("config.yaml")


def load_sources() -> dict:
    return load_yaml("sources.yaml")


def load_founder() -> dict:
    return load_yaml("profile/founder.yaml")


def env(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if required and not value:
        raise RuntimeError(f"Missing environment variable {name}")
    return value


class LLM:
    """Thin wrapper over the Claude Messages API that always returns structured JSON.

    Structured output is obtained by forcing a single tool call whose input_schema is the
    desired JSON schema. Pass `fake` (a callable) in tests to avoid network calls.
    """

    def __init__(self, fake: Callable[..., dict] | None = None, client: Any = None):
        self.fake = fake
        self.client = client
        self._auto_only: set[str] = set()   # models that reject a forced tool_choice
        self.usage: dict[str, dict[str, int]] = {}   # per model: tokens and web searches, for the cost line
        if fake is None and client is None:
            import anthropic
            self.client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))

    def _track(self, model: str, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        t = self.usage.setdefault(model, {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0,
                                          "web_searches": 0})
        t["calls"] += 1
        t["input"] += getattr(u, "input_tokens", 0) or 0
        t["output"] += getattr(u, "output_tokens", 0) or 0
        t["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        t["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        t["web_searches"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0

    def cost(self, pricing: dict) -> dict:
        """Cost of this run from the tracked usage. pricing: USD per million tokens per model, plus
        web_search_usd_per_1000 and usd_to_aud."""
        per_model, usd = {}, 0.0
        for model, t in self.usage.items():
            p = pricing.get("models", {}).get(model, {"input": 0, "output": 0})
            c = (t["input"] * p["input"] + t["output"] * p["output"] + t["cache_write"] * p["input"] * 1.25
                 + t["cache_read"] * p["input"] * 0.1) / 1e6 + t["web_searches"] * pricing.get("web_search_usd_per_1000", 10) / 1000
            per_model[model] = {**t, "usd": round(c, 4)}
            usd += c
        return {"usd": round(usd, 3), "aud": round(usd * pricing.get("usd_to_aud", 1.0), 3), "per_model": per_model}

    @staticmethod
    def _rejects_forced_tool(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) == 400 and "tool_choice" in str(exc)

    def structured(self, *, model: str, system: str, user: str, schema: dict, name: str,
                   max_tokens: int = 4000, server_tools: list | None = None) -> dict:
        if self.fake is not None:
            return self.fake(name=name, system=system, user=user, schema=schema)

        result_tool = {"name": name, "description": "Return the final result.", "input_schema": schema}
        tools = list(server_tools or []) + [result_tool]
        system = f"{system}\n\nAlways finish by calling the {name} tool exactly once with your final answer."
        messages = [{"role": "user", "content": user}]
        forced, auto = {"type": "tool", "name": name}, {"type": "auto"}
        choice = forced if (not server_tools and model not in self._auto_only) else auto
        for _ in range(10):  # server tools (web search) may pause; continue a few times
            try:
                resp = self.client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                                   tools=tools, tool_choice=choice, messages=messages)
            except Exception as exc:
                # Some models (for example with built-in extended thinking) only accept tool_choice auto.
                if choice is forced and self._rejects_forced_tool(exc):
                    self._auto_only.add(model)
                    choice = auto
                    continue
                raise
            self._track(model, resp)
            missing: list[str] = []
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use" and block.name == name:
                    result = dict(block.input)
                    missing = [k for k in schema.get("required", []) if k not in result]
                    if not missing:
                        return result
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "pause_turn":
                continue
            if missing:
                # The tool call must be answered before the next user turn.
                tool_id = next(b.id for b in resp.content if getattr(b, "type", "") == "tool_use" and b.name == name)
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_id, "is_error": True,
                     "content": f"Incomplete: missing required fields {missing}. Call {name} again with ALL fields."}]})
            else:
                messages.append({"role": "user", "content": f"Now call the {name} tool with your final answer."})
            choice = forced if model not in self._auto_only else auto
        raise RuntimeError(f"Model did not return {name}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
