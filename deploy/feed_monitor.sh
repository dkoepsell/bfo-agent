#!/usr/bin/env bash
# Half-hourly snapshot of the active feed job + prompt-cache savings.
# Appends one timestamped line to feed_monitor.log. Install via cron:
#   0,30 * * * * $HOME/bfo-agent/deploy/feed_monitor.sh <job_id>
cd "$HOME/bfo-agent" || exit 1
JOB="${1:-job_edc6948bb5a3}"
exec .venv/bin/python deploy/feed_monitor.py "$JOB" >> "$HOME/bfo-agent/feed_monitor.log" 2>&1
