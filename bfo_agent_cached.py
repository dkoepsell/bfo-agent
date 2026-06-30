"""
bfo_agent_cached.py
===================
Drop-in prompt-caching client for bfo-agent (Hetzner / any Linux host).

WHY
---
bfo-agent re-sends the same large stable prefix on every call:
    system instructions + the sool-kernel.ttl closed vocabulary + few-shot
    examples + the prohibited-construction rules.
Only the per-case source text changes. Without caching you pay full input
rate on that whole prefix every single case. With a cache breakpoint at the
end of the prefix, the first call writes it (1.25x base input) and every
later call reads it (0.1x base input) — up to ~90% off the repeated portion.

Sequential corpus runs keep the cache hot: the 5-minute TTL refreshes on
each hit, so as long as cases are processed back-to-back the prefix stays
cached for the whole run. Use ttl="1h" only if calls are spaced >5 min apart.

USAGE
-----
    from bfo_agent_cached import CachedAnthropic

    client = CachedAnthropic(
        system_instructions=SOOL_SYSTEM_PROMPT,     # stable
        kernel_text=open("sool-kernel.ttl").read(), # stable, cached
        model="claude-sonnet-4-6",
        max_tokens=4000,
    )
    for case in corpus:
        resp = client.create(case.text)            # only this varies
        emit(resp.text)
    client.report()                                 # cache-hit % + $ saved

ENV
---
    export SOOL_ANTHROPIC_KEY=sk-ant-...
    pip install anthropic
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-sonnet-4-6"   # bulk OWL generation; swap to opus for hard cases, haiku for first-pass

# Per-million-token USD rates. Defaults are Sonnet 4.6 (verify on the pricing
# page; these only affect the savings estimate, not behavior).
@dataclass
class Rates:
    input: float = 3.00          # base input
    cache_read: float = 0.30     # 0.1x base
    cache_write_5m: float = 3.75 # 1.25x base
    cache_write_1h: float = 6.00 # 2.0x base
    output: float = 15.00

RATES = {
    "claude-opus-4-8":          Rates(5.00, 0.50, 6.25, 10.00, 25.00),
    "claude-sonnet-4-6":        Rates(3.00, 0.30, 3.75,  6.00, 15.00),
    "claude-haiku-4-5-20251001":Rates(1.00, 0.10, 1.25,  2.00,  5.00),
}


@dataclass
class Usage:
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    calls: int = 0

    def add(self, u, ttl: str):
        self.calls += 1
        self.input       += getattr(u, "input_tokens", 0) or 0
        self.cache_read  += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.output      += getattr(u, "output_tokens", 0) or 0


@dataclass
class Response:
    text: str
    raw: object
    usage: object


class CachedAnthropic:
    """Wraps the Anthropic client and puts one cache breakpoint at the end of
    the stable prefix (tools -> system, which the API caches together)."""

    def __init__(self, system_instructions: str, kernel_text: str = "",
                 model: str = DEFAULT_MODEL, max_tokens: int = 4000,
                 tools: list | None = None, ttl: str = "5m",
                 rates: Rates | None = None, api_key: str | None = None):
        self.model = model
        self.max_tokens = max_tokens
        self.tools = tools
        self.ttl = ttl
        self.rates = rates or RATES.get(model, Rates())
        self.session = Usage()
        self._key = api_key or os.environ.get("SOOL_ANTHROPIC_KEY")
        if not self._key:
            raise RuntimeError("Set SOOL_ANTHROPIC_KEY (or pass api_key=).")

        # Build the cached system prefix. The kernel vocabulary is the bulk of
        # the stable tokens, so it lives here and gets cached once per warm
        # window. cache_control on the LAST stable block caches everything
        # before it (tools + the whole system array).
        cache_control = {"type": "ephemeral"}
        if ttl == "1h":
            cache_control = {"type": "ephemeral", "ttl": "1h"}
        self.system = [{"type": "text", "text": system_instructions}]
        if kernel_text:
            self.system.append({
                "type": "text",
                "text": "# Frozen SOoL kernel (closed vocabulary)\n" + kernel_text,
            })
        self.system[-1]["cache_control"] = cache_control  # the breakpoint

        import anthropic  # imported lazily so --selftest needs no SDK
        self._client = anthropic.Anthropic(api_key=self._key)

    def create(self, case_text: str) -> Response:
        kwargs = dict(model=self.model, max_tokens=self.max_tokens,
                      system=self.system,
                      messages=[{"role": "user", "content": case_text}])
        if self.tools:
            kwargs["tools"] = self.tools
        resp = self._client.messages.create(**kwargs)
        self.session.add(resp.usage, self.ttl)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return Response(text=text, raw=resp, usage=resp.usage)

    # ---- reporting -------------------------------------------------------
    def _costs(self, u: Usage):
        r = self.rates
        write_rate = r.cache_write_1h if self.ttl == "1h" else r.cache_write_5m
        actual = (u.input * r.input + u.cache_read * r.cache_read
                  + u.cache_write * write_rate + u.output * r.output) / 1e6
        # counterfactual: same tokens, no caching -> whole prefix at base input
        naive = ((u.input + u.cache_read + u.cache_write) * r.input
                 + u.output * r.output) / 1e6
        return actual, naive

    def stats(self) -> dict:
        u = self.session
        cacheable = u.cache_read + u.cache_write
        hit = (u.cache_read / cacheable) if cacheable else 0.0
        actual, naive = self._costs(u)
        return {
            "calls": u.calls,
            "tokens": {"new_input": u.input, "cache_read": u.cache_read,
                       "cache_write": u.cache_write, "output": u.output},
            "cache_hit_ratio": round(hit, 4),
            "cost_usd": round(actual, 4),
            "cost_without_cache_usd": round(naive, 4),
            "saved_usd": round(naive - actual, 4),
            "saved_pct": round((1 - actual / naive) * 100, 1) if naive else 0.0,
        }

    def report(self):
        s = self.stats()
        t = s["tokens"]
        print(f"[bfo-agent] {s['calls']} calls | model={self.model} ttl={self.ttl}")
        print(f"  tokens: new_input={t['new_input']:,} cache_read={t['cache_read']:,} "
              f"cache_write={t['cache_write']:,} output={t['output']:,}")
        print(f"  cache-hit ratio: {s['cache_hit_ratio']*100:.1f}%")
        print(f"  cost: ${s['cost_usd']:.4f}  (uncached would be ${s['cost_without_cache_usd']:.4f})")
        print(f"  saved: ${s['saved_usd']:.4f}  ({s['saved_pct']:.1f}%)")
        return s


# ---------------------------------------------------------------------------
# Self-test: validates request shape + savings math with a mock, no SDK/network.
# ---------------------------------------------------------------------------
def _selftest():
    class _U:  # mock usage object
        def __init__(self, i, cr, cw, o):
            self.input_tokens, self.cache_read_input_tokens = i, cr
            self.cache_creation_input_tokens, self.output_tokens = cw, o
    # bypass __init__ (no key/SDK needed) and exercise the math directly
    c = CachedAnthropic.__new__(CachedAnthropic)
    c.model, c.ttl, c.rates, c.session = "claude-sonnet-4-6", "5m", RATES["claude-sonnet-4-6"], Usage()
    # 1 cold call (writes 8k prefix) + 9 warm calls (read 8k prefix, ~300 new each)
    c.session.add(_U(300, 0, 8000, 600), "5m")
    for _ in range(9):
        c.session.add(_U(300, 8000, 0, 600), "5m")
    s = c.report()
    assert s["calls"] == 10
    assert s["tokens"]["cache_read"] == 72000
    assert 0.89 < s["cache_hit_ratio"] < 0.91          # 72k read / 80k cacheable
    assert s["saved_usd"] > 0 and s["saved_pct"] > 50  # caching clearly wins
    print("selftest: PASS")


def _smoke():
    """One real call if SOOL_ANTHROPIC_KEY is set; proves caching is live."""
    c = CachedAnthropic(
        system_instructions="You are bfo-agent. Reply with the single word OK.",
        kernel_text="(kernel placeholder) " * 800,   # ~big enough to cache
        model=os.environ.get("BFO_MODEL", DEFAULT_MODEL), max_tokens=16)
    c.create("case A"); c.create("case B")            # 2nd should read cache
    c.report()


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        _smoke()
    else:
        _selftest()
