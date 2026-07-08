#!/usr/bin/env bash
# Coherence-gate deploy for the Hetzner box. USER-SPACE ONLY (no sudo).
# Run this ON the server after the code-only rsync has staged files into
# ~/bfo-agent-deploy-stage/. It copies code into the live app dir, installs
# deps, sets the gate env vars idempotently, and runs the test suite.
#
# It deliberately NEVER touches ontology/, sessions/, jobs/, .env data values,
# or the venv contents beyond pip install, so live data is preserved.
#
# Restarting the service needs sudo and is a SEPARATE step: hetzner_restart.sudo.sh
set -euo pipefail

APP=/home/drkoepsell/bfo-agent
STAGE=/home/drkoepsell/bfo-agent-deploy-stage
VENV="$APP/.venv"
TS=$(date +%Y%m%d_%H%M%S)

echo "== bfo-agent coherence-gate deploy =="
[ -d "$APP" ] || { echo "FAIL: $APP not found"; exit 1; }
[ -d "$STAGE" ] || { echo "FAIL: staging dir $STAGE not found (run the rsync step first)"; exit 1; }

# 1. Back up the code we are about to replace (code only, fast).
BACKUP="$HOME/bfo-agent-codebackup-$TS"
echo "-- backing up current code to $BACKUP"
mkdir -p "$BACKUP"
for p in app evaluation scripts requirements.txt; do
  [ -e "$APP/$p" ] && cp -a "$APP/$p" "$BACKUP/" || true
done

# 2. Copy code-only paths from staging into the live app dir.
echo "-- applying new code"
for p in app evaluation scripts tests deploy requirements.txt; do
  [ -e "$STAGE/$p" ] && cp -a "$STAGE/$p" "$APP/" || true
done

# 3. Install/refresh Python deps into the existing venv.
echo "-- installing deps"
if [ -x "$VENV/bin/pip" ]; then
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt"
else
  echo "WARN: no venv at $VENV; skipping pip install"
fi

# 4. Set gate env vars idempotently in .env (values, not secrets).
echo "-- ensuring gate env vars in .env"
ENVF="$APP/.env"
touch "$ENVF"
set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENVF"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENVF"
  else
    echo "${key}=${val}" >> "$ENVF"
  fi
}
# ensure_env: add the key with a default ONLY if absent — never clobbers a
# value chosen in the live .env (flag flips are operational state, not code).
ensure_env() {
  local key="$1" val="$2"
  grep -q "^${key}=" "$ENVF" || echo "${key}=${val}" >> "$ENVF"
}
set_env ENABLE_COHERENCE_GATE true
set_env GATE_POLICY reject_resample
set_env GATE_RUN_REASONER true
# Tunable (1 = no resample, saves up to 2 extra propose calls per rejected
# claim on LLM-bound feeds); default 2, but respect a live override.
ensure_env GATE_MAX_ATTEMPTS 2
set_env ENABLE_SCAFFOLDING true
# bfo-agent-spec.md (2026-06-30) rollout:
#   CACHE_TTL: "5m" keeps the BFO prefix hot for back-to-back corpus runs (lower
#     write cost); flip to "1h" only when calls are spaced >5 min apart.
#   STABLE_INDIVIDUAL_IRIS: deterministic, diffable individual IRIs; turn on for
#     a book/corpus run you want to diff between passes.
#   OWLTESTER_URL: empty => in-process gate (construction + lint + local HermiT +
#     file-level owl_checks). Point at an owltesterservice to use the remote gate.
set_env CACHE_TTL 5m
set_env STABLE_INDIVIDUAL_IRIS false
grep -q "^OWLTESTER_URL=" "$ENVF" || echo "OWLTESTER_URL=" >> "$ENVF"

# SPEC-bfo-agent-speed.md rollout (stages 0-5). Ensured-if-absent only:
# these are progressive flips validated by timing runs, so a deploy must
# never reset a flip that is already live. Target end-state values are in
# the plan's flag table; the conservative defaults below only seed a fresh
# .env that has never been flipped.
ensure_env TIMING_INSTRUMENTATION true
ensure_env GATE_REASONER_STRUCTURAL_SKIP false   # flip 1 (live: true)
ensure_env INMEM_DRY_RUN false                   # flip 2 (live: true)
ensure_env VERIFY_EVERY_COMMIT true              # flip 3: false
ensure_env FULL_VERIFY_EVERY_K 250
ensure_env FINALIZE_REQUIRES_FULL_VERIFY true
ensure_env CHECKPOINT_FAIL_MARK_REVIEW false
ensure_env REDUCED_REASONING_WORLD false         # flip 3: true
ensure_env BATCH_PROPOSE_ENABLED false           # flip 4: true
ensure_env IRI_RESERVATION_ENABLED false         # flip 4: true
ensure_env SAVE_EVERY_COMMIT true                # flip 5, only if timing says

# 5. Smoke test: imports + the gate test suite (no server, no API key needed).
echo "-- smoke test"
cd "$APP"
"$VENV/bin/python" -c "from app.orchestrator import create_app; create_app(); print('app builds OK')"
if "$VENV/bin/python" -m pytest -q tests/ >/tmp/gate_pytest.log 2>&1; then
  tail -1 /tmp/gate_pytest.log
else
  echo "WARN: tests did not all pass; see /tmp/gate_pytest.log"
fi

echo
echo "== code deployed. NOT yet live: restart the service with sudo =="
echo "   sudo bash $APP/deploy/hetzner_restart.sudo.sh"
echo "   (rollback code with: cp -a $BACKUP/* $APP/ && restart)"
