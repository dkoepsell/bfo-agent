"""Snapshot the active feed job + prompt-cache savings as one log line.

Usage: feed_monitor.py <job_id>
Prints: [ts] status= committed= inconsistent= pending=/total | cache savings
"""
import collections
import datetime
import json
import sys
import urllib.request

job = sys.argv[1] if len(sys.argv) > 1 else "job_edc6948bb5a3"
ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

try:
    d = json.load(open(f"jobs/{job}.json"))
    cl = d.get("claims") or []
    b = collections.Counter(c.get("status") for c in cl)
    prog = (
        f"status={d.get('status')} committed={b.get('committed', 0)} "
        f"inconsistent={b.get('inconsistent', 0)} "
        f"pending={b.get('pending', 0)}/{len(cl)}"
    )
except Exception as e:  # noqa: BLE001
    prog = f"job-read-error: {e}"

try:
    h = json.load(urllib.request.urlopen("http://127.0.0.1:5000/health", timeout=10))
    pc = h.get("prompt_cache")
    if pc:
        cache = (
            f"cache hit={pc.get('cache_hit_ratio')} "
            f"cost=${pc.get('cost_usd')} vs_uncached=${pc.get('cost_without_cache_usd')} "
            f"saved=${pc.get('saved_usd')} ({pc.get('saved_pct')}%) "
            f"calls={pc.get('calls')}"
        )
    else:
        cache = "cache: no model calls yet this process"
except Exception as e:  # noqa: BLE001
    cache = f"health-error: {e}"

print(f"[{ts}] {prog} | {cache}")
