#!/usr/bin/env bash
# bfo-restart — kill any running Flask and start the BFO-Agent fresh
#
# Drop in ~/.local/bin/ and chmod +x. Or just `bash bfo-restart`.

set -u

PROJECT="${BFO_AGENT_HOME:-$HOME/projects/bfo-agent}"

if [ ! -d "$PROJECT" ]; then
  echo "ERROR: project directory not found: $PROJECT"
  echo "Set BFO_AGENT_HOME if it's elsewhere."
  exit 1
fi

echo "[bfo-restart] killing any existing Flask"
if pkill -f "python run.py" 2>/dev/null; then
  echo "  killed"
  sleep 1
else
  echo "  nothing was running"
fi

# Belt and suspenders: also clear port 5000 in case something else holds it
if command -v fuser > /dev/null 2>&1; then
  fuser -k 5000/tcp 2>/dev/null || true
fi

cd "$PROJECT" || exit 1

# Activate virtualenv if it exists
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "[bfo-restart] starting from $(pwd)"
echo "[bfo-restart] log: /tmp/flask.log"
echo

exec env PYTHONUNBUFFERED=1 python run.py 2>&1 | tee /tmp/flask.log
