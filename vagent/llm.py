"""Thin Claude client with an on-disk response cache and cost accounting.

Every request is keyed by a hash of its full parameters plus a ``sample``
index, so re-running an experiment replays identical responses for free and
independent samples (for pass@k) get distinct cache entries.

Sampling parameters (temperature, top_p) are rejected by current Claude models,
so run-to-run variance is handled by caching and by reporting pass@k over
multiple samples rather than by fixing a seed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from .dataset import ROOT

load_dotenv(ROOT / ".env")

CACHE_DIR = ROOT / ".cache" / "llm"

# USD per million tokens: (input, output). Cache writes bill at 1.25x input,
# cache reads at 0.1x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
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
    content: list[dict]  # content blocks as plain dicts, safe to append to history unchanged
    stop_reason: str
    usage: Usage = field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b["type"] == "text")

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b["type"] == "tool_use"]


def _cost(model: str, u: dict) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return (
        u.get("input_tokens", 0) * p_in
        + (u.get("cache_creation_input_tokens") or 0) * p_in * 1.25
        + (u.get("cache_read_input_tokens") or 0) * p_in * 0.10
        + u.get("output_tokens", 0) * p_out
    ) / 1e6


class LLM:
    def __init__(self, model: str, effort: str = "medium", max_tokens: int = 16000):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client: anthropic.Anthropic | None = None
        self._lock = threading.Lock()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def client(self) -> anthropic.Anthropic:
        with self._lock:
            if self._client is None:
                # Generous retries: a full benchmark run will hit rate limits.
                self._client = anthropic.Anthropic(max_retries=8)
            return self._client

    def call(self, *, system: str, messages: list[dict], tools: list[dict] | None = None, sample: int = 0) -> Reply:
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

        key = hashlib.sha256(json.dumps({**params, "_sample": sample}, sort_keys=True).encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text())
            cached = True
        else:
            resp = self.client.messages.create(**params)
            data = {
                "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content],
                "stop_reason": resp.stop_reason,
                "usage": resp.usage.model_dump(mode="json", exclude_none=True),
            }
            path.write_text(json.dumps(data))
            cached = False

        u = data["usage"]
        usage = Usage(
            calls=1,
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cache_read_tokens=u.get("cache_read_input_tokens") or 0,
            cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
            cost_usd=_cost(self.model, u),
            cached_calls=int(cached),
        )
        return Reply(content=data["content"], stop_reason=data["stop_reason"], usage=usage)
