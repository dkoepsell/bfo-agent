"""Coverage Kernel web service.

The Coverage Kernel lives in its own repository (default ~/ck) and is deliberately
not part of this application. This module is a thin front for it: it serves a
precomputed payload, and it runs one live assessment at a time.

Two things shape the design.

The demonstrator, the gate verdict and the pattern library do not change between
page views, so they are precomputed by ck/harness/build_web_payload.py and served
as a file. Putting a description logic reasoner on the critical path of a page load
would be wasteful anywhere and reckless on a small box next to a running feed.

The live assessment does reason, and it takes the same reasoner lock the feed and
the coherence tools take, without blocking. If the lock is held, the caller is told
to try again rather than queued behind a job that may run for minutes. One
assessment costs roughly 180 MB and three seconds.

The input to an assessment is a structured fact pattern, never Turtle. Accepting
Turtle from a browser would mean feeding arbitrary axioms to a reasoner.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from flask import jsonify, request

from .ontology_manager import _REASONER_LOCK

CK_HOME = Path(os.environ.get("CK_HOME", str(Path.home() / "ck"))).expanduser()
PAYLOAD = CK_HOME / "out" / "web" / "ck-payload.json"
ASSESS = CK_HOME / "harness" / "assess.py"

# One assessment is about three seconds. Anything past this is a reasoner in
# trouble, and on a two core box it must not be allowed to sit there.
ASSESS_TIMEOUT_S = int(os.environ.get("CK_ASSESS_TIMEOUT", "90"))
MAX_EXCLUSIONS = 8

# Live assessment spawns a JVM that peaks around 180 MB. That JVM is a child of this
# process, so it is charged to this unit's cgroup. On a host where the unit is already
# near its MemoryMax the kernel will not politely refuse: it will pick something in the
# cgroup and kill it, and the something may be the ontology feed.
#
# So it is opt in. Set CK_LIVE_ASSESS=1 once the host has the headroom, which means
# either swap or a raised MemoryMax. The page handles the disabled case and says so.
LIVE_ENABLED = os.environ.get("CK_LIVE_ASSESS", "1") not in ("0", "false", "no", "")

# How much room the JVM needs before it is safe to start one. Measured peak is about
# 180 MB; the margin is for the rest of the request and for the feed to breathe.
HEADROOM_MB = int(os.environ.get("CK_ASSESS_HEADROOM_MB", "420"))


def _headroom_mb() -> tuple[int | None, str]:
    """Free memory in this process's cgroup, falling back to the host.

    The JVM is a child of this process, so the kernel charges it to this unit's
    cgroup. A unit sitting near its MemoryMax will not get a polite refusal when
    that JVM starts: the kernel picks a victim inside the cgroup, and the victim
    may be the ontology feed. So the check is on the cgroup, not on the host.
    """
    try:
        cg = Path("/proc/self/cgroup").read_text().strip().split(":")[-1]
        base = Path("/sys/fs/cgroup") / cg.lstrip("/")
        cur = (base / "memory.current").read_text().strip()
        mx = (base / "memory.max").read_text().strip()
        if mx != "max":
            free = (int(mx) - int(cur)) // (1024 * 1024)
            return free, f"cgroup {base.name}"
    except Exception:
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024, "host MemAvailable"
    except Exception:
        pass
    return None, "unknown"

_last_assess = {"at": 0.0}
MIN_INTERVAL_S = float(os.environ.get("CK_ASSESS_MIN_INTERVAL", "2"))


def _java_present() -> bool:
    try:
        return subprocess.run(["java", "-version"], capture_output=True,
                              timeout=20).returncode == 0
    except Exception:
        return False


def _sanitise(body: dict) -> tuple[dict | None, str | None]:
    """Accept only the fields the assessor understands, with sane bounds."""
    if not isinstance(body, dict):
        return None, "expected a JSON object"

    exclusions = body.get("exclusions") or []
    if not isinstance(exclusions, list):
        return None, "exclusions must be a list"
    if len(exclusions) > MAX_EXCLUSIONS:
        return None, f"at most {MAX_EXCLUSIONS} exclusions"

    clean_exclusions = []
    for ex in exclusions:
        if not isinstance(ex, dict):
            return None, "each exclusion must be an object"
        clean_exclusions.append({
            "label": str(ex.get("label", ""))[:90],
            "applies": bool(ex.get("applies", True)),
            "carved_back": bool(ex.get("carved_back", False)),
        })

    spec = {
        "peril": str(body.get("peril", "") or "the peril")[:90],
        "granted": bool(body.get("granted", True)),
        "deemed_covered": bool(body.get("deemed_covered", False)),
        "closure_asserted": bool(body.get("closure_asserted", True)),
        "exclusions": clean_exclusions,
    }
    upstream = body.get("upstream_peril")
    if upstream:
        spec["upstream_peril"] = str(upstream)[:90]
        spec["grant_on_upstream"] = bool(body.get("grant_on_upstream", True))
    return spec, None


def register(app) -> None:
    """Attach the Coverage Kernel routes to the Flask app."""

    def _disabled_reason(free):
        if not LIVE_ENABLED:
            return ("Live assessment is switched off on this host by configuration. "
                    "Everything else on this page is precomputed and unaffected.")
        if not ASSESS.exists():
            return "The Coverage Kernel is not installed on this host."
        if not _java_present():
            return "No Java runtime on this host, so the reasoner cannot start."
        if free is not None and free < HEADROOM_MB:
            return (f"Live assessment is paused: this service has {free} MB of memory "
                    f"headroom and a reasoner run needs about {HEADROOM_MB} MB. Starting "
                    f"one now could get the ontology feed killed, so it waits. "
                    f"Everything else on this page is precomputed and unaffected.")
        return None

    @app.get("/ck/health")
    def ck_health():
        free, src = _headroom_mb()
        return jsonify({
            "ck_home": str(CK_HOME),
            "installed": CK_HOME.is_dir(),
            "payload": PAYLOAD.exists(),
            "assessor": ASSESS.exists(),
            "java": _java_present(),
            "live_enabled": LIVE_ENABLED,
            "live_assessment": LIVE_ENABLED and ASSESS.exists() and _java_present()
                               and (free is None or free >= HEADROOM_MB),
            "headroom_mb": free,
            "headroom_source": src,
            "headroom_required_mb": HEADROOM_MB,
            "disabled_reason": _disabled_reason(free),
            "reasoner_busy": _REASONER_LOCK.locked(),
        })

    @app.get("/ck/payload")
    def ck_payload():
        """Everything the page shows that does not need a reasoner."""
        if not PAYLOAD.exists():
            return jsonify({
                "error": "the Coverage Kernel payload has not been built",
                "fix": "in the ck repository run: make gates && "
                       "python3 harness/build_web_payload.py",
            }), 404
        try:
            return jsonify(json.loads(PAYLOAD.read_text(encoding="utf-8")))
        except Exception as e:
            return jsonify({"error": f"payload unreadable: {e}"}), 500

    @app.post("/ck/assess")
    def ck_assess():
        """Assess one fact pattern, live, one at a time."""
        if not LIVE_ENABLED:
            return jsonify({
                "error": "live assessment is switched off on this host",
                "detail": "It spawns a reasoner that peaks around 180 MB inside this "
                          "service's memory cgroup, and this host has no headroom for "
                          "that beside the ontology feed. The rest of the page is "
                          "precomputed and unaffected.",
            }), 503
        if not ASSESS.exists():
            return jsonify({"error": "the Coverage Kernel is not installed on this host",
                            "ck_home": str(CK_HOME)}), 503

        free, _src = _headroom_mb()
        if free is not None and free < HEADROOM_MB:
            return jsonify({
                "error": "not enough memory headroom to start a reasoner right now",
                "detail": f"This service has {free} MB free against a {HEADROOM_MB} MB "
                          f"requirement. Starting a reasoner here risks the ontology "
                          f"feed being killed, so the request is refused rather than "
                          f"gambled on. Try again later.",
            }), 503

        spec, problem = _sanitise(request.get_json(silent=True) or {})
        if problem:
            return jsonify({"error": problem}), 400

        since = time.time() - _last_assess["at"]
        if since < MIN_INTERVAL_S:
            return jsonify({"error": "too fast", "retry_after_s": round(MIN_INTERVAL_S - since, 1)}), 429

        # Non blocking. The feed can hold this lock for a long time and a web
        # request must not wait on it.
        if not _REASONER_LOCK.acquire(blocking=False):
            return jsonify({
                "error": "the reasoner is busy with another job",
                "detail": "Assessments are serialised behind the ontology feed so that "
                          "a page cannot starve it. Try again in a few seconds.",
            }), 409
        try:
            _last_assess["at"] = time.time()
            proc = subprocess.run(
                [sys.executable, str(ASSESS), json.dumps(spec)],
                capture_output=True, text=True,
                timeout=ASSESS_TIMEOUT_S, cwd=str(CK_HOME),
            )
        except subprocess.TimeoutExpired:
            return jsonify({"error": f"the reasoner did not finish within "
                                     f"{ASSESS_TIMEOUT_S} seconds"}), 504
        finally:
            _REASONER_LOCK.release()

        if proc.returncode != 0:
            return jsonify({"error": "assessment failed",
                            "detail": (proc.stderr or proc.stdout)[-800:]}), 500
        try:
            return jsonify(json.loads(proc.stdout))
        except json.JSONDecodeError:
            return jsonify({"error": "the assessor returned something unreadable",
                            "detail": proc.stdout[-800:]}), 500
