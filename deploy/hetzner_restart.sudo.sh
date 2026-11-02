#!/usr/bin/env bash
# Restart bfo-agent after a coherence-gate code deploy. RUN WITH SUDO.
#   sudo bash hetzner_restart.sudo.sh
#
# The systemd unit may report inactive while a foreground `python run.py` is
# actually serving :5000 (per the deploy notes). This script restarts the unit
# and, if a stray foreground process is holding :5000, reports it so you can
# decide. It does NOT kill foreground processes automatically.
set -euo pipefail

echo "== restart bfo-agent.service =="
systemctl restart bfo-agent.service || {
  echo "systemctl restart failed; unit status:"; systemctl status bfo-agent.service --no-pager -l | head -20; exit 1;
}
sleep 2
systemctl status bfo-agent.service --no-pager -l | head -12

echo
echo "-- listeners on :5000 --"
ss -ltnp 'sport = :5000' || true

echo
echo "-- local health check --"
if curl -fsS http://127.0.0.1:5000/health >/tmp/bfo_health.json 2>&1; then
  cat /tmp/bfo_health.json; echo
  echo "OK: service responded on :5000"
else
  echo "WARN: no health response on :5000. If a foreground 'python run.py' was"
  echo "serving instead of the unit, stop it and start the unit, or relaunch"
  echo "  cd /home/drkoepsell/bfo-agent && .venv/bin/python run.py"
fi
