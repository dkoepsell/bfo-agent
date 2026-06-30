# Deployment log — 2026-06-30

Rollout of **bfo-agent-spec.md (2026-06-30)** to Hetzner: PC-7/PC-8, prompt-cache
savings telemetry, the validation gate client, deterministic IRIs, structured
kernel-extension requests, and the SOoL-only MLC layer. BFO 2020 stays the
dominant kernel; MLC is gated to SOoL only.

## Commit

- `fac6874` — Implement bfo-agent-spec.md (2026-06-30): PC-7/8, cache savings,
  gate client, stable IRIs, kext, SOoL-only MLC
- Branch: `feature/coherence-gate`
- Tests: **80 passed, 2 skipped** (regression fixtures pending) — locally and on
  the server smoke test.

## Target

- Host: `drkoepsell@178.105.7.249` (Hetzner)
- Service: **user-space foreground `python run.py` on :5000** (NOT systemd; the
  `*.sudo.sh` restart is unused here). Behind shared Caddy basicauth at
  `bfo-agent.davidkoepsell.com`.
- DGX (ollama fork) is a separate target and was **not** touched — the cache-API
  savings are Anthropic-only and irrelevant there.

## Procedure (all user-space, no sudo)

1. `rsync -az --delete` `app/`, `tests/`, `deploy/` (+ spec & reference impls)
   to `~/bfo-agent-deploy-stage/`, excluding `.git/.venv/__pycache__/.env`,
   `ontology/library`, `jobs`, `sessions`, `.serena`.
2. `bash ~/bfo-agent-deploy-stage/deploy/hetzner_deploy.sh` — backs up live code
   to `~/bfo-agent-codebackup-20260630_144456`, applies staging → live, installs
   deps, sets gate env vars idempotently in `.env`, runs the test smoke
   (80 pass / 2 skip).
3. `bash ~/bfo-agent/deploy/hetzner_restart.sh` — stops the old `run.py`,
   relaunches, health-checks :5000.

## New env vars (set in `~/bfo-agent/.env`)

| Var | Value set | Notes |
|---|---|---|
| `CACHE_TTL` | `5m` | Keeps the BFO/rules prefix hot for back-to-back corpus runs (lower write cost). Flip to `1h` only when calls are spaced >5 min apart. |
| `STABLE_INDIVIDUAL_IRIS` | `false` | Deterministic, diffable individual IRIs (FR-6). Turn **on** for a book/corpus run you want to diff between passes. |
| `OWLTESTER_URL` | *(empty)* | Empty → in-process gate (construction PC-1..PC-8 + lint + local HermiT + file-level owl_checks). Point at an `owltesterservice` to use the remote gate. |
| `KERNEL_VERSION_IRI` | *(code default)* | `http://purl.obolibrary.org/obo/bfo/2020/bfo.owl`; stamps fragments and kext requests. |

## Verification

- `/health` now returns the new `prompt_cache` field (null until the first
  propose call instantiates the proposer) — confirms the new code is live:
  `{"ontology":"eco","prompt_cache":null,"stats":{"bfo_loaded":true,...},"status":"ok"}`
- Cache savings surface in `/health.prompt_cache` (cache-hit ratio, cost,
  `saved_usd`/`saved_pct`) after the first model call, and via
  `LLMProposer.report()`.

## For the book run

- Set `STABLE_INDIVIDUAL_IRIS=true` to diff the resulting ontology across passes.
- Consider `CACHE_TTL=1h` if the feed is not back-to-back.
- Read cost-vs-uncached from `/health.prompt_cache.saved_pct` after the run.

## Still owed

- Regression fixtures `tests/corpus/SOOL_autofixed.owl` + `AristotleCategories.owl`
  (the §10.5/§10.6 tests skip until present).
- 149-row offense CSV for batch mode.
- `owltesterservice` is external; the client falls back to the local gate when
  `OWLTESTER_URL` is unset.

— rollout 2026-06-30 (BFO-dominant; MLC SOoL-only)
