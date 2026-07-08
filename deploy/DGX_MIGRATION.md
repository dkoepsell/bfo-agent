# DGX migration — ontology-quality changes

> **2026-07-07 extractor parity (claim-yield fix): DONE.** DGX was extracting far
> fewer claims than Hetzner for two reasons, both now fixed on `dgx:~/research/bfo-agent`:
> (1) the extractor ran the **old pre-fidelity prompt** (no ATOMICITY-SPLIT / COHERENCE
> rules) — ported the current `app/extractor.py` prompt strings surgically, keeping
> DGX's `llm_client.chat()` ollama wiring untouched (backup: `app/extractor.py.bak-2026-07-07`);
> (2) extraction ran on **qwen2.5:14b** — switched `.env` `OLLAMA_EXTRACTOR_MODEL` to
> `qwen2.5:72b-instruct-q4_K_M` (already pulled for the proposer). Smoke test: a bundled
> "depends on X but retains integrity" sentence now splits into 3 atomic claims and drops
> the contradictory independence half, matching mainline behavior. Restart the service to
> pick up both changes. Note: 72b extraction is slower per chunk than 14b — expected tradeoff.

> **2026-07-07 migration (fidelity mode + FOL gate, commits e22591e1..4e89e750): DONE.**
> Shipped surgically per the method below. Wholesale copies: `coherence_gate.py`,
> `registry.py` (base-identical) + new `incoherence_ledger.py`, `fol_translate.py`,
> `fol_gate.py`, `ontology/bfo-2020-fol/` (15 files), `tests/test_fidelity_mode.py`,
> `tests/test_fol_gate.py`. Patched with hand-merged rejects: `config.py` (append),
> `gate_client.py`, `orchestrator.py` (DGX's inline feed_one — no job_runner there,
> so the end-of-job audit hook is present but unwired), `ontology_manager.py`
> (grafted the commit backstop + `_verify_saved_coherent` + `_REASONER_LOCK` +
> `CommitCoherenceError`, which the fork never had), `client/index.html` (Audits
> tab). **DGX behavior preserved:** curated commits stay no-verify (`verify` only
> engages in faithful mode) so ollama feed throughput is unchanged; LLM_BACKEND=
> ollama/qwen2.5 untouched. LADR built on-box (mace4 needs `CC="gcc -std=gnu89"`
> and the `-lm`-after-objects Makefile fix in BOTH `provers.src/` and `mace4.src/`);
> binaries in `~/.local/bin`, paths in `.env`. Tests: **86 passed / 0 failed**
> (one DGX-local scorer test updated to `verify=False`, matching mainline).
> Smoke: Mode A audit of AristotleCategories → `consistent`, mace4 model domain 2.
> App intentionally NOT started (GPU/72B policy).

## 2026-06-30 session

> **STATUS: DONE (2026-06-30).** Applied to `dgx:~/research/bfo-agent` (repo lives
> at `~/research/bfo-agent`, venv `venv/`, launch `./start.sh`). All 6 new modules
> + construction_linter (PC-5/7/8) + ontology_manager guards + config vars +
> orchestrator kext wiring are live; seeds checked (load-time guard auto-heals
> regardless). 47 migrated tests pass on DGX; the migration added **zero
> regressions** (7 pre-existing DGX test failures in coherence_gate/scorer/
> regression_corpus/scaffolding are unchanged before vs after — verified against
> `~/bfo-agent-premigration-backup/`). **Those 7 were a test-env PATH issue, not
> product bugs:** pytest ran without `~/.local/bin` on PATH, so HermiT couldn't
> find `java`, the reasoner silently failed, and the gate read it as
> "inconsistent". Fixed at source — `config.py` now self-heals the java PATH
> (commit `7697528`), so bare `pytest tests/` is **77 passed / 0 failed**, and the
> service no longer silently mis-rejects when launched without start.sh.
> Smoke-tested end to end on DGX (qwen2.5:72b propose → PC-1..8 → 200, 0 errors).
> Orchestrator gate_client rewiring was
> **deferred** (DGX invokes the gate differently; PC-1..8 already flow through the
> existing gate path). Caching/cost work intentionally NOT migrated.



Tracking which changes from this session must migrate to the **DGX fork** (local
ollama/qwen2.5). The DGX is a *diverged* fork — apply **surgically per change,
never blanket rsync** (it would clobber the ollama divergence).

**Scope rule:** migrate the **ontology-quality / correctness** changes. **Skip
all prompt-caching / cost-savings** work — DGX runs ollama locally, so API cost
and prompt caching are irrelevant there.

---

## ✅ MIGRATE — ontology quality & correctness

### P0 — correctness guards (highest priority; ollama is more likely to trip these)

1. **BFO Function unsatisfiability fix** (commit `ad04ea2`)
   - `ontology_manager._sanitize_bfo_disjointness()` — load-time guard that strips
     any `owl:disjointWith` between two BFO classes in a subclass relationship.
     Auto-heals existing ontologies in-memory; needs the `log` logger + the
     `bfo_catalog` import already present.
   - **Seed fix:** in every `ontology/library/*/seed/bfo_relations.ttl`, delete the
     line `obo:BFO_0000016 owl:disjointWith obo:BFO_0000034 .` (Disposition ⊥
     Function — invalid, Function ⊑ Disposition) and fix the comment. **DGX has its
     own seeds with the same bug.**
   - Without this, every claim comes back `inconsistent` (Function unsatisfiable).

2. **subClassOf-a-property guard** (commits `effe2cc7`, `9b730f6`)
   - `ontology_manager._strip_subclass_of_property()` — load-time strip of any
     `rdfs:subClassOf` whose object is typed as a property (detected from the
     world's `rdf:type`, covering BFO_0000050/part_of, BFO_0000054/realized_in,
     RO_*). Prevents the owlready2 **metaclass conflict** that bricks class
     enumeration and 500s the feed.
   - `construction_linter` **PC-5**: reject a `parent_class` / subClassOf-target
     that is a BFO/RO/IAO id **not in K_C** (i.e. not a known class). Uses the new
     `_target_is_not_a_class()` helper.
   - Without this, one bad ollama proposal crashes the whole feed.

### P1 — construction discipline

3. **PC-7 / PC-8 lexical IRI checks** (commit `fac6874`)
   - `app/owl_checks.py` (NEW, Anthropic-free) — emitters + `check_expression_iris`
     (PC-7) / `check_antipatterns_all` (PC-8) / `check_dangling_and_remint`.
   - `construction_linter`: `_check_iris()` wiring so self-lint == gate. Reuse the
     promoted `app/owl_checks.py` (the repo-root `sool_owl_checks.py` is just a CI
     shim).

4. **Gate client with local fallback** (commit `fac6874`)
   - `app/gate_client.py` (NEW, Anthropic-free) — `evaluate()` runs the in-process
     gate + file-level `owl_checks.run_all`. On DGX leave `OWLTESTER_URL` unset →
     pure local gate (HermiT). Wire the two `gate_mod.gate(...)` call sites in
     `orchestrator` to `gate_client.evaluate(...)`.

### P1 — BFO-anchoring structure (SOoL-scoped)

5. **SOoL/MLC anchored extension** (commit `fac6874`)
   - `app/sool_extension.py` (NEW) — 8 MLC nodes → BFO anchors; `applies_to()`
     guard. **MLC is SOoL-only** — gated, never wired into the general path.
   - `app/mlc.py` (NEW) — FR-4 well-formedness + FR-5 13-type contradiction
     typology (typed individuals, never absence classes). SOoL-gated.

6. **Kernel-extension requests** (commit `fac6874`)
   - `app/kext.py` (NEW) — structured `*.kext.json`. Wire into
     `orchestrator._budget_and_kext_notes`. Needs `config.KERNEL_VERSION_IRI`.

### P2 — reproducibility (optional, quality-review aid)

7. **Deterministic individual IRIs (FR-6)** (commit `fac6874`)
   - `app/stable_iri.py` (NEW, Anthropic-free) — `stable_local_name` +
     `remap_proposal`. Opt-in via `config.STABLE_INDIVIDUAL_IRIS`; applied in
     `ontology_manager.apply_proposal`. Lets you **diff** ollama output between
     runs. Pure-Python, no cost angle — safe to take.

### Config additions to migrate
`OWLTESTER_URL` (leave empty), `OWLTESTER_TIMEOUT`, `KERNEL_VERSION_IRI`,
`STABLE_INDIVIDUAL_IRIS`. **Do NOT migrate `CACHE_TTL`.**

### New modules safe to copy wholesale (all Anthropic-free)
`app/owl_checks.py`, `app/sool_extension.py`, `app/mlc.py`, `app/kext.py`,
`app/stable_iri.py`, `app/gate_client.py`. The surgical work is only in the
already-diverged `ontology_manager.py`, `construction_linter.py`, `orchestrator.py`.

### Tests to take
`tests/test_sool_extension.py`, `tests/test_gate_client.py`, `tests/test_phase5.py`,
`tests/test_bfo_disjointness_guard.py`, and the new PC-5/PC-7/PC-8 cases in
`tests/test_construction_linter.py`.

---

## ❌ SKIP — caching / cost-savings (Anthropic-only; irrelevant on ollama)

- `app/cached_client.py` (CachedAnthropic / Usage / savings math).
- `llm_proposer.py` cache accounting: `self.usage`, `stats()`/`report()`,
  `cache_control` ttl, and the `_compact_lines` / `_split_for_breakpoints`
  two-breakpoint prompt restructure (commit `30bd3e6b`).
  - ⚠️ *Optional aside:* `_compact_lines` shrinks the ontology context ~65%. That
    has **no cost relevance** on ollama, but a smaller prompt can help qwen2.5's
    limited context window / latency. Take it only if context-window pressure is a
    problem — otherwise skip.
- `config.CACHE_TTL`; the `/health` `prompt_cache` field in `orchestrator`.
- `deploy/feed_monitor.*`, `deploy/deploy_on_complete.py` (Hetzner cron infra).
- All `deploy/DEPLOYMENT_LOG_*.md` and this file (Hetzner-side records).

---

## Method (per DGX deployment notes)
Surgical merge on `ssh dgx`: copy the 6 new Anthropic-free modules, then hand-apply
the `ontology_manager` / `construction_linter` / `orchestrator` diffs to the
diverged versions, fix the seed `bfo_relations.ttl` files, run the migrated tests
under the DGX `venv/`, restart via `./start.sh`. Verify a SOoL baseline is
`coherent=True` (the Function fix) and feed one claim that would have hit a
subClassOf-property crash.

---

# 2026-07-08 — SPEC-bfo-agent-speed stages 0–5 (staged bundle)

DGX was offline (ssh timeout via tailscale) on 2026-07-07/08, so this round
is **staged, not applied**: everything needed lives in
[`deploy/dgx_staging/`](dgx_staging/APPLY_CHECKLIST.md) — `wholesale/` files
to copy verbatim, `patches/` (`git diff 800dc0a5..7afb022a`) for the three
diverged modules to hand-apply, and `APPLY_CHECKLIST.md` with the ordered
on-box steps, `.env` flag block, and test/verify gates.

## ✅ MIGRATE (quality + speed, Anthropic-free)

- Step-0 timing instrumentation (`app/timing.py`, `claim_timing` events,
  `scripts/timing_report.py`) — read the split on the DGX box before
  flipping anything (its bottleneck may differ: local LLM is slower, so
  propose may dominate even more than on Hetzner).
- Structural straddle gate (`app/gate_structural.py`,
  `GATE_REASONER_STRUCTURAL_SKIP`) — rejected-by-straddle claims stop
  spawning a JVM.
- In-memory dry-run (`AppliedDelta`, `_scratch_world`, `_proposal_guards`,
  `INMEM_DRY_RUN`) — kills the 2–5 reparse-per-claim disk round-trip.
- Checkpoint + mandatory final full verify (`verify_full`,
  `VERIFY_EVERY_COMMIT=false` + `FULL_VERIFY_EVERY_K`) — a quality
  **upgrade** for DGX, which currently runs no-verify with no certificate;
  counter re-homed onto the manager (no `job_runner` on the fork).
- Reduced reasoning world (`REDUCED_REASONING_WORLD`) — dry-run reasons
  over TBox + touched individuals; checkpoint/final pass reconciles.
- Amortized save (`SAVE_EVERY_COMMIT`, manager `unsaved_commits`) — flip
  only if DGX timings show the O(N) per-claim save matters.

## ❌ SKIP (Anthropic-only / Hetzner infra)

`app/batch_propose.py` + Message Batches, `BATCH_PROPOSE_*` /
`IRI_RESERVATION_ENABLED` flags, `jobs.py` proposal field +
`reset_claims_pending`, `job_runner.py` flush/verify plumbing,
`client/index.html` Live SSE tab, all cache-savings telemetry.

Method as always: surgical merge per the checklist, never blanket rsync.
