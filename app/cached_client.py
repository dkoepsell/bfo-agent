"""app/cached_client.py
=======================
Prompt-caching model layer for bfo-agent (bfo-agent-spec.md §9, MC-1..MC-7).

bfo-agent re-sends the same large stable prefix on every call: system
instructions + the BFO 2020 closed vocabulary + few-shot examples + the
prohibited-construction rules. Only the per-case source text changes. A cache
breakpoint at the end of that prefix means the first call writes it (1.25x base
input) and every later call reads it (0.1x base input) -- up to ~90% off the
repeated portion.

This module is promoted from the reference ``bfo_agent_cached.py`` and adapted
to the app's config (``ANTHROPIC_API_KEY`` / ``ANTHROPIC_MODEL``). It exposes:

  * ``Rates`` / ``RATES`` / ``Usage``   -- the cost model + token accumulator,
  * ``summarize(usage, model, ttl)``    -- the savings math, shared so the
    in-loop ``LLMProposer`` reports the SAME numbers as the batch client,
  * ``CachedAnthropic``                 -- the kernel-as-cached-block client used
    by the batch/standalone path.

The dominant cached block is BFO 2020 (the stable prefix), which is exactly why
caching pays: the invariant context is large and the per-case delta is small.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, LLM_CALL_TIMEOUT_SECONDS

DEFAULT_MODEL = ANTHROPIC_MODEL


# Per-million-token USD rates. These only affect the savings estimate, not
# behavior. Verify against the current pricing page.
@dataclass
class Rates:
    input: float = 3.00          # base input
    cache_read: float = 0.30     # 0.1x base
    cache_write_5m: float = 3.75 # 1.25x base
    cache_write_1h: float = 6.00 # 2.0x base
    output: float = 15.00


RATES = {
    "claude-opus-4-8":           Rates(5.00, 0.50, 6.25, 10.00, 25.00),
    "claude-opus-4-8[1m]":       Rates(5.00, 0.50, 6.25, 10.00, 25.00),
    "claude-sonnet-4-6":         Rates(3.00, 0.30, 3.75,  6.00, 15.00),
    "claude-haiku-4-5-20251001": Rates(1.00, 0.10, 1.25,  2.00,  5.00),
}


def rates_for(model: str) -> Rates:
    return RATES.get(model, Rates())


@dataclass
class Usage:
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    calls: int = 0

    def add(self, u) -> None:
        """Accumulate one Anthropic ``usage`` object (tolerant of missing fields)."""
        self.calls += 1
        self.input       += getattr(u, "input_tokens", 0) or 0
        self.cache_read  += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.output      += getattr(u, "output_tokens", 0) or 0


def _costs(u: Usage, rates: Rates, ttl: str):
    write_rate = rates.cache_write_1h if ttl == "1h" else rates.cache_write_5m
    actual = (u.input * rates.input + u.cache_read * rates.cache_read
              + u.cache_write * write_rate + u.output * rates.output) / 1e6
    # counterfactual: same tokens, no caching -> whole prefix at base input
    naive = ((u.input + u.cache_read + u.cache_write) * rates.input
             + u.output * rates.output) / 1e6
    return actual, naive


def summarize(usage: Usage, model: str, ttl: str = "5m",
              rates: Rates | None = None) -> dict:
    """Cache-hit ratio + $ saved for an accumulated Usage. Shared by the
    proposer and the batch client so both report identical numbers."""
    r = rates or rates_for(model)
    cacheable = usage.cache_read + usage.cache_write
    hit = (usage.cache_read / cacheable) if cacheable else 0.0
    actual, naive = _costs(usage, r, ttl)
    return {
        "calls": usage.calls,
        "model": model,
        "ttl": ttl,
        "tokens": {"new_input": usage.input, "cache_read": usage.cache_read,
                   "cache_write": usage.cache_write, "output": usage.output},
        "cache_hit_ratio": round(hit, 4),
        "cost_usd": round(actual, 4),
        "cost_without_cache_usd": round(naive, 4),
        "saved_usd": round(naive - actual, 4),
        "saved_pct": round((1 - actual / naive) * 100, 1) if naive else 0.0,
    }


def cache_control(ttl: str = "5m") -> dict:
    """The cache_control marker for a breakpoint block. ``ttl='1h'`` for the
    longer (2x) window when calls are spaced >5 min apart (MC-5)."""
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


class CachedAnthropic:
    """Wraps the Anthropic client with one cache breakpoint at the end of the
    stable prefix (system array, which the API caches together). Used by the
    batch / standalone path; the in-loop proposer shares the same Usage/cost
    model via ``summarize``."""

    def __init__(self, system_instructions: str, kernel_text: str = "",
                 model: str = DEFAULT_MODEL, max_tokens: int = 4000,
                 tools: list | None = None, ttl: str = "5m",
                 rates: Rates | None = None, api_key: str | None = None):
        self.model = model
        self.max_tokens = max_tokens
        self.tools = tools
        self.ttl = ttl
        self.rates = rates or rates_for(model)
        self.session = Usage()
        self._key = (api_key or os.environ.get("SOOL_ANTHROPIC_KEY")
                     or ANTHROPIC_API_KEY)
        if not self._key:
            raise RuntimeError(
                "No API key: set SOOL_ANTHROPIC_KEY or ANTHROPIC_API_KEY, "
                "or pass api_key=."
            )

        # The kernel vocabulary (BFO 2020) is the bulk of the stable tokens, so
        # it lives in the system prefix and gets cached once per warm window.
        # cache_control on the LAST stable block caches everything before it.
        self.system = [{"type": "text", "text": system_instructions}]
        if kernel_text:
            self.system.append({
                "type": "text",
                "text": "# Frozen BFO 2020 kernel (closed vocabulary)\n" + kernel_text,
            })
        self.system[-1]["cache_control"] = cache_control(ttl)  # the breakpoint

        import anthropic  # imported lazily so --selftest needs no SDK
        self._client = anthropic.Anthropic(
            api_key=self._key,
            timeout=(LLM_CALL_TIMEOUT_SECONDS
                     if LLM_CALL_TIMEOUT_SECONDS > 0 else None),
        )

    def create(self, case_text: str):
        kwargs = dict(model=self.model, max_tokens=self.max_tokens,
                      system=self.system,
                      messages=[{"role": "user", "content": case_text}])
        if self.tools:
            kwargs["tools"] = self.tools
        resp = self._client.messages.create(**kwargs)
        self.session.add(resp.usage)
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        return text, resp

    def stats(self) -> dict:
        return summarize(self.session, self.model, self.ttl, self.rates)

    def report(self) -> dict:
        s = self.stats()
        t = s["tokens"]
        print(f"[bfo-agent] {s['calls']} calls | model={self.model} ttl={self.ttl}")
        print(f"  tokens: new_input={t['new_input']:,} cache_read={t['cache_read']:,} "
              f"cache_write={t['cache_write']:,} output={t['output']:,}")
        print(f"  cache-hit ratio: {s['cache_hit_ratio']*100:.1f}%")
        print(f"  cost: ${s['cost_usd']:.4f}  (uncached would be "
              f"${s['cost_without_cache_usd']:.4f})")
        print(f"  saved: ${s['saved_usd']:.4f}  ({s['saved_pct']:.1f}%)")
        return s
