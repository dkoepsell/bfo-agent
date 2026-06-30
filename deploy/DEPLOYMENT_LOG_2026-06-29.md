# Deployment Log — BFO-anchoring discipline

**Date:** 2026-06-29
**Change deployed:** BFO-anchoring discipline (bfo-agent-spec.md) — PC-1..PC-6
construction linter, anchor-not-regenerate proposer, BFO 2020 K_C/K_P kernel,
class budget + kernel-extension trail, regression tests.
**Targets:** Hetzner (live web, Anthropic backend) and DGX (local LLM / ollama).

---

## 0. Source change (laptop repo)

- Branch: `feature/coherence-gate`
- Commit: `6500160` — "Add BFO-anchoring discipline: PC-1..PC-6 construction
  linter + anchored proposer"
- Files: `app/bfo_catalog.py`, `app/coherence_gate.py`, `app/config.py`,
  `app/construction_linter.py` (new), `app/llm_proposer.py`,
  `app/orchestrator.py`, `app/schema.py`, `tests/test_bfo_catalog.py`,
  `tests/test_construction_linter.py` (new), `.env.example`
- Local test result: **55 passed**.

---

## 1. Hetzner (live web) — `drkoepsell@178.105.7.249`

Backend: **Anthropic** (`LLM_BACKEND` not present; Anthropic SDK). Served as a
foreground `python run.py` on `:5000` (the systemd unit reports `inactive` by
design while the foreground process serves). Deploy is **rsync + on-box script**,
not `git pull`. Fully user-space (no sudo).

### Pre-flight
- SSH reachable; host `ubuntu-4gb-fsn1-1`, Python 3.12.3.
- Live health before deploy:
  `{"ontology":"SOoL_v1","stats":{"num_classes":6881,"num_individuals":1678,"num_object_properties":0}}`
  — i.e. the exact runaway-class / zero-property failure mode this change targets.

### Steps
1. **rsync** code paths to the staging dir (excluding caches):
   ```
   rsync -az --exclude=__pycache__ --exclude='*.pyc' \
     app scripts tests deploy requirements.txt evaluation \
     drkoepsell@178.105.7.249:~/bfo-agent-deploy-stage/
   ```
   Verified `construction_linter.py`, `tests/test_construction_linter.py`, and
   `KERNEL_CLASSES` landed in the stage.
2. **Deploy script** (copies stage→live, backs up, pip install, runs tests):
   ```
   ssh drkoepsell@178.105.7.249 'bash ~/bfo-agent-deploy-stage/deploy/hetzner_deploy.sh'
   ```
   - Code backup created: `~/bfo-agent-codebackup-20260629_235703`
   - Test result on box: **55 passed in 7.66s**
3. **Restart** (user-space foreground restart + health check):
   ```
   ssh drkoepsell@178.105.7.249 'bash ~/bfo-agent/deploy/hetzner_restart.sh'
   ```
   - Stopped run.py pid 1111084, relaunched, health OK.

### Verification
- `/health` after restart returned `status:ok`, app serving on `:5000` with the
  new code (orchestrator imports `construction_linter`, so a clean start proves
  the merge imports).

### Result: SUCCESS. No ollama involved; the local-LLM scheme bug (below) cannot
occur on Hetzner.

### Rollback (if needed)
Restore `~/bfo-agent-codebackup-20260629_235703/*` into `~/bfo-agent/` and
re-run `hetzner_restart.sh`.

---

## 2. DGX (local LLM / ollama) — `ssh dgx`, `~/research/bfo-agent`

Backend: **ollama** (`LLM_BACKEND=ollama`, proposer `qwen2.5:72b-instruct-q4_K_M`,
extractor `qwen2.5:14b-instruct-q4_K_M`). Launched via `./start.sh`
(starts ollama, ensures models, runs `run.py` bound to the Tailscale IP
`100.79.97.53:5000`). venv is `venv/` (not `.venv`).

### Key finding: the DGX is a DIVERGED FORK, not a hand-patch
- No shared git history with the laptop repo (laptop base commits absent; no
  shared remote).
- Cleaner LLM abstraction: `app/llm_client.py` exposes
  `chat(system, user, max_tokens, model, cache_prefix)`; proposer/extractor call
  `chat()` instead of constructing `Anthropic(...)`.
- `app/config.py` has the `LLM_BACKEND`/`OLLAMA_*` block (and lacks the
  laptop's accounts/BYOK block).
- `app/orchestrator.py` has an extra `/ontologies/<name>/export` endpoint.

A blanket rsync of `app/` would have reverted the ollama backend → **surgical
merge** instead.

### Merge strategy
1. **Base-identical files** (verified by md5 against laptop `HEAD~1`) — overwrite
   directly: `bfo_catalog.py`, `coherence_gate.py`, `schema.py`,
   `tests/test_bfo_catalog.py`.
2. **New files** — copy: `construction_linter.py`,
   `tests/test_construction_linter.py`.
3. **Diverged files** — apply the laptop commit's diff as a patch onto the DGX
   copies: `config.py`, `llm_proposer.py`, `orchestrator.py`. All edit anchors
   matched verbatim; `llm_proposer.py` + `orchestrator.py` patched cleanly;
   `config.py` needed a manual append (the trailing context — the
   accounts/BYOK block — does not exist on the DGX).

### Steps
- Backed up the 6 changed files to `~/bfo-agent-codebackup-20260629_235754/`.
- Pushed the 7 merged app files + 2 test files via rsync/scp.
- Pre-push validation: all 7 files `py_compile` clean; confirmed the new code
  landed AND the DGX-specific code (ollama backend, export endpoint) was
  preserved.

### Validation on the DGX (its own venv)
```
venv/bin/python -c "import app.orchestrator, app.construction_linter, ...; \
  import app.config as c; print(c.LLM_BACKEND, c.PROPOSER_MODEL)"
# -> ollama qwen2.5:72b-instruct-q4_K_M   (local LLM preserved)
venv/bin/python -m pytest tests/test_construction_linter.py tests/test_bfo_catalog.py -q
# -> 32 passed
```

### Post-deploy incident: extraction returned 0 claims
Job `job_2220ffd472ef` (feed Aristotle's Categories) extracted **0 claims across
14/14 chunks**.

- **Symptom:** live `POST /extract/chunk` returned HTTP 500.
- **Error body:** `extraction failed: Could not reach Ollama at
  127.0.0.1:11434: No connection adapters were found for
  '127.0.0.1:11434/api/chat'`.
- **Root cause:** `start.sh` exported `OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"`
  **without an http:// scheme**. `run.py` loads `.env` via dotenv, which does NOT
  override an already-set env var, so the scheme-less value won (the `.env`
  value `http://127.0.0.1:11434` was ignored). `llm_client.py` builds
  `OLLAMA_HOST + "/api/chat"`, and `requests` rejects a scheme-less URL with
  "No connection adapters were found". Every ollama call 500'd → 0 claims.
  (Standalone CLI runs worked because they did not go through `start.sh`, so the
  config default — which has the scheme — was used.)

### Fix (two layers, committed)
- `app/config.py`: normalize `OLLAMA_HOST` to prepend `http://` when no scheme
  is present (backstop for any caller).
- `start.sh`: normalize `OLLAMA_HOST` to carry a scheme via a `case` block, and
  drop the now-redundant hardcoded `http://` in the two `curl` health checks.

### Verification of the fix
- `OLLAMA_HOST=127.0.0.1:11434 venv/bin/python -c "from app import config; print(config.OLLAMA_HOST)"`
  → `http://127.0.0.1:11434`
- Restarted `run.py` (note: `pkill -f run.py` is unsafe over SSH — it matches the
  remote shell whose argv contains "run.py"; launch when nothing is running, or
  kill by PID). Detached launch:
  `( setsid ./venv/bin/python run.py < /dev/null > run.log 2>&1 & )`
- Live `POST /extract/chunk` on a real Aristotle passage now returns
  **high-confidence claims** (substance / species-genus). End-to-end extraction
  restored.

### Commit (DGX repo, branch `master`)
`3fdc641` — "Add BFO-anchoring discipline (PC-1..PC-6 linter, anchored proposer)
+ fix ollama scheme" — 10 files, +829/-34. Only code committed; runtime data
(ontology/, jobs/, sessions/, locks, run.log) deliberately excluded.

### Second incident: feed committed nothing (every proposal "inconsistent")
During "feed selected" of 77 claims, the 72B proposer produced good output
(properly-typed individuals), but every claim was rejected at the gate's
**reasoner** tier and `committed:false`.

- **Root cause:** the app process's PATH did not include `~/.local/bin`, where
  `java` lives. owlready2 runs HermiT by spawning `java`; with java absent the
  reasoner tier errored and the gate marked every proposal inconsistent. (Both
  `start.sh` and a bare `setsid run.py` launch lacked the PATH; ollama worked
  only because `start.sh` calls it via the full `$OLLAMA_BIN` path.)
- **Fix:** `start.sh` now `export PATH="$HOME/.local/bin:$PATH"` (commit
  `c303a7b`). Verified: with java on PATH, `check_coherence_dry_run` returns
  coherent, and a live `feed_one` returned `committed:true, verdict:consistent`
  with `num_classes` growing 0 -> 1. ~20 s/claim with the 72B model warm.

### Result: SUCCESS. Local LLM (ollama) preserved and working; extraction AND
feed/commit fixed.

### Rollback (if needed)
Restore `~/bfo-agent-codebackup-20260629_235754/*` into
`~/research/bfo-agent/app/`, `git checkout start.sh`, restart via `./start.sh`.

---

## 3. Cross-cutting notes

- **Hetzner is unaffected by the ollama bug** — it has no ollama path
  (`LLM_BACKEND=anthropic`, Anthropic SDK) and no `start.sh` ollama export.
- **Action item for operator:** job `job_2220ffd472ef` recorded 0 claims while
  extraction was broken; re-run "Extract & append" on the DGX to populate it.
- **Tech debt:** the DGX fork and the laptop repo have diverged with no shared
  history. The DGX's `LLM_BACKEND`/`llm_client.py` design is cleaner than the
  laptop's direct-Anthropic wiring and is worth upstreaming so both boxes run
  identical, single-source code.
