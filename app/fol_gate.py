"""Out-of-loop FOL audit: Prover9/Mace4 against the BFO 2020 FOL axioms.

Implements fol-gate-spec.md. Evidence-only (FG-0): this module has no write
path to any ontology -- it opens the OWL file read-only, translates it to
LADR clauses (app/fol_translate.py), and runs:

  * Mace4  -- finite model search. A found model is POSITIVE evidence of
    consistency (stronger than HermiT's "no clash found"), independent of
    the owlready2/HermiT stack.
  * Prover9 -- refutation (goal ``$F``). A proof is a DEFINITE inconsistency
    with an auditable proof object; probes (P-2) additionally test for
    entailed category straddles and uninstantiable classes.

Its sole outputs are sidecar records under the ontology's sessions/ directory
and the returned dict. Verdicts are findings about the SOURCE TEXT the
ontology faithfully represents, never defects to fix in the ontology.

Runs are serialized behind a module lock AND the HermiT lock so no two
memory-hungry reasoning processes ever stack on the 4GB host (O-2; incident
2026-07-02), with CPU/address-space rlimits on every subprocess.

CLI (C-1):  python -m app.fol_gate file.owl [--mode A|B] [--profile ...]
            [--probes] [--timeout N] [--out record.json]
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from . import fol_translate
from .ontology_manager import _REASONER_LOCK

log = logging.getLogger(__name__)

_FOL_LOCK = threading.Lock()
_availability_logged = False

# Sub-theory profiles (fol-gate-spec.md B-2).
PROFILES = {
    "default": [
        "universal-declaration.prover9",
        "existence-instantiation.prover9",
        "temporalized-relations.p9",
        "continuant-mereology.prover9",
        "specific-dependency.prover9",
        "generic-dependence.prover9",
        "participation.prover9",
    ],
    "full": [
        "universal-declaration.prover9",
        "existence-instantiation.prover9",
        "temporalized-relations.p9",
        "continuant-mereology.prover9",
        "occurrent-mereology.prover9",
        "specific-dependency.prover9",
        "generic-dependence.prover9",
        "participation.prover9",
        "material-entity.prover9",
        "history.prover9",
        "spatial.prover9",
        "spatiotemporal.prover9",
        "temporal-region.prover9",
        "order.prover9",
    ],
}


def binaries_available() -> bool:
    """Soft dependency (O-1): absent binaries disable the gate, log once."""
    global _availability_logged
    ok = bool(shutil.which(config.FOL_PROVER9_BIN)) and bool(
        shutil.which(config.FOL_MACE4_BIN)
    )
    if not ok and not _availability_logged:
        log.info(
            "FOL gate disabled: prover9/mace4 not found "
            "(FOL_PROVER9_BIN=%s, FOL_MACE4_BIN=%s)",
            config.FOL_PROVER9_BIN, config.FOL_MACE4_BIN,
        )
        _availability_logged = True
    return ok


def _limits(cpu_seconds: int):
    """rlimit setter for LADR subprocesses (O-2): CPU + 1GB address space."""
    import resource

    def set_limits():
        resource.setrlimit(resource.RLIMIT_CPU,
                           (cpu_seconds + 5, cpu_seconds + 5))
        gb = 1 << 30
        resource.setrlimit(resource.RLIMIT_AS, (gb, gb))

    return set_limits


def _run_ladr(binary: str, args: list[str], input_text: str,
              cpu_seconds: int) -> dict:
    """Run one LADR binary serialized against every other reasoner (O-2)."""
    t0 = time.monotonic()
    try:
        with _FOL_LOCK, _REASONER_LOCK:
            proc = subprocess.run(
                [binary, *args],
                input=input_text,
                capture_output=True,
                text=True,
                timeout=cpu_seconds * 2 + 10,  # wall-clock kill at ~2x CPU
                preexec_fn=_limits(cpu_seconds),
            )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr[-2000:],
            "elapsed": round(time.monotonic() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": "wall-clock kill",
                "elapsed": round(time.monotonic() - t0, 2)}
    except OSError as e:
        return {"returncode": None, "stdout": "", "stderr": str(e),
                "elapsed": round(time.monotonic() - t0, 2)}


def load_axioms(profile: str) -> str:
    """Wrap the vendored sub-theory files for a profile into one sos block.

    The upstream files (B-1) are bare formula lists headed by a
    ``set(prolog_style_variables)`` line -- they expect the harness to supply
    the ``formulas(sos)...end_of_list`` wrapper, which we do here.
    """
    names = PROFILES.get(profile)
    if names is None:
        raise ValueError(f"unknown FOL axiom profile {profile!r}")
    lines = []
    for name in names:
        path = Path(config.FOL_AXIOMS_DIR) / name
        if not path.exists():
            raise FileNotFoundError(f"vendored axiom file missing: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("%"):
                continue
            if stripped.startswith("set("):
                continue
            lines.append("  " + stripped)
    return (
        "set(prolog_style_variables).\n\n"
        "formulas(sos).\n" + "\n".join(lines) + "\nend_of_list.\n"
    )


# ------------------------------------------------------------------ mace4
def mace4_consistency(sos_block: str, axioms: str = "",
                      max_domain: Optional[int] = None,
                      timeout: Optional[int] = None) -> dict:
    """Sweep domain sizes for a finite model (M-1). A model is positive
    consistency evidence; exhaustion/timeout is INCONCLUSIVE, never
    'consistent'."""
    max_domain = max_domain or config.FOL_MACE4_MAX_DOMAIN
    timeout = timeout or config.FOL_TIMEOUT_SECS
    input_text = axioms + "\n" + sos_block
    res = _run_ladr(
        config.FOL_MACE4_BIN,
        ["-n", "2", "-N", str(max_domain), "-t", str(timeout), "-m", "1"],
        input_text, timeout,
    )
    out = res["stdout"]
    if "exiting with 1 model" in out or "interpretation(" in out:
        m = None
        start = out.find("interpretation(")
        if start != -1:
            m = out[start:out.find("\n\n", start)]
        domain = None
        dm = out.rfind("DOMAIN SIZE")
        if dm != -1:
            try:
                domain = int(out[dm:].split()[2])
            except (IndexError, ValueError):
                pass
        return {"outcome": "model_found", "domain_size": domain,
                "model": m, "elapsed": res["elapsed"]}
    if "all_domains_exhausted" in out or "exhausted" in out:
        return {"outcome": "exhausted", "elapsed": res["elapsed"]}
    if "max_sec" in out or res["returncode"] is None:
        return {"outcome": "timeout", "elapsed": res["elapsed"]}
    return {"outcome": "error", "detail": (res["stderr"] or out)[-500:],
            "elapsed": res["elapsed"]}


# ---------------------------------------------------------------- prover9
def _prover9_input(sos_block: str, axioms: str, goal: Optional[str],
                   timeout: int) -> str:
    goal_block = ""
    if goal:
        goal_block = f"\nformulas(goals).\n  {goal}\nend_of_list.\n"
    return (
        f"assign(max_seconds, {timeout}).\n"
        f"set(prolog_style_variables).\n\n"
        + axioms + "\n" + sos_block + goal_block
    )


def _extract_proof(out: str) -> Optional[str]:
    start = out.find("= PROOF =")
    end = out.find("= end of proof =")
    if start == -1 or end == -1:
        return None
    return out[out.rfind("=", 0, start + 1):end + len("= end of proof =")]


def prover9_prove(sos_block: str, axioms: str = "", goal: Optional[str] = None,
                  timeout: Optional[int] = None) -> dict:
    """Prove ``goal`` from axioms+module; goal=None is the refutation check
    (P-1: any proof at all means the clause set is inconsistent)."""
    timeout = timeout or config.FOL_TIMEOUT_SECS
    res = _run_ladr(config.FOL_PROVER9_BIN, [],
                    _prover9_input(sos_block, axioms, goal, timeout), timeout)
    out = res["stdout"]
    if "THEOREM PROVED" in out:
        return {"outcome": "proved", "proof": _extract_proof(out),
                "elapsed": res["elapsed"]}
    if "SEARCH FAILED" in out and "sos_empty" in out:
        # Saturation without a proof: the clause set is genuinely consistent
        # at the first-order level (complete search space exhausted).
        return {"outcome": "saturated_no_proof", "elapsed": res["elapsed"]}
    if "SEARCH FAILED" in out or "max_seconds" in out or \
            res["returncode"] is None:
        return {"outcome": "no_proof_within_budget", "elapsed": res["elapsed"]}
    return {"outcome": "error", "detail": (res["stderr"] or out)[-500:],
            "elapsed": res["elapsed"]}


def proof_input_axioms(proof: Optional[str]) -> list[str]:
    """The input clauses a proof actually used -- the diagnosis (P-1)."""
    if not proof:
        return []
    lines = []
    for line in proof.splitlines():
        line = line.strip()
        # Input lines look like: "3 formula.  [assumption]."
        if line.endswith("[assumption].") and " " in line:
            lines.append(line.split(" ", 1)[1].rsplit("[", 1)[0].strip())
    return lines


# ------------------------------------------------------------------ probes
def straddle_probes(translation) -> list[dict]:
    """P-2: for each disjoint top-category pair, is a straddling instance
    ENTAILED? (Only meaningful in Mode B where membership is instanceOf.)"""
    from . import bfo_catalog

    probes = []
    declared = fol_translate._declared_bfo_constants()
    for group in bfo_catalog.DISJOINT_GROUPS:
        members = sorted(group)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                try:
                    ca = fol_translate.bfo_class_constant(a, declared)
                    cb = fol_translate.bfo_class_constant(b, declared)
                except ValueError:
                    continue
                if ca is None or cb is None:
                    continue
                probes.append({
                    "kind": "straddle",
                    "pair": [a, b],
                    "goal": (f"exists X exists T (instanceOf(X,{ca},T) & "
                             f"instanceOf(X,{cb},T))."),
                })
    return probes


def unsat_class_probes(translation, cap: Optional[int] = None) -> list[dict]:
    """P-2: per working class, is 'no instance is possible' provable?"""
    cap = cap if cap is not None else config.FOL_PROBES_MAX_CLASSES
    probes = []
    working = sorted(
        sym for iri, sym in translation.symbols.items()
        if sym.startswith(("w_", "c_")) and not _is_bfo_sym(iri)
    )
    dropped = max(0, len(working) - cap)
    for sym in working[:cap]:
        if translation.mode == "B":
            goal = f"all X all T -(instanceOf(X,{sym},T))."
        else:
            goal = f"all X -({sym}(X))."
        probes.append({"kind": "unsat_class", "class": sym, "goal": goal})
    if dropped:
        # No silent caps: surface what was not probed.
        probes.append({"kind": "cap_notice",
                       "detail": f"{dropped} classes beyond "
                                 f"FOL_PROBES_MAX_CLASSES={cap} not probed"})
    return probes


def _is_bfo_sym(iri: str) -> bool:
    frag = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    return frag.startswith(("BFO_", "RO_", "IAO_"))


# ------------------------------------------------------------------- audit
def audit(owl_path: Path, mode: str = "A", profile: Optional[str] = None,
          probes: bool = False, timeout: Optional[int] = None,
          write_record: bool = True) -> dict:
    """Run the full audit on one OWL file and return the R-1 record.

    FG-0 / A-7: the file is read exactly once for translation plus once for
    hashing; the content hash is re-checked at the end so the record itself
    certifies the audit changed nothing.
    """
    owl_path = Path(owl_path)
    profile = profile or config.FOL_AXIOM_PROFILE
    started = datetime.now(timezone.utc).isoformat()
    content = owl_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()

    if not binaries_available():
        return {"status": "disabled",
                "detail": "prover9/mace4 binaries not available",
                "ontology": str(owl_path), "mode": mode}

    translation = fol_translate.translate_file(owl_path, mode=mode)
    sos = translation.sos_block()
    axioms = load_axioms(profile) if mode == "B" else ""

    record = {
        "ontology": str(owl_path),
        "sha256": digest,
        "mode": mode,
        "axiom_profile": profile if mode == "B" else None,
        "started": started,
        "formula_count": len(translation.formulas),
        "skipped_axioms": translation.skipped,
        "unbridged_relations": translation.unbridged,
        "framing": (
            "Evidence about the source text this ontology faithfully "
            "represents; the ontology itself was not modified (FG-0)."
        ),
    }

    record["mace4"] = mace4_consistency(sos, axioms, timeout=timeout)
    refute = prover9_prove(sos, axioms, goal=None, timeout=timeout)
    if refute["outcome"] == "proved":
        refute["culprit_axioms"] = proof_input_axioms(refute.get("proof"))
    record["prover9_refutation"] = refute

    if probes:
        probe_results = []
        budget = config.FOL_PROBE_TIMEOUT_SECS
        candidates = (straddle_probes(translation) if mode == "B" else []) \
            + unsat_class_probes(translation)
        for p in candidates:
            if p["kind"] == "cap_notice":
                probe_results.append(p)
                continue
            r = prover9_prove(sos, axioms, goal=p["goal"], timeout=budget)
            entry = {**p, "outcome": r["outcome"], "elapsed": r["elapsed"]}
            if r["outcome"] == "proved":
                entry["proof"] = r.get("proof")
            probe_results.append(entry)
        record["probes"] = probe_results

    # Consistency verdict, spelled conservatively (M-1): only a model is
    # positive evidence; a translation with skips is only partial.
    if refute["outcome"] == "proved":
        verdict = "inconsistent"
    elif record["mace4"]["outcome"] == "model_found":
        verdict = "consistent"
        if translation.skipped:
            verdict = "consistent_partial_translation"
    else:
        verdict = "inconclusive"
    record["verdict"] = verdict
    record["finished"] = datetime.now(timezone.utc).isoformat()

    # A-7: byte-identical before and after, certified in the record.
    record["file_unchanged"] = (
        hashlib.sha256(owl_path.read_bytes()).hexdigest() == digest
    )

    if write_record:
        record["record_path"] = str(_write_record(owl_path, record))
    return record


def _write_record(owl_path: Path, record: dict) -> Path:
    sessions = owl_path.parent / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    stamp = record["finished"].replace(":", "").replace("-", "")[:15]
    path = sessions / f"fol_audit_{stamp}_{record['mode']}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    (sessions / "fol_audit_latest.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    return path


def latest_record(working_path: Path) -> Optional[dict]:
    path = Path(working_path).parent / "sessions" / "fol_audit_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def summarize(record: dict) -> str:
    """One-line human summary for notifications and reports (R-2)."""
    if record.get("status") == "disabled":
        return "FOL audit skipped: prover9/mace4 not installed."
    parts = [f"FOL audit ({record['mode']}): {record['verdict']}"]
    m = record.get("mace4", {})
    if m.get("outcome") == "model_found":
        parts.append(f"mace4 model (domain {m.get('domain_size')})")
    r = record.get("prover9_refutation", {})
    if r.get("outcome") == "proved":
        parts.append(f"prover9 PROOF of inconsistency "
                     f"({len(r.get('culprit_axioms', []))} culprit axioms)")
    if record.get("probes"):
        hits = [p for p in record["probes"] if p.get("outcome") == "proved"]
        if hits:
            parts.append(f"{len(hits)} probe(s) proved")
    if record.get("skipped_axioms"):
        parts.append(f"{len(record['skipped_axioms'])} axiom(s) skipped "
                     f"in translation")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------- CLI
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m app.fol_gate",
        description="Prover9/Mace4 FOL audit of an OWL file (evidence-only; "
                    "the file is never modified).",
    )
    ap.add_argument("owl_file", type=Path)
    ap.add_argument("--mode", choices=("A", "B"), default="A")
    ap.add_argument("--profile", choices=tuple(PROFILES), default=None)
    ap.add_argument("--probes", action="store_true")
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    if not args.owl_file.exists():
        print(f"error: {args.owl_file} does not exist")
        return 2
    if not binaries_available():
        print("FOL gate disabled: prover9 and/or mace4 not found on PATH "
              "(set FOL_PROVER9_BIN / FOL_MACE4_BIN). Nothing was run.")
        return 3

    record = audit(args.owl_file, mode=args.mode, profile=args.profile,
                   probes=args.probes, timeout=args.timeout,
                   write_record=args.out is None)
    if args.out:
        args.out.write_text(json.dumps(record, indent=2), encoding="utf-8")
        record["record_path"] = str(args.out)

    print(summarize(record))
    if record.get("skipped_axioms"):
        print(f"skipped ({len(record['skipped_axioms'])}):")
        for s in record["skipped_axioms"][:10]:
            print(f"  - {s['axiom']}: {s['reason']}")
    r = record.get("prover9_refutation", {})
    if r.get("outcome") == "proved":
        print("culprit axioms (from the proof object):")
        for c in r.get("culprit_axioms", [])[:15]:
            print(f"  - {c}")
    print(f"record: {record.get('record_path', '(not written)')}")
    return 0 if record.get("verdict") != "inconsistent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
