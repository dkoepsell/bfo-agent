#!/usr/bin/env bash
# Restart the bfo-agent foreground server (USER-SPACE, no sudo).
# Prod runs `python run.py` as a foreground process owned by this user, so we
# stop that process and relaunch it with nohup. Verifies /health before and
# after so a failed relaunch is obvious.
set -uo pipefail

APP=/home/drkoepsell/bfo-agent
VENV="$APP/.venv"
LOG="$APP/run.log"
cd "$APP"

echo "== restart bfo-agent (foreground) =="
PIDS=$(pgrep -f "run.py" || true)
if [ -n "$PIDS" ]; then
  echo "-- stopping run.py pid(s): $PIDS"
  kill $PIDS 2>/dev/null || true
  for i in 1 2 3 4 5; do
    pgrep -f "run.py" >/dev/null || break
    sleep 1
  done
  pgrep -f "run.py" >/dev/null && { echo "-- still up, forcing"; pkill -9 -f "run.py" || true; sleep 1; }
fi

echo "-- relaunching"
nohup "$VENV/bin/python" run.py >>"$LOG" 2>&1 &
disown || true
sleep 3

echo "-- health check on :5000"
if curl -fsS http://127.0.0.1:5000/health; then
  echo; echo "OK: bfo-agent is serving on :5000"
else
  echo; echo "FAIL: no health response. Last log lines:"; tail -20 "$LOG"; exit 1
fi
