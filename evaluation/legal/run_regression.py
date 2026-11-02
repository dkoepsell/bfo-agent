"""SPEC-legal-drafting.md section 8.1: re-run the baseline sources through the
legal pipeline and measure D1-D8 against the baseline draft.

Talks to a running bfo-agent over HTTP (default http://127.0.0.1:5000), so it
exercises exactly what the UI does: create a fresh ontology on bare BFO
2020, extract each source in the ``legislation`` genre, approve every claim,
feed them one by one, then measure the result and the release copy with
``app.legal_eval``. The previously active ontology is re-activated at the end.

Writes ``evaluation/legal/regression-<timestamp>.json`` (and ``.md``).
Spends API tokens: roughly one extraction call per article chunk and one
proposer call per claim.

    python -m evaluation.legal.run_regression [--base URL] [--name NAME]
        [--max-claims N]

Runs in curated mode (the default fidelity of a new ontology).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import legal_eval  # noqa: E402

SOURCES = ROOT / "tests" / "fixtures" / "legal" / "sources"
BASELINE = ROOT / "tests" / "fixtures" / "legal" / "eu_personal_data_baseline.owl"
OUT = ROOT / "evaluation" / "legal"


def call(base, method, path, body=None, timeout=900):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")
    try:
        return json.loads(raw)
    except Exception:
        return raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--name", default="LegalRegression_" + datetime.now().strftime("%Y%m%d_%H%M"))
    ap.add_argument("--max-claims", type=int, default=0, help="0 = all")
    ap.add_argument("--library", default=str(ROOT / "ontology" / "library"),
                    help="where the server keeps ontologies (to read the result)")
    a = ap.parse_args(argv)

    meta = json.loads((SOURCES / "sources.json").read_text())
    before = call(a.base, "GET", "/ontologies")
    prev_active = before.get("active") if isinstance(before, dict) else None
    print(f"previous active ontology: {prev_active}")

    call(a.base, "POST", "/ontologies", {
        "name": a.name, "clone_seeds_from": "BFO_2020",
        "description": "SPEC-legal-drafting section 8.1 regression run"})
    call(a.base, "POST", f"/ontologies/{a.name}/activate", {})
    job = call(a.base, "POST", "/jobs", {"name": f"regression {a.name}"})
    job_id = job["job_id"]
    print(f"ontology {a.name}, job {job_id}")

    source_text = ""
    extracted = rejected = 0
    t0 = time.time()
    try:
        for act_id, m in meta.items():
            text = (SOURCES / m["file"]).read_text(encoding="utf-8")
            source_text += text + "\n"
            prep = call(a.base, "POST", "/extract/prepare", {
                "text": text, "genre": "legislation", "act_id": act_id,
                "act_title": m["title"], "chunk_chars": 6000})
            for i, chunk in enumerate(prep["chunks"]):
                r = call(a.base, "POST", "/extract/chunk", {
                    "chunk": chunk, "chunk_index": i, "genre": "legislation",
                    "act_id": act_id, "act_title": m["title"],
                    "locator": prep["chunk_locators"][i]})
                claims = r.get("claims", [])
                rejected += len(r.get("rejected_claims", []))
                if claims:
                    call(a.base, "POST", f"/jobs/{job_id}/append_claims", {"claims": claims})
                    extracted += len(claims)
                print(f"  {act_id} chunk {i + 1}/{len(prep['chunks'])}: {len(claims)} claims")

        j = call(a.base, "GET", f"/jobs/{job_id}")
        ids = [c["id"] for c in j["claims"]]
        if a.max_claims:
            ids = ids[: a.max_claims]
        call(a.base, "POST", f"/jobs/{job_id}/approve",
             {"approvals": {str(c["id"]): (c["id"] in ids) for c in j["claims"]}})

        statuses: dict[str, int] = {}
        legal_out = None
        for n in range(len(ids) + 2):
            r = call(a.base, "POST", f"/jobs/{job_id}/feed_one", {"auto_accept": True})
            if r.get("done") or r.get("fatal"):
                legal_out = r.get("legal")
                if r.get("fatal"):
                    print("FATAL", r)
                break
            st = (r.get("claim") or {}).get("status") or ("committed" if r.get("committed") else "?")
            st = "committed" if r.get("committed") else (r.get("proposal") or {}).get("reasoner_verdict") or st
            statuses[st] = statuses.get(st, 0) + 1
            print(f"  fed {n + 1}/{len(ids)}: {'committed' if r.get('committed') else st}"
                  f" ({r.get('remaining')} left)")
    finally:
        if prev_active and prev_active != a.name:
            try:
                call(a.base, "POST", f"/ontologies/{prev_active}/activate", {})
                print(f"re-activated {prev_active}")
            except Exception as e:
                print(f"WARNING: could not re-activate {prev_active}: {e}")

    onto = Path(a.library) / a.name
    working = onto / "working.owl"
    release = onto / "release" / f"{a.name}.owl"
    bfo = ROOT / "ontology" / "bfo.owl"
    result = {
        "run": a.name, "job_id": job_id, "fidelity": "curated",
        "finished": datetime.now(timezone.utc).isoformat(),
        "seconds": round(time.time() - t0),
        "claims_extracted": extracted, "claims_rejected_paraphrase": rejected,
        "feed_outcomes": statuses, "legal_outputs": legal_out,
        "baseline": legal_eval.measure(BASELINE, source_text, bfo),
        "rerun": legal_eval.measure(working, source_text, bfo) if working.exists() else None,
        "release": legal_eval.measure(release, source_text, bfo) if release.exists() else None,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (OUT / f"regression-{stamp}.json").write_text(json.dumps(result, indent=2, default=str))
    lines = [f"# Legal regression {a.name} (curated)", "",
             f"Claims extracted {extracted}, dropped for paraphrase {rejected}; feed {statuses}.", "",
             "| | D-line |", "| --- | --- |",
             f"| baseline | {legal_eval.summary_line(result['baseline'])} |"]
    if result["rerun"]:
        lines.append(f"| re-run (working) | {legal_eval.summary_line(result['rerun'])} |")
    if result["release"]:
        lines.append(f"| re-run (release) | {legal_eval.summary_line(result['release'])} |")
    (OUT / f"regression-{stamp}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
