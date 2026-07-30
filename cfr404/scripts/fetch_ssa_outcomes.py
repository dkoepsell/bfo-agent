#!/usr/bin/env python3
"""K-D1 input: SSA published ALJ disposition statistics.

K-D1 is the gap between a capacity granted in structure and one available in
practice. It cannot be computed from the regulation text, so this loader is not
optional decoration: without the data there is no K-D1 detection, only an assertion.

Endpoints are probed rather than assumed. If none succeed, the failure is written to
``data/ssa-outcomes/acquisition-status.json`` with the exact reason, and the K-D1
detector reports NOT COMPUTED rather than inventing a finding.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import ROOT  # noqa: E402

OUTDIR = ROOT / "data" / "ssa-outcomes"
STATUS = OUTDIR / "acquisition-status.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

# Documented publication locations, most current first. SSA has moved these before,
# so all are probed and whichever answers is recorded.
CANDIDATES = [
    ("SSA ODAR ALJ disposition data (landing page)",
     "https://www.ssa.gov/appeals/DataSets/03_ALJ_Disposition_Data.html"),
    ("SSA appeals datasets index",
     "https://www.ssa.gov/appeals/DataSets/"),
    ("SSA open data catalogue",
     "https://www.ssa.gov/open/data/"),
    ("data.gov CKAN package search",
     "https://catalog.data.gov/api/3/action/package_search?q=ALJ+disposition+hearings"),
]


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    attempts = []
    acquired = None

    for name, url in CANDIDATES:
        rec = {"name": name, "url": url,
               "attempted_utc": datetime.now(timezone.utc).isoformat()}
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30,
                             allow_redirects=True)
            rec["status"] = r.status_code
            rec["bytes"] = len(r.content)
            if r.status_code == 200 and len(r.content) > 500:
                path = OUTDIR / (url.rstrip("/").split("/")[-1] or "index.html")
                path.write_bytes(r.content)
                rec["saved"] = str(path.relative_to(ROOT))
                acquired = rec
        except requests.RequestException as exc:
            rec["status"] = None
            rec["error"] = f"{type(exc).__name__}: {exc}"
        attempts.append(rec)
        print(f"{rec.get('status')}  {name}")
        if acquired:
            break

    status = {
        "acquired": acquired is not None,
        "acquired_from": acquired,
        "attempts": attempts,
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "consequence_if_not_acquired": (
            "K-D1 reports NOT COMPUTED. The spec is explicit that a claim of a "
            "structure-to-practice gap without the measured inter-adjudicator variance "
            "is an assertion, not a detection, so no K-D1 flag is raised on the basis "
            "of the regulation text alone."),
        "how_to_complete": (
            "Run this script from a network that can reach ssa.gov, then re-run "
            "scripts/run_kernel.py. The K-D1 detector needs a CSV or XLSX at "
            "data/ssa-outcomes/ with one row per adjudicator and an allowance or "
            "favourable-rate column; see kernel/detectors/k_d1.py for the accepted "
            "column names."),
    }
    STATUS.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"\nacquired={status['acquired']}  -> {STATUS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
