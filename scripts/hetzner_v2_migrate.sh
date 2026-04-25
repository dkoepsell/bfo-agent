#!/usr/bin/env bash
# hetzner_v2_migrate.sh
#
# Migrate the Hetzner production server from v1 (singleton working.owl)
# to v2 (library-based multi-ontology) while preserving rollback.
#
# Run this script FROM KoeppyBox (not on Hetzner). It uses rsync and ssh
# to push code and run remote commands.
#
# Prereqs:
#   - KoeppyBox has the v2 code at ~/projects/bfo-agent/
#   - Phase 1-6 patches are applied on KoeppyBox and tagged
#   - Hetzner is currently running v1 at 178.105.7.249
#   - You can SSH to drkoepsell@178.105.7.249 without a password
#   - Hetzner's systemd service bfo-agent.service exists and is running
#
# What this does:
#   1. Pre-flight checks on KoeppyBox and Hetzner
#   2. Tags the Hetzner state as v1-hetzner-stable (commits first if dirty)
#   3. Backs up Hetzner's ontology/, sessions/, jobs/ just in case
#   4. Rsyncs updated app/, client/, scripts/ from KoeppyBox to Hetzner
#      (preserving Hetzner's .env, ontology/, sessions/, jobs/)
#   5. Runs phase 1 migration on Hetzner against Hetzner's single-ontology data
#   6. Restarts bfo-agent.service
#   7. Verifies /health and /ontologies respond correctly
#
# Rollback: if anything goes wrong, run with --rollback. Restores from
# backup and resets git to v1-hetzner-stable.

set -euo pipefail

HETZNER="drkoepsell@178.105.7.249"
LOCAL_PROJECT="$HOME/projects/bfo-agent"
REMOTE_PROJECT="/home/drkoepsell/bfo-agent"
TS="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_DIR="${REMOTE_PROJECT}/backup_pre_v2_${TS}"

run_remote() {
  ssh "$HETZNER" "$@"
}

preflight() {
  echo "=== Preflight checks ==="

  # Local: ensure we're in a clean v2 state
  cd "$LOCAL_PROJECT"
  if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: KoeppyBox git tree is dirty. Commit or stash first."
    git status --short
    exit 1
  fi

  # Check we have the expected tags
  for tag in v2-phase1-filesystem v2-phase2-registry v2-phase3-read-selector v2-phase4-lifecycle v2-phase5-ui; do
    if ! git rev-parse -q --verify "refs/tags/$tag" > /dev/null; then
      echo "ERROR: missing tag $tag. Have you committed all phases?"
      exit 1
    fi
  done
  echo "  KoeppyBox: tree clean, all v2 tags present"

  # Remote: ensure we can reach Hetzner
  if ! run_remote "echo ok" > /dev/null 2>&1; then
    echo "ERROR: cannot ssh to $HETZNER"
    exit 1
  fi
  echo "  Hetzner: reachable"

  # Remote: check bfo-agent service is known
  if ! run_remote "systemctl status bfo-agent.service" > /dev/null 2>&1; then
    echo "ERROR: bfo-agent.service not found on Hetzner"
    exit 1
  fi
  echo "  Hetzner: bfo-agent.service exists"

  # Remote: check current health (v1 should be responding)
  if ! run_remote "curl -s http://127.0.0.1:5000/health | grep -q '\"status\":\"ok\"'"; then
    echo "ERROR: Hetzner v1 is not healthy. Fix before migrating."
    exit 1
  fi
  echo "  Hetzner: v1 /health is ok"
}

tag_hetzner_stable() {
  echo "=== Tagging Hetzner state as v1-hetzner-stable ==="
  run_remote bash -s <<'REMOTE'
set -e
cd /home/drkoepsell/bfo-agent

if [ ! -d .git ]; then
  echo "  initializing git (Hetzner had no repo)"
  git init -q
  git config user.email "bfo-agent@local"
  git config user.name "BFO Agent"
  # Ignore .env which contains secrets
  if ! grep -q "^\.env$" .gitignore 2>/dev/null; then
    echo ".env" >> .gitignore
  fi
fi

# Commit whatever we have so the rollback tag is meaningful
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -q -m "Hetzner v1 stable snapshot before v2 migration" || true
fi

# Tag (force replace if someone already tagged)
git tag -f v1-hetzner-stable
echo "  tagged v1-hetzner-stable at $(git rev-parse --short HEAD)"
REMOTE
}

backup_hetzner_data() {
  echo "=== Backing up Hetzner ontology/sessions/jobs to ${BACKUP_DIR} ==="
  run_remote bash -s <<REMOTE
set -e
cd ${REMOTE_PROJECT}
mkdir -p ${BACKUP_DIR}
for d in ontology sessions jobs; do
  if [ -d "\$d" ]; then
    cp -r "\$d" ${BACKUP_DIR}/
    echo "  backed up \$d/"
  fi
done
REMOTE
}

push_code() {
  echo "=== Syncing v2 code to Hetzner ==="
  # Rsync only code directories. Explicitly NOT touching:
  # - .env (contains API key and auth settings)
  # - ontology/ (Hetzner's live data)
  # - sessions/ (Hetzner's session history)
  # - jobs/ (Hetzner's job state)
  # - .git (we maintain independent repo states)
  # - .venv, __pycache__ (local to each machine)
  #
  # We DO push:
  # - app/ (Python code, all phases 1-6 applied)
  # - client/ (the phase-5 UI with phase-6 delete button)
  # - scripts/ (phase migrations, so Hetzner can run phase 1 locally)
  # - requirements.txt (in case deps changed)
  rsync -avz --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pre_phase*' \
    "$LOCAL_PROJECT/app/" "$HETZNER:${REMOTE_PROJECT}/app/"
  rsync -avz --delete \
    --exclude '.pre_phase*' \
    "$LOCAL_PROJECT/client/" "$HETZNER:${REMOTE_PROJECT}/client/"
  rsync -avz \
    --exclude '__pycache__' \
    "$LOCAL_PROJECT/scripts/" "$HETZNER:${REMOTE_PROJECT}/scripts/"
  rsync -avz \
    "$LOCAL_PROJECT/requirements.txt" "$HETZNER:${REMOTE_PROJECT}/"
  echo "  pushed app/, client/, scripts/, requirements.txt"
}

run_phase1_on_hetzner() {
  echo "=== Running phase 1 migration on Hetzner ==="
  # If Hetzner is already in v2 layout (library/), skip.
  if run_remote "test -f ${REMOTE_PROJECT}/ontology/active.txt"; then
    echo "  Hetzner already has library/ layout, skipping phase 1"
    return
  fi

  # Ensure bfo_relations.ttl is in the seed directory (same precondition
  # as on KoeppyBox). Move it if it's at the root.
  run_remote bash -s <<REMOTE
set -e
cd ${REMOTE_PROJECT}
mkdir -p ontology/seed
if [ -f ontology/bfo_relations.ttl ] && [ ! -f ontology/seed/bfo_relations.ttl ]; then
  mv ontology/bfo_relations.ttl ontology/seed/bfo_relations.ttl
  echo "  moved bfo_relations.ttl into seed/"
fi

# Ensure working.owl exists. On Hetzner it was served directly as
# SOoL_final.owl copied to working.owl earlier; confirm.
if [ ! -f ontology/working.owl ]; then
  if [ -f ontology/SOoL_final.owl ]; then
    cp ontology/SOoL_final.owl ontology/working.owl
    echo "  bootstrapped working.owl from SOoL_final.owl"
  else
    echo "ERROR: no working.owl and no SOoL_final.owl"
    exit 1
  fi
fi
REMOTE

  # Apply phase 1 migration
  run_remote bash -s <<REMOTE
set -e
cd ${REMOTE_PROJECT}
source .venv/bin/activate
python scripts/phase1_migrate.py --apply
python scripts/phase1_patch_config.py
REMOTE
  echo "  phase 1 migration done on Hetzner"
}

restart_service() {
  echo "=== Restarting bfo-agent.service ==="
  run_remote "sudo systemctl restart bfo-agent.service"
  sleep 3
  run_remote "sudo systemctl status bfo-agent.service --no-pager | head -10"
}

verify() {
  echo "=== Verifying v2 endpoints ==="
  local resp
  resp=$(run_remote "curl -s http://127.0.0.1:5000/health")
  echo "  /health: $resp"
  if ! echo "$resp" | grep -q '"num_classes":6881'; then
    echo "ERROR: health response looks wrong, aborting"
    return 1
  fi

  resp=$(run_remote "curl -s http://127.0.0.1:5000/ontologies")
  echo "  /ontologies: $resp"
  if ! echo "$resp" | grep -q '"active":"SOoL_v1"'; then
    echo "ERROR: /ontologies doesn't show SOoL_v1 as active"
    return 1
  fi

  echo "  PASSED"
}

rollback() {
  echo "=== Rolling back ==="
  run_remote bash -s <<REMOTE
set -e
cd ${REMOTE_PROJECT}
# Find most recent backup
BACKUP=\$(ls -dt backup_pre_v2_* 2>/dev/null | head -1 || true)
if [ -z "\$BACKUP" ]; then
  echo "  no backup found; can still reset via git"
else
  echo "  restoring ontology/sessions/jobs from \$BACKUP"
  rm -rf ontology sessions jobs
  cp -r "\$BACKUP/ontology" ./ 2>/dev/null || true
  cp -r "\$BACKUP/sessions" ./ 2>/dev/null || true
  cp -r "\$BACKUP/jobs" ./ 2>/dev/null || true
fi
# Reset code to v1 tag
git reset --hard v1-hetzner-stable 2>/dev/null || echo "  (git reset skipped)"
sudo systemctl restart bfo-agent.service
REMOTE
  echo "  rollback complete. Verify with: curl http://178.105.7.249 (in browser)"
}

commit_migration() {
  echo "=== Committing v2 state on Hetzner ==="
  run_remote bash -s <<'REMOTE'
set -e
cd /home/drkoepsell/bfo-agent
# Add everything we can (will honor .gitignore, which should ignore .env)
git add -A 2>/dev/null || true
if [ -n "$(git status --porcelain)" ]; then
  git commit -q -m "v2 migration: library layout, multi-ontology support"
  git tag v2-hetzner
  echo "  tagged v2-hetzner at $(git rev-parse --short HEAD)"
else
  echo "  nothing to commit"
fi
REMOTE
}

main() {
  if [ "${1:-}" = "--rollback" ]; then
    rollback
    exit 0
  fi

  preflight
  tag_hetzner_stable
  backup_hetzner_data
  push_code
  run_phase1_on_hetzner
  restart_service

  if verify; then
    commit_migration
    echo ""
    echo "=== Migration complete ==="
    echo "Hetzner is now on v2. Browse http://178.105.7.249 (password prompt)."
    echo "Rollback with: $0 --rollback"
  else
    echo ""
    echo "=== Verification FAILED ==="
    echo "Consider rolling back: $0 --rollback"
    exit 1
  fi
}

main "$@"
