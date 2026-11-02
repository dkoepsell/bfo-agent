#!/usr/bin/env bash
# Server-side deploy for bfo-agent on Hetzner. Run by GitHub Actions over SSH.
#
# INSTALLED COPY: ~/bin/bfo-agent-deploy (the forced command of the deploy key
# in ~/.ssh/authorized_keys). The installed copy is deliberately NOT the file in
# the app tree, so a push cannot change what the deploy key is allowed to run.
# After editing this file, re-install it by hand:
#   scp deploy/remote_deploy.sh hetzner:~/bin/bfo-agent-deploy
#
# What it does:
#   1. fetch origin/main into a bare mirror and export it to a fresh stage dir
#   2. byte-compile the staged Python (fail before touching the live app)
#   3. back up the live code paths, then rsync the code allowlist over them
#      (no --delete; live data -- ontology/, sessions, .env -- is never overwritten;
#      under ontology/ only kernels/, profiles/ and canonical/ get files the
#      server lacks; ontology/library/ is never touched)
#   4. pip install only if requirements.txt changed
#   5. restart the systemd --user unit and wait for /health
#   6. on health failure, restore the backup and restart again (exit 1)
set -euo pipefail

APP=/home/drkoepsell/bfo-agent
VENV="$APP/.venv"
MIRROR=/home/drkoepsell/bfo-agent-src.git
STAGE=/home/drkoepsell/bfo-agent-deploy-stage
BACKUPS=/home/drkoepsell/bfo-agent-deploy-backups
REPO=https://github.com/dkoepsell/bfo-agent.git
BRANCH=main
UNIT=bfo-agent
HEALTH=http://127.0.0.1:5000/health

# Code that ships with the repo. Anything not listed here is never touched.
CODE_DIRS=(app client chainlab nfip scripts tests deploy evaluation)
CODE_FILES=(run.py requirements.txt sool_owl_checks.py bfo_agent_cached.py interim_report.py pytest.ini)

export XDG_RUNTIME_DIR="/run/user/$(id -u)"   # systemctl --user over a non-login ssh

exec 9>/tmp/bfo-agent-deploy.lock
flock -n 9 || { echo "another deploy is running"; exit 1; }

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

# 1. fetch + export --------------------------------------------------------
[ -d "$MIRROR" ] || git clone --bare --quiet "$REPO" "$MIRROR"
git --git-dir="$MIRROR" fetch --quiet --prune "$REPO" "+refs/heads/$BRANCH:refs/heads/$BRANCH"
SHA=$(git --git-dir="$MIRROR" rev-parse "refs/heads/$BRANCH")
PREV=$(cat "$APP/.deployed_sha" 2>/dev/null || echo none)
log "deploying $BRANCH @ ${SHA:0:8} (previous: ${PREV:0:8})"

rm -rf "$STAGE"; mkdir -p "$STAGE"
git --git-dir="$MIRROR" archive "$SHA" | tar -x -C "$STAGE"

# 2. sanity: staged code must compile --------------------------------------
"$VENV/bin/python" -m compileall -q "$STAGE/app" "$STAGE/run.py" >/dev/null \
  || { log "FAIL: staged code does not compile; live app untouched"; exit 1; }

# 3. backup + sync ---------------------------------------------------------
mkdir -p "$BACKUPS"
BK="$BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)-${PREV:0:8}.tgz"
present=()
for p in "${CODE_DIRS[@]}" "${CODE_FILES[@]}"; do [ -e "$APP/$p" ] && present+=("$p"); done
tar -czf "$BK" -C "$APP" --exclude=__pycache__ "${present[@]}"
ls -1t "$BACKUPS"/*.tgz | tail -n +11 | xargs -r rm -f   # keep last 10
log "backup: $BK"

REQ_BEFORE=$(sha256sum "$APP/requirements.txt" 2>/dev/null | cut -d' ' -f1 || true)
for d in "${CODE_DIRS[@]}"; do
  [ -d "$STAGE/$d" ] && rsync -a --exclude=__pycache__ "$STAGE/$d/" "$APP/$d/"
done
for f in "${CODE_FILES[@]}"; do
  [ -f "$STAGE/$f" ] && cp -p "$STAGE/$f" "$APP/$f"
done
# Only shipped reference folders, and only files the server lacks. Never
# ontology/library/: the server's library is live data, and an ontology
# removed there on purpose must not come back from the repo.
for d in kernels profiles canonical; do
  [ -d "$STAGE/ontology/$d" ] && rsync -a --ignore-existing "$STAGE/ontology/$d/" "$APP/ontology/$d/"
done

# 4. deps ------------------------------------------------------------------
REQ_AFTER=$(sha256sum "$APP/requirements.txt" | cut -d' ' -f1)
if [ "$REQ_BEFORE" != "$REQ_AFTER" ]; then
  log "requirements.txt changed; pip install"
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt"
fi

# 5. restart + health ------------------------------------------------------
wait_healthy() {
  for _ in $(seq 1 30); do
    curl -fsS "$HEALTH" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

log "restarting $UNIT"
systemctl --user restart "$UNIT"
if wait_healthy; then
  echo "$SHA" > "$APP/.deployed_sha"
  log "OK: ${SHA:0:8} is live"
  exit 0
fi

# 6. rollback --------------------------------------------------------------
log "FAIL: no /health after restart; rolling back to ${PREV:0:8}"
journalctl --user -u "$UNIT" -n 30 --no-pager || true
tar -xzf "$BK" -C "$APP"
systemctl --user restart "$UNIT"
wait_healthy && log "rollback healthy" || log "ROLLBACK ALSO UNHEALTHY - check the server"
exit 1
