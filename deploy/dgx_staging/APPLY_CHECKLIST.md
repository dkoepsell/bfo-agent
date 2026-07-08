# DGX staging bundle — SPEC-bfo-agent-speed migration (staged 2026-07-08)

Speed/efficiency changes from `feature/proposer-cache-scoping` (stages 0–5,
base `800dc0a5`, through `7afb022a`), staged for the diverged DGX/ollama fork.
DGX was offline when this was assembled — execute when the box is back.

The DGX fork differs from Hetzner in two load-bearing ways:
- **No Anthropic**: local LLM via ollama/qwen2.5. Everything batch-propose /
  prompt-cache / Message-Batches is skipped outright.
- **No `job_runner`**: the DGX orchestrator drives an inline `feed_one`
  loop, so the checkpoint commit counter lives on the **manager**
  (`mgr.commits_since_full_verify`), not in a job's `feed_state`, and the
  final `verify_full` is wired into the fork's own completion branch.

## What's in the bundle

- `wholesale/` — safe to copy verbatim onto the fork:
  - `app/timing.py` (new), `app/gate_structural.py` (new),
    `app/coherence_gate.py`, `app/stable_iri.py`
  - `scripts/timing_report.py` (new)
  - `tests/`: `test_timing.py`, `test_gate_structural_skip.py`,
    `test_inmem_dry_run.py`, `test_reduced_world.py`,
    `test_checkpoint_verify.py`
    - ⚠️ `test_checkpoint_verify.py` exercises the Hetzner-shaped
      orchestrator/job `feed_state`; on DGX keep the manager-level cases
      (`test_verify_full_real_smoke`, finalize-guard cases) and adapt or
      drop the `feed_env` ones to the fork's inline loop.
- `patches/` — `git diff 800dc0a5..7afb022a` for the three diverged
  modules, to hand-apply (NOT `git apply` blind):
  - `ontology_manager.patch` — AppliedDelta apply/rollback, `_scratch_world`,
    `_proposal_guards`, `verify_full()`, `commits_since_full_verify`,
    TBox mirror + reduced world, `unsaved_commits` + conditional save.
    Graft onto the fork's version, which already carries the commit
    backstop. Take everything except `_update_iri_reservations`'s
    batch-propose coupling (keep the method; it is a no-op with the flag
    off) — nothing here touches Anthropic.
  - `orchestrator.patch` — take: phase timers + `claim_timing` emission,
    structural-gate wiring, checkpoint + mandatory final `verify_full`
    (re-home the counter onto the manager), amortized-save flush points +
    `_prepare_amortized_resume` (re-home: no `job_runner`, so flush on the
    fork's loop exit + atexit; the `last_saved` watermark lives wherever
    the fork records per-claim state). SKIP: batch-propose consumption,
    `prepare_proposals` route, SSE `/jobs/<id>/stream` UI feed, `flush_fn`
    plumbing into `job_runner`.
  - `config.patch` — append the flags block verbatim (all default-off /
    legacy-default, so inert until flipped): `TIMING_INSTRUMENTATION`,
    `GATE_REASONER_STRUCTURAL_SKIP`, `INMEM_DRY_RUN`,
    `VERIFY_EVERY_COMMIT` + `FULL_VERIFY_EVERY_K` +
    `FINALIZE_REQUIRES_FULL_VERIFY` + `CHECKPOINT_FAIL_MARK_REVIEW`,
    `REDUCED_REASONING_WORLD`, `SAVE_EVERY_COMMIT` (+ its sanitizer and
    `_warn_contradictory_flags`). SKIP `BATCH_PROPOSE_*` /
    `IRI_RESERVATION_ENABLED` (batch propose is never merged to DGX;
    the reservation map without batch propose is optional — harmless).

## SKIP entirely on DGX

`app/batch_propose.py`, `cached_client.py` / any Anthropic client change,
`jobs.py` proposal field + `reset_claims_pending` (unless the fork grows a
job store), `job_runner.py` changes, `client/index.html` Live tab.

## Ordered steps (on `ssh dgx`, in the fork checkout)

1. Backup the fork's current `app/ontology_manager.py`,
   `app/orchestrator.py`, `app/config.py` and note the pre-merge
   `pytest tests/` pass count (venv is `venv/`, NOT `.venv`).
2. Copy `wholesale/` files into place.
3. Hand-apply the three patches per the notes above.
4. Append to the DGX `.env`:
   ```
   TIMING_INSTRUMENTATION=true
   GATE_REASONER_STRUCTURAL_SKIP=false   # flip after burn-in
   INMEM_DRY_RUN=false                   # flip after burn-in
   VERIFY_EVERY_COMMIT=false             # quality UPGRADE for DGX: it ran
   FULL_VERIFY_EVERY_K=250               # no-verify with no certificate;
   FINALIZE_REQUIRES_FULL_VERIFY=true    # this adds the missing certificate
   REDUCED_REASONING_WORLD=false         # flip with/after INMEM_DRY_RUN
   SAVE_EVERY_COMMIT=true                # flip last, after timing evidence
   ```
5. `venv/bin/python -m pytest tests/` on-box; compare against the
   pre-merge pass count from step 1.
6. Restart via `./start.sh`.
7. Verify the SOoL baseline is still `coherent=True`, feed one claim
   end-to-end, then run `verify_full()` once and confirm
   `ok: true, unsat_classes: []`.
8. Burn-in, then flip `GATE_REASONER_STRUCTURAL_SKIP=true` and
   `INMEM_DRY_RUN=true`; later `REDUCED_REASONING_WORLD=true`; compare
   `scripts/timing_report.py <session>` before/after each flip.
