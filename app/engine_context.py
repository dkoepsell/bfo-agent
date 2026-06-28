"""Per-request engine context: which key pays, which models, and where token
usage is recorded.

Built once per request (or per worker job) from the authenticated user + the
target job, then handed to the per-request proposer/extractor factories so the
``on_usage`` hook lands token counts in the ``usage`` table.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .db import session_scope
from .models import Usage


@dataclass
class EngineContext:
    api_key: str
    model: str
    extractor_model: str
    billed_to: str            # 'owner' | 'byok'
    user_id: int | None = None
    job_id: str | None = None

    def record_usage(self, phase: str, model: str,
                     input_tokens: int, output_tokens: int) -> None:
        """Append a usage row. Best-effort: metering must never break a job."""
        if self.user_id is None:
            return
        try:
            with session_scope() as s:
                s.add(Usage(
                    user_id=self.user_id,
                    job_id=self.job_id,
                    phase=phase,
                    model=model,
                    input_tokens=input_tokens or 0,
                    output_tokens=output_tokens or 0,
                    billed_to=self.billed_to,
                ))
        except Exception:
            # Swallow metering failures; the LLM work itself already happened.
            pass

    def usage_hook(self, phase: str):
        """Return an on_usage(model, in_tok, out_tok) callback for a phase."""
        return lambda model, in_tok, out_tok: self.record_usage(
            phase, model, in_tok, out_tok
        )


def owner_context(model: str | None = None,
                  extractor_model: str | None = None,
                  user_id: int | None = None,
                  job_id: str | None = None) -> EngineContext:
    """Context billed to the owner key (free tier / legacy interactive use)."""
    return EngineContext(
        api_key=config.ANTHROPIC_API_KEY,
        model=model or config.ANTHROPIC_MODEL,
        extractor_model=extractor_model or config.ANTHROPIC_EXTRACTOR_MODEL,
        billed_to="owner",
        user_id=user_id,
        job_id=job_id,
    )


def byok_context(api_key: str, model: str | None = None,
                 extractor_model: str | None = None,
                 user_id: int | None = None,
                 job_id: str | None = None) -> EngineContext:
    """Context billed to a user's own Anthropic key."""
    return EngineContext(
        api_key=api_key,
        model=model or config.ANTHROPIC_MODEL,
        extractor_model=extractor_model or config.ANTHROPIC_EXTRACTOR_MODEL,
        billed_to="byok",
        user_id=user_id,
        job_id=job_id,
    )
