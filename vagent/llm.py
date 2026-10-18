"""Provider-neutral LLM client with an on-disk response cache and cost accounting.

The agent speaks one message format (Anthropic-style content blocks:
``text``, ``tool_use``, ``tool_result``). Each backend translates to and from
its own API, so ``agent.py`` is identical for every provider. The backend is
chosen from the model name: ``gemini-*`` -> Gemini, ``claude-*`` -> Claude.

Every request is keyed by a hash of its full parameters plus a ``sample``
index, so re-running an experiment replays identical responses for free and
independent samples (for pass@k) get distinct cache entries.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .dataset import ROOT

load_dotenv(ROOT / ".env")

CACHE_DIR = ROOT / ".cache" / "llm"

# USD per million tokens: (input, output incl. thinking, cache read).
# Gemini prices are the promotional rates in effect October 2026 (gemini-4-argon
# doubles on 2026-12-01, gemini-3.8-flash on 2027-01-01).
PRICES = {
    "gemini-4-argon": (2.00, 10.00, 0.10),
    "gemini-3.8-flash": (0.75, 3.75, 0.075),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "claude-opus-5-5": (4.00, 20.00, 0.40),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
CACHE_WRITE_MULTIPLIER = 1.25  # Claude bills explicit cache writes at 1.25x input


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0  # includes thinking tokens
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    cached_calls: int = 0  # served from local disk cache (cost still counted)

    def add(self, other: "Usage") -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class Reply:
    # Content blocks as plain dicts, safe to append to history unchanged. A
    # ``provider_raw`` block may carry the provider's native parts (e.g. Gemini
    # thought signatures) so they can be replayed verbatim on the next turn.
    content: list[dict]
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal
    usage: Usage = field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b["type"] == "text")

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b["type"] == "tool_use"]


class LLM:
    """Caching + accounting. Subclasses implement ``_request``."""

    provider = ""

    def __init__(self, model: str, effort: str = "medium", max_tokens: int = 16000):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = None
        self._lock = threading.Lock()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def call(self, *, system: str, messages: list[dict], tools: list[dict] | None = None, sample: int = 0) -> Reply:
        req = {"provider": self.provider, "model": self.model, "effort": self.effort, "max_tokens": self.max_tokens,
               "system": system, "messages": messages, "tools": tools or [], "_sample": sample}
        key = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            data, cached = json.loads(path.read_text()), True
        else:
            data, cached = self._request(system, messages, tools or []), False
            path.write_text(json.dumps(data))

        u = data["usage"]
        p_in, p_out, p_cache = PRICES.get(self.model, (0.0, 0.0, 0.0))
        usage = Usage(calls=1, cached_calls=int(cached), **u)
        usage.cost_usd = (
            u["input_tokens"] * p_in
            + u["cache_write_tokens"] * p_in * CACHE_WRITE_MULTIPLIER
            + u["cache_read_tokens"] * p_cache
            + u["output_tokens"] * p_out
        ) / 1e6
        return Reply(content=data["content"], stop_reason=data["stop_reason"], usage=usage)

    def _request(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """Return {"content": [...], "stop_reason": str, "usage": {input_tokens, output_tokens,
        cache_read_tokens, cache_write_tokens}}."""
        raise NotImplementedError


# --------------------------------------------------------------------------- Gemini

class GeminiLLM(LLM):
    provider = "gemini"
    REFUSAL_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}

    @property
    def client(self):
        from google import genai
        from google.genai import types

        with self._lock:
            if self._client is None:
                if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
                    raise SystemExit("GEMINI_API_KEY is not set. Add it to .env (see .env.example).")
                # Generous retries: a full benchmark run will hit rate limits.
                retry = types.HttpRetryOptions(attempts=8, initial_delay=2.0, max_delay=60.0,
                                               http_status_codes=[429, 500, 502, 503, 504])
                self._client = genai.Client(http_options=types.HttpOptions(retry_options=retry))
            return self._client

    @staticmethod
    def to_contents(messages: list[dict]) -> list:
        """Translate the agent's message history into Gemini ``Content`` objects."""
        from google.genai import types

        names: dict[str, str] = {}  # tool_use_id -> function name (Gemini responses are matched by name)
        contents = []
        for m in messages:
            blocks = m["content"]
            if m["role"] == "assistant":
                for b in blocks:
                    if b["type"] == "tool_use":
                        names[b["id"]] = b["name"]
                raw = next((b for b in blocks if b["type"] == "provider_raw"), None)
                if raw is not None:  # replay verbatim to preserve thought signatures
                    parts = [types.Part.model_validate(p) for p in raw["parts"]]
                else:
                    parts = [types.Part(text=b["text"]) if b["type"] == "text" else
                             types.Part(function_call=types.FunctionCall(name=b["name"], args=b["input"]))
                             for b in blocks if b["type"] in ("text", "tool_use")]
                contents.append(types.Content(role="model", parts=parts))
            elif isinstance(blocks, str):
                contents.append(types.Content(role="user", parts=[types.Part(text=blocks)]))
            else:
                parts = []
                for b in blocks:
                    if b["type"] == "tool_result":
                        key = "error" if b.get("is_error") else "output"
                        part = types.Part.from_function_response(name=names[b["tool_use_id"]], response={key: b["content"]})
                        if not b["tool_use_id"].startswith("call_"):  # echo provider-issued ids
                            part.function_response.id = b["tool_use_id"]
                        parts.append(part)
                    elif b["type"] == "text":
                        parts.append(types.Part(text=b["text"]))
                contents.append(types.Content(role="user", parts=parts))
        return contents

    @staticmethod
    def to_declarations(tools: list[dict]) -> list:
        from google.genai import types

        def clean(schema: dict) -> dict:
            return {k: v for k, v in schema.items() if k != "additionalProperties"}

        return [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=clean(t["input_schema"])) for t in tools]

    @classmethod
    def from_response(cls, resp, turn_index: int) -> dict:
        """Translate a Gemini response into the agent's content-block format."""
        cand = resp.candidates[0] if resp.candidates else None
        parts = list(cand.content.parts or []) if cand and cand.content else []
        content: list[dict] = []
        for i, p in enumerate(parts):
            if p.function_call:
                fc = p.function_call
                content.append({"type": "tool_use", "id": fc.id or f"call_{turn_index}_{i}",
                                "name": fc.name, "input": dict(fc.args or {})})
            elif p.text and not p.thought:
                content.append({"type": "text", "text": p.text})
        if parts:
            content.append({"type": "provider_raw", "provider": "gemini",
                            "parts": [p.model_dump(mode="json", exclude_none=True) for p in parts]})

        finish = cand.finish_reason.name if cand and cand.finish_reason else "OTHER"
        if any(b["type"] == "tool_use" for b in content):
            stop = "tool_use"
        elif not cand or finish in cls.REFUSAL_REASONS:
            stop = "refusal"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        else:
            stop = "end_turn"

        um = resp.usage_metadata
        cached = (um.cached_content_token_count or 0) if um else 0
        prompt = (um.prompt_token_count or 0) if um else 0
        return {
            "content": content,
            "stop_reason": stop,
            "finish_reason": finish,
            "usage": {
                "input_tokens": prompt - cached,
                "output_tokens": ((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0,
                "cache_read_tokens": cached,
                "cache_write_tokens": 0,  # Gemini implicit caching has no write charge
            },
        }

    def _request(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        from google.genai import types

        level = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}.get(self.effort)
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=self.max_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=level) if level else None,
            tools=[types.Tool(function_declarations=self.to_declarations(tools))] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        turn = sum(m["role"] == "assistant" for m in messages)
        resp = self.client.models.generate_content(model=self.model, contents=self.to_contents(messages), config=config)
        return self.from_response(resp, turn)


# --------------------------------------------------------------------------- Claude

class ClaudeLLM(LLM):
    provider = "anthropic"

    @property
    def client(self):
        import anthropic

        with self._lock:
            if self._client is None:
                self._client = anthropic.Anthropic(max_retries=8)
            return self._client

    def _request(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        params: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            # Thinking is adaptive by default; effort is the depth control.
            "output_config": {"effort": self.effort},
            # Multi-turn agent loops resend a growing prefix: cache it.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = tools
        resp = self.client.messages.create(**params)
        u = resp.usage
        return {
            "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content],
            "stop_reason": resp.stop_reason,
            "usage": {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_read_tokens": u.cache_read_input_tokens or 0,
                "cache_write_tokens": u.cache_creation_input_tokens or 0,
            },
        }


def make_llm(model: str, effort: str = "medium") -> LLM:
    if model.startswith("gemini"):
        return GeminiLLM(model, effort)
    if model.startswith("claude"):
        return ClaudeLLM(model, effort)
    raise SystemExit(f"Unknown model family for {model!r} (expected gemini-* or claude-*)")
