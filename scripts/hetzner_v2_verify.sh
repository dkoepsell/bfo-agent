#!/usr/bin/env bash
# hetzner_v2_verify.sh
#
# Post-migration verification. Runs a series of sanity checks against
# the Hetzner v2 deployment and reports pass/fail for each.
#
# Usage:
#   # From KoeppyBox, after migration:
#   bash scripts/hetzner_v2_verify.sh
#
# Or run on Hetzner directly (skips the SSH wrapper):
#   bash scripts/hetzner_v2_verify.sh --local

set -u

HETZNER="drkoepsell@178.105.7.249"
REMOTE_PROJECT="/home/drkoepsell/bfo-agent"
PASS=0
FAIL=0

run() {
  if [ "${1:-}" = "--local" ]; then
    shift
    bash -c "$@"
  else
    ssh "$HETZNER" "$@"
  fi
}

MODE="${1:-remote}"

check() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  if echo "$actual" | grep -qF "$expected"; then
    echo "  PASS  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label"
    echo "        expected: $expected"
    echo "        got:      $actual"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== Hetzner v2 verification ==="
echo "Mode: $MODE"
echo ""

# --- Service status ---
echo "[1/8] Systemd service"
if [ "$MODE" = "--local" ]; then
  status=$(systemctl is-active bfo-agent.service 2>/dev/null || true)
else
  status=$(ssh "$HETZNER" "systemctl is-active bfo-agent.service" 2>/dev/null || true)
fi
check "bfo-agent.service active" "active" "$status"

# --- Filesystem layout ---
echo ""
echo "[2/8] Filesystem layout"
if [ "$MODE" = "--local" ]; then
  has_lib=$(test -d /home/drkoepsell/bfo-agent/ontology/library/SOoL_v1 && echo "yes" || echo "no")
  active=$(cat /home/drkoepsell/bfo-agent/ontology/active.txt 2>/dev/null | tr -d '[:space:]')
  has_manifest=$(test -f /home/drkoepsell/bfo-agent/ontology/library/SOoL_v1/manifest.json && echo "yes" || echo "no")
else
  has_lib=$(ssh "$HETZNER" "test -d ${REMOTE_PROJECT}/ontology/library/SOoL_v1 && echo yes || echo no")
  active=$(ssh "$HETZNER" "cat ${REMOTE_PROJECT}/ontology/active.txt 2>/dev/null | tr -d '[:space:]'")
  has_manifest=$(ssh "$HETZNER" "test -f ${REMOTE_PROJECT}/ontology/library/SOoL_v1/manifest.json && echo yes || echo no")
fi
check "library/SOoL_v1/ exists" "yes" "$has_lib"
check "active ontology is SOoL_v1" "SOoL_v1" "$active"
check "library/SOoL_v1/manifest.json exists" "yes" "$has_manifest"

# --- Internal endpoints ---
echo ""
echo "[3/8] Internal /health (loopback)"
if [ "$MODE" = "--local" ]; then
  health=$(curl -s http://127.0.0.1:5000/health)
else
  health=$(ssh "$HETZNER" "curl -s http://127.0.0.1:5000/health")
fi
check "/health reports 6881 classes" '"num_classes":6881' "$health"
check "/health reports 1678 individuals" '"num_individuals":1678' "$health"

# --- Phase 2+ endpoints ---
echo ""
echo "[4/8] /ontologies endpoint"
if [ "$MODE" = "--local" ]; then
  onts=$(curl -s http://127.0.0.1:5000/ontologies)
else
  onts=$(ssh "$HETZNER" "curl -s http://127.0.0.1:5000/ontologies")
fi
check "/ontologies returns active=SOoL_v1" '"active":"SOoL_v1"' "$onts"

# --- Phase 3 read selector ---
echo ""
echo "[5/8] Phase 3: ?ontology parameter"
if [ "$MODE" = "--local" ]; then
  by_name=$(curl -s 'http://127.0.0.1:5000/health?ontology=SOoL_v1')
  bogus_status=$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5000/health?ontology=nonexistent')
else
  by_name=$(ssh "$HETZNER" "curl -s 'http://127.0.0.1:5000/health?ontology=SOoL_v1'")
  bogus_status=$(ssh "$HETZNER" "curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5000/health?ontology=nonexistent'")
fi
check "/health?ontology=SOoL_v1 returns stats" '"num_classes":6881' "$by_name"
check "/health?ontology=nonexistent returns 404" "404" "$bogus_status"

# --- Phase 5 UI ---
echo ""
echo "[6/8] Phase 5: UI served with dropdown"
if [ "$MODE" = "--local" ]; then
  html=$(curl -s http://127.0.0.1:5000/)
else
  html=$(ssh "$HETZNER" "curl -s http://127.0.0.1:5000/")
fi
check "client has ontology-selector" "ontology-selector" "$html"
check "client has refreshOntologies" "refreshOntologies" "$html"
check "client has deleteOntology" "deleteOntology" "$html"

# --- Caddy + basic auth ---
echo ""
echo "[7/8] Caddy reverse proxy"
if [ "$MODE" != "--local" ]; then
  # From KoeppyBox, hit the public endpoint (401 without auth is correct)
  public_status=$(curl -s -o /dev/null -w '%{http_code}' http://178.105.7.249/)
  check "public / returns 401 without auth" "401" "$public_status"

  api_status=$(curl -s -o /dev/null -w '%{http_code}' http://178.105.7.249/api/health)
  check "public /api/health returns 401 without auth" "401" "$api_status"
else
  echo "  SKIP  Caddy tests require remote mode"
fi

# --- Reasoner consistency ---
echo ""
echo "[8/8] Reasoner consistency (canonical claim)"
if [ "$MODE" = "--local" ]; then
  consistent=$(cd /home/drkoepsell/bfo-agent && source .venv/bin/activate && python3 -c "
import sys; sys.path.insert(0, '.')
from app.ontology_manager import OntologyManager
from app import config
mgr = OntologyManager(config.BFO_PATH, config.WORKING_PATH, config.SEED_PATH)
class Empty:
    entities = []
    relations = []
ok, _ = mgr.check_consistency_dry_run(Empty())
print(ok)
" 2>/dev/null)
else
  consistent=$(ssh "$HETZNER" "cd ${REMOTE_PROJECT} && source .venv/bin/activate && python3 -c \"
import sys; sys.path.insert(0, '.')
from app.ontology_manager import OntologyManager
from app import config
mgr = OntologyManager(config.BFO_PATH, config.WORKING_PATH, config.SEED_PATH)
class Empty:
    entities = []
    relations = []
ok, _ = mgr.check_consistency_dry_run(Empty())
print(ok)
\" 2>/dev/null")
fi
check "SOoL_v1 is HermiT-consistent" "True" "$consistent"

# --- Summary ---
echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "  Result: PASS"
  exit 0
else
  echo "  Result: FAIL ($FAIL failures)"
  exit 1
fi
