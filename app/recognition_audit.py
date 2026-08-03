"""Recognition-layer audit: the kernel run over one ontology, typed by chain locus.

Implements SPEC-recognition-layer.md P5-P7 over "The Recognition Layer" §§6-7, §10:

* Strata A-C come from :mod:`app.kernel_audit` (structural) and the reasoner
  (unsatisfiable classes), which is where the existing detectors live.
* Stratum D is added here, and fires only where the ontology declares an
  institutional profile with acts (§6.4): institutional ontologies are exactly
  those capable of pragmatic contradiction.
* Every finding is a ``(kernel_code, locus, attribution)`` triple. Attribution
  defaults to ``"undetermined"`` -- §10 forbids defaulting to "source", because
  a defect we detect may belong to our translation of the source.
* Every result carries its stratum-completeness table. A zero for a primitive
  nothing instrumented is not a measurement (§7.5, §13).

Loci are derived from BFO anchors rather than read from the artifact: a
realizable is an effect-locus term, a process an act-locus term, a role an
assessor-locus term. This is coarser than a per-term declaration and is
reported as such -- we do not write chain annotations into extracted
ontologies (anchor, don't regenerate).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import recognition as rec

try:
    from .kernel import load_registry as _load_registry

    _REGISTRY = _load_registry()
except Exception:  # the audit must still run if the registry is unreadable
    _REGISTRY = {}

log = logging.getLogger(__name__)

# Which kernel primitives this module actually instruments. Anything outside
# this set is reported as silent-by-principle rather than as zero.
INSTRUMENTED_STRUCTURAL = ("K-A1", "K-A3", "K-B1", "K-B3", "K-C1", "K-C2", "K-C3")
INSTRUMENTED_REASONER = ("K-A1", "K-A2")
INSTRUMENTED_STRATUM_D = ("K-D2", "K-D3")

# BFO fragments that place a term at a chain locus.
_LOCUS_BY_ANCHOR = {
    "BFO_0000023": rec.Locus.ASSESSOR,     # role
    "BFO_0000015": rec.Locus.ACT,          # process
    "BFO_0000017": rec.Locus.EFFECT,       # realizable
    "BFO_0000016": rec.Locus.EFFECT,       # disposition
    "BFO_0000019": rec.Locus.FACTS,        # quality
    "BFO_0000031": rec.Locus.CRITERIA,     # GDC / information content
}

# Default locus per kernel primitive, from the product construction (§6.2,
# Table 6): where that primitive characteristically bites in a classification.
_DEFAULT_LOCUS = {
    "K-A1": rec.Locus.CRITERIA,
    "K-A2": rec.Locus.CRITERIA,
    "K-A3": rec.Locus.CRITERIA,
    "K-B1": rec.Locus.CRITERIA,
    "K-B2": rec.Locus.CRITERIA,
    "K-B3": rec.Locus.CRITERIA,
    "K-C1": rec.Locus.EFFECT,
    "K-C2": rec.Locus.CRITERIA,
    "K-C3": rec.Locus.EFFECT,
    "K-D1": rec.Locus.FACTS,
    "K-D2": rec.Locus.ACT,
    "K-D3": rec.Locus.EFFECT,
}


def locus_for(kernel_code: str, bfo_anchor: str | None = None) -> str:
    """Place a finding on the chain: by the term's BFO anchor when we have one,
    otherwise by the primitive's characteristic locus."""
    if bfo_anchor:
        frag = str(bfo_anchor).rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        hit = _LOCUS_BY_ANCHOR.get(frag)
        if hit is not None:
            return hit.value
    return _DEFAULT_LOCUS.get(kernel_code, rec.Locus.CRITERIA).value


def _base_code(code: str) -> str:
    """kernel_audit reports subtypes such as K-A3b (indeterminacy by missing
    definition); the kernel primitive it instantiates is K-A3."""
    code = (code or "").strip()
    if code in rec.KERNEL:
        return code
    trimmed = code.rstrip("abc")
    return trimmed if trimmed in rec.KERNEL else code


def _normalise(code: str, entry: dict) -> dict:
    """One kernel_audit sample entry -> a typed finding triple.

    ``kernel_code`` keeps the subtype the detector actually reported. Collapsing
    it to the parent was how a coverage predicate reached the debt calculation
    dressed as a defect: K-A3b, which fires once per undefined class, arrived as
    K-A3 and the registry never got the chance to classify it. The registry
    entry for the reported code is what decides whether a finding counts.
    """
    base = _base_code(code)
    return {
        "kernel_code": code if code else base,
        "base_code": base,
        "subtype": code if code != base else None,
        "locus": locus_for(base, entry.get("bfo_anchor")),
        "subjects": [entry.get("label") or entry.get("iri") or ""],
        "iri": entry.get("iri"),
        "detail": entry.get("detail") or "",
        # §10: our detectors cannot tell the source's defect from our
        # translation's. The analyst adjudicates.
        "attribution": entry.get("attribution") or "undetermined",
        "instrument": "structural",
    }


# --------------------------------------------------------------------------
# Stratum D (§6.4) -- fires only where the profile says there are acts
# --------------------------------------------------------------------------

def _stratum_d(working_path: str, profile: rec.DomainProfile,
               unsat_names: Optional[list[str]] = None) -> list[dict]:
    """Structurally detectable Stratum D defects.

    K-D2 (performative self-defeat): an *act*-locus class that is
    unsatisfiable. The act is defined so that performing it is impossible --
    satisfaction of its content entails failure of its success conditions. This
    is the product construction doing its work: the same empty class is K-A2 at
    the criteria locus and K-D2 at the act locus.

    K-D3 (modal clash): a capacity present in structure but blocked in
    practice. In artifact terms, a conferred status (realizable) with no act in
    the artifact that could realize it, in an institution the profile declares
    act-thick. Possible according to the artifact, unrealizable in the
    institution implementing it.

    K-D1 is never returned: falsification needs world-facing data the artifact
    does not contain, and is reported as silent-by-principle instead.
    """
    if not profile.has_chain:
        return []

    findings: list[dict] = []
    try:
        from owlready2 import World

        world = World()
        world.get_ontology(Path(working_path).resolve().as_uri()).load()

        acts, realizables, realized = [], [], set()
        for cls in world.classes():
            anchors = {getattr(p, "name", "") for p in cls.ancestors()}
            name = getattr(cls, "name", "")
            if not name:
                continue
            if "BFO_0000015" in anchors:
                acts.append(name)
            if "BFO_0000017" in anchors or "BFO_0000016" in anchors \
                    or "BFO_0000023" in anchors:
                realizables.append(name)
            for restriction in getattr(cls, "is_a", []):
                prop = getattr(restriction, "property", None)
                pname = getattr(prop, "name", "") if prop is not None else ""
                if pname in ("BFO_0000054", "realized_in", "BFO_0000055",
                             "realizes"):
                    realized.add(name)

        # K-D2: unsatisfiable act-locus classes.
        for name in (unsat_names or []):
            if name in acts:
                findings.append({
                    "kernel_code": "K-D2",
                    "subtype": None,
                    "locus": rec.Locus.ACT.value,
                    "subjects": [name],
                    "detail": (
                        "the recognition act is defined so that it cannot be "
                        "performed: its success conditions are undermined by "
                        "its own content"
                    ),
                    "attribution": "undetermined",
                    "instrument": "process_model",
                })

        # K-D3: conferred capacities with no realizing act anywhere in the
        # artifact. Only meaningful where the institution is act-thick; a
        # recognition-only institution keeps its acts outside the artifact, so
        # their absence is expected rather than a defect.
        if profile.act_thickness == "thick":
            if not acts and realizables:
                findings.append({
                    "kernel_code": "K-D3",
                    "subtype": None,
                    "locus": rec.Locus.ACT.value,
                    "subjects": sorted(realizables)[:20],
                    "detail": (
                        f"{len(realizables)} capacities are conferred but the "
                        f"artifact contains no act at all, in an institution "
                        f"declared act-thick: possible in structure, "
                        f"unrealizable in practice"
                    ),
                    "attribution": "undetermined",
                    "instrument": "process_model",
                })
            else:
                blocked = sorted(set(realizables) - realized)
                if blocked and acts:
                    findings.append({
                        "kernel_code": "K-D3",
                        "subtype": None,
                        "locus": rec.Locus.EFFECT.value,
                        "subjects": blocked[:20],
                        "detail": (
                            f"{len(blocked)} conferred capacities have no "
                            f"realization link to any of the {len(acts)} acts "
                            f"in the artifact"
                        ),
                        "attribution": "undetermined",
                        "instrument": "process_model",
                    })
    except Exception as e:  # a D probe must never fail an audit
        log.warning("stratum D probe skipped: %s", e)
    return findings


# --------------------------------------------------------------------------
# The audit
# --------------------------------------------------------------------------

def audit(working_path: str, profile: rec.DomainProfile,
          bfo_path: Optional[str] = None,
          run_reasoner: bool = True,
          sample: int = 25) -> dict:
    """Run the kernel over one ontology and type every finding by chain locus.

    Returns a report carrying the typed findings, the contradiction debt, and
    -- always -- the stratum-completeness table that says what this run could
    see at all.
    """
    findings: list[dict] = []
    instrumented: set[str] = set()
    errors: list[str] = []

    # Strata A-C: structural detectors.
    truncated: dict[str, int] = {}
    try:
        from . import kernel_audit

        ledger = kernel_audit.audit(str(working_path), bfo_path, sample=sample)
        samples = ledger.get("findings_sample") or {}
        counts = ledger.get("finding_counts") or {}
        for code, entries in samples.items():
            findings.extend(_normalise(code, e) for e in entries)
        for code, total in counts.items():
            # Full population per type; the findings list may hold only a
            # sample of it, and a sampled count must never read as the whole.
            #
            # Keyed by the reported code rather than its parent, for the same
            # reason as _normalise: a coverage subtype folded into its defect
            # parent enters the debt figure as though it were a defect.
            if code in _REGISTRY or _base_code(code) in rec.KERNEL:
                truncated[code] = truncated.get(code, 0) + int(total)
        instrumented.update(INSTRUMENTED_STRUCTURAL)
    except Exception as e:
        errors.append(f"structural audit failed: {e}")
        log.warning("kernel_audit failed: %s", e)

    # Strata A: the reasoner's view (unsatisfiable classes and inconsistency).
    unsat_names: list[str] = []
    if run_reasoner:
        try:
            from . import coherence_reason

            res = coherence_reason.coherence_check(str(working_path))
            if res.get("error"):
                errors.append(str(res["error"]))
            else:
                instrumented.update(INSTRUMENTED_REASONER)
                if res.get("inconsistent_ontology"):
                    findings.append({
                        "kernel_code": "K-A1",
                        "subtype": None,
                        "locus": _DEFAULT_LOCUS["K-A1"].value,
                        "subjects": [],
                        "detail": res.get("detail") or "the ontology has no model",
                        "attribution": "undetermined",
                        "instrument": "reasoner",
                    })
                for u in res.get("unsatisfiable_classes") or []:
                    name = u.get("class") or u.get("name") or ""
                    if name:
                        unsat_names.append(name)
                    findings.append({
                        "kernel_code": "K-A2",
                        "subtype": None,
                        "locus": _DEFAULT_LOCUS["K-A2"].value,
                        "subjects": [name] if name else [],
                        "detail": u.get("why") or u.get("detail") or
                                  "class equivalent to the empty class",
                        "attribution": "undetermined",
                        "instrument": "reasoner",
                    })
        except Exception as e:
            errors.append(f"reasoner audit failed: {e}")
            log.warning("coherence_check failed: %s", e)

    # Stratum D: only where the institution has acts.
    if profile.has_chain:
        d_findings = _stratum_d(str(working_path), profile, unsat_names)
        # A defect is one defect. An unsatisfiable act class is the SAME empty
        # class the reasoner reports as K-A2; the product construction gives it
        # a more specific reading at the act locus (K-D2), and the specific
        # typing replaces the generic one rather than adding to the debt.
        retyped = {
            s for f in d_findings if f["kernel_code"] == "K-D2"
            for s in f["subjects"]
        }
        if retyped:
            findings = [
                f for f in findings
                if not (f["kernel_code"] == "K-A2"
                        and set(f["subjects"]) & retyped)
            ]
        findings.extend(d_findings)
        instrumented.update(INSTRUMENTED_STRATUM_D)

    # CD counts the full population, not the displayed sample.
    debt = rec.contradiction_debt(findings, profile, instrumented,
                                  totals=truncated)

    # by_locus is over the displayed sample; by_stratum is over the full counts.
    by_locus: dict[str, int] = {}
    for f in findings:
        by_locus[f["locus"]] = by_locus.get(f["locus"], 0) + 1
    by_stratum: dict[str, int] = {}
    for code, n in debt["per_type"].items():
        s = rec.KERNEL[code].stratum
        by_stratum[s] = by_stratum.get(s, 0) + n

    return {
        "domain": profile.key,
        "system_class": profile.system_class,
        "has_chain": profile.has_chain,
        "active_strata": list(profile.active_strata),
        "findings": findings,
        "count": sum(debt["per_type"].values()),
        "sampled_count": len(findings),
        "by_locus": by_locus,
        "by_stratum": by_stratum,
        "instrumented": sorted(instrumented),
        "contradiction_debt": debt,
        "errors": errors,
        # The reporting invariant of SPEC P7: a CD figure without this table is
        # a subset score presented as a measurement.
        "note": (
            "CD is a lower bound over the instrumented primitives only, with "
            "uncalibrated (uniform) weights. Read it beside the completeness "
            "table. Attribution defaults to 'undetermined': whether a defect "
            "belongs to the source or to our translation of it is an analyst's "
            "call, not the tool's."
        ),
    }


def render_text(report: dict) -> str:
    """Human-readable report. The completeness table is not optional: a debt
    figure printed without what the run could not see is a subset score
    presented as a measurement (§7.5, §13)."""
    out: list[str] = []
    out.append(f"Recognition-layer audit -- {report['domain']} "
               f"({report['system_class']})")
    out.append(f"active strata: {', '.join(report['active_strata'])}")
    debt = report["contradiction_debt"]
    out.append("")
    out.append(f"Contradiction debt (lower bound, uncalibrated): {debt['cd']:g}")
    for code, n in sorted(debt["per_type"].items()):
        sampled = debt["sampled_types"].get(code)
        note = f"  [{sampled} shown]" if sampled is not None else ""
        out.append(f"  {code} {rec.KERNEL[code].name:<28} {n}{note}")
    if debt["attributed_to_translation"]:
        out.append(f"  (excluded: {debt['attributed_to_translation']} attributed "
                   f"to our translation)")
    out.append(f"  attribution undetermined: {debt['attribution_undetermined']}")
    out.append("")
    out.append("By chain locus (over the sampled findings):")
    for locus in rec.LOCUS_ORDER:
        out.append(f"  {locus.value:<10} {report['by_locus'].get(locus.value, 0)}")
    out.append("")
    out.append("Stratum completeness -- what this run could see at all:")
    for row in debt["completeness"]:
        out.append(f"  {row['code']:<6} {row['stratum']}  {row['status']:<20} "
                   f"{row['note']}")
    if report["errors"]:
        out.append("")
        out.append("Errors:")
        out.extend(f"  {e}" for e in report["errors"])
    out.append("")
    out.append(report["note"])
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        description="Recognition-layer audit: the contradiction kernel over one "
                    "ontology, typed by recognition-chain locus.")
    ap.add_argument("target", help="path to the ontology (working.owl)")
    ap.add_argument("--domain", default=rec.DEFAULT_DOMAIN,
                    help="declared institution (default: scientific_reference)")
    ap.add_argument("--bfo", default=None, help="path to bfo.owl")
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--no-reasoner", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.domain not in rec.DOMAIN_PROFILES:
        raise SystemExit(
            f"unknown domain {args.domain!r}; choose one of: "
            + ", ".join(sorted(rec.DOMAIN_PROFILES)))
    report = audit(args.target, rec.profile_for(args.domain), args.bfo,
                   run_reasoner=not args.no_reasoner, sample=args.sample)
    print(_json.dumps(report, indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
