"""Deploy the staged proposer-cache-scoping code once the current feed run is done.

Run from cron. Fires exactly once (sentinel-guarded), and ONLY when every
*approved* claim in the job has reached a terminal state (committed/inconsistent/
error) -- i.e. the run is genuinely finished, not merely paused. Swaps the staged
code from ~/bfo-agent-pending into the live tree, restarts, verifies /health, and
rolls back from backup if the restart is unhealthy.

Usage: deploy_on_complete.py <job_id>
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import urllib.request

HOME = os.path.expanduser("~")
LIVE = f"{HOME}/bfo-agent"
PEND = f"{HOME}/bfo-agent-pending"
SENTINEL = f"{PEND}/.deployed"
BACKUP = f"{HOME}/bfo-agent-codebackup-cachescoping"
JOB = sys.argv[1] if len(sys.argv) > 1 else "job_edc6948bb5a3"

ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def log(msg):
    print(f"[{ts}] DEPLOY-WATCH: {msg}", flush=True)


def health_ok():
    try:
        h = json.load(urllib.request.urlopen("http://127.0.0.1:5000/health", timeout=15))
        return h.get("status") == "ok" and h.get("ontology")
    except Exception as e:  # noqa: BLE001
        log(f"health check error: {e}")
        return False


def main():
    if os.path.exists(SENTINEL):
        return 0  # already deployed
    try:
        d = json.load(open(f"{LIVE}/jobs/{JOB}.json"))
    except Exception as e:  # noqa: BLE001
        log(f"cannot read job: {e}")
        return 0
    cl = d.get("claims") or []
    approved = [c for c in cl if c.get("approved")]
    approved_pending = [c for c in approved if c.get("status") == "pending"]
    if not approved or approved_pending:
        return 0  # run not complete (nothing approved yet, or work remaining)

    log(f"run complete: {len(approved)} approved, 0 pending. deploying cache-scoping.")
    # Back up live code, then swap in the staged .py files.
    if os.path.exists(BACKUP):
        shutil.rmtree(BACKUP)
    shutil.copytree(f"{LIVE}/app", BACKUP)
    for sub in ("app", "tests"):
        src, dst = f"{PEND}/{sub}", f"{LIVE}/{sub}"
        for f in os.listdir(src):
            if f.endswith(".py"):
                shutil.copy(f"{src}/{f}", f"{dst}/{f}")

    subprocess.run(["bash", f"{LIVE}/deploy/hetzner_restart.sh"],
                   capture_output=True, text=True, timeout=180)

    if health_ok():
        open(SENTINEL, "w").write(ts)
        log("DEPLOYED proposer-cache-scoping; /health OK.")
        return 0

    # Unhealthy -> roll back.
    log("UNHEALTHY after deploy -- rolling back to backup.")
    for f in os.listdir(BACKUP):
        if f.endswith(".py"):
            shutil.copy(f"{BACKUP}/{f}", f"{LIVE}/app/{f}")
    subprocess.run(["bash", f"{LIVE}/deploy/hetzner_restart.sh"],
                   capture_output=True, text=True, timeout=180)
    log("rolled back. NOT writing sentinel; investigate before retrying.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
