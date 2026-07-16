"""Phase E orchestrator: sanity gate -> regression -> build+save ontologies ->
run proof shapes -> emit findings.json.

Findings are the product surface. They carry verdict + verbatim clause text +
plain-language consequence. They never carry the kernel axioms or the typology
(those stay server-side). Run:  python -m nfip.run_analysis
"""
from __future__ import annotations

import datetime
import json
import os

import owlready2 as o2

from . import fixtures, kernel, nfip_model, proofs, segment_sfip

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
FINDINGS = os.path.join(HERE, "findings.json")
KERNEL_OWL = os.path.join(ROOT, "ontology", "kernels", "coverage-kernel.owl")
NFIP_OWL = os.path.join(ROOT, "ontology", "library", "NFIP_SFIP_v1", "nfip_sfip.owl")


def _clauses() -> dict:
    path = os.path.join(HERE, "sfip_clauses.json")
    if not os.path.exists(path):
        segment_sfip.main()
    data = json.load(open(path, encoding="utf-8"))
    return {c["id"]: c for c in data["clauses"]}


def _cite(clauses: dict, cid: str) -> dict:
    c = clauses.get(cid, {})
    return {"id": cid, "cfr_cite": c.get("cfr_cite", ""),
            "sfip_section": c.get("sfip_section", ""),
            "verbatim_text": c.get("verbatim_text", "")}


def _save_ontologies() -> None:
    """Persist the kernel and the NFIP base for independent verification."""
    os.makedirs(os.path.dirname(KERNEL_OWL), exist_ok=True)
    os.makedirs(os.path.dirname(NFIP_OWL), exist_ok=True)
    wk = o2.World()
    kernel.build_coverage_kernel(wk)
    wk.get_ontology(kernel.KERNEL_IRI).save(file=KERNEL_OWL, format="rdfxml")
    wn = o2.World()
    onto, _ = nfip_model.build_nfip_base(wn)
    onto.save(file=NFIP_OWL, format="rdfxml")


def run() -> dict:
    clauses = _clauses()

    if not kernel.sanity_gate():
        raise RuntimeError("reasoner sanity gate FAILED - verdicts not trustworthy")
    regression = fixtures.run_fixtures()
    if not regression["all_pass"]:
        raise RuntimeError("regression fixtures FAILED - instrument not validated")

    _save_ontologies()

    s1 = proofs.run_shape1()
    s2 = proofs.run_shape2()

    findings = []

    # Finding 1 (Shape 2): mudflow vs earth movement -- covered and excluded.
    if s2["verdict"] == "inconsistent":
        findings.append({
            "id": "NFIP-F1",
            "shape": 2,
            "shape_name": "Inconsistency (a fact pattern that is both)",
            "title": "A saturated debris flow is both covered (mudflow) and excluded "
                     "(earth movement)",
            "verdict": "inconsistent ontology",
            "reasoner_evidence": "HermiT: asserting a single loss that exhibits both the "
                                 "mudflow feature and the earth-movement feature makes the "
                                 "ontology inconsistent (the two are disjoint).",
            "loss": s2["loss"],
            "responsible_clauses": [
                _cite(clauses, "SFIP-DW-II-B"),
                _cite(clauses, "SFIP-DW-II-C-20"),
                _cite(clauses, "SFIP-DW-V-C"),
            ],
            "plain_english":
                "Flood is defined to include mudflow (earth carried by a current of water), "
                "so a mudflow loss is covered. The earth-movement exclusion removes loss "
                "caused by earth movement even if caused by flood, listing landslide and a "
                "saturated soil mass moving down a slope. A real saturated debris flow "
                "answers to both descriptions at once, so the policy both covers and "
                "excludes the same loss. The wording does not determine the outcome of the "
                "claim -- exactly the ambiguity NFIP mudflow-versus-landslide disputes "
                "have turned on.",
        })

    # Finding 2 (Shape 1): illusory mudflow carveback to the earth-movement exclusion.
    if s1["verdict"] == "consistent" and s1["target_unsat"] and s1["perils_satisfiable"]:
        findings.append({
            "id": "NFIP-F2",
            "shape": 1,
            "shape_name": "Coherence failure (an empty coverage class)",
            "title": "The mudflow limb of the earth-movement carveback restores nothing "
                     "from the exclusion it modifies",
            "verdict": "unsatisfiable coverage class (RestoredFromEarthMovement)",
            "reasoner_evidence": "HermiT: the class of losses the mudflow carveback restores "
                                 "FROM the earth-movement exclusion (mudflow AND earth "
                                 "movement) is unsatisfiable, while mudflow and earth "
                                 "movement are each individually satisfiable.",
            "responsible_clauses": [
                _cite(clauses, "SFIP-DW-V-C-CB"),
                _cite(clauses, "SFIP-DW-V-C"),
                _cite(clauses, "SFIP-DW-II-C-20"),
            ],
            "plain_english":
                "The earth-movement exclusion is followed by a carveback: 'We do, however, "
                "pay for losses from mudflow ... specifically insured under our definition "
                "of flood.' But the policy's own Mudflow definition says landslide, slope "
                "failure, and a saturated soil mass moving down a slope are NOT mudflows -- "
                "i.e. mudflow is disjoint from the forms of earth movement the exclusion "
                "removes. So the mudflow limb of the carveback restores nothing from that "
                "exclusion: it reads as a concession but, against the earth-movement "
                "exclusion, grants nothing. (The carveback's separate erosion-driven land "
                "subsidence limb is not implicated.)",
        })

    report = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "form": "NFIP Standard Flood Insurance Policy - Dwelling Form "
                "(44 CFR Part 61, Appendix A(1))",
        "source": "44CFR61NFIP.txt (eCFR, retrieved per file; public domain)",
        "domain_source": "NFIP extraction corpus (working.owl), produced by the feed "
                         "pipeline over 44CFR61NFIP.txt. The mudflow / earth-movement "
                         "disjointness both findings turn on is the extraction's own "
                         "rendering of II.C.20 -- not authored for this demo -- so every "
                         "domain axiom is checkable in the fed corpus.",
        "reasoner": "HermiT via owlready2",
        "fidelity": "faithful (the text's structure is encoded as written; findings are "
                    "evidence, never corrections)",
        "sanity_gate_passed": True,
        "regression": regression,
        "raw_reasoner": {"shape1": s1, "shape2": s2},
        "finding_count": len(findings),
        "findings": findings,
        "artifacts": {
            "coverage_kernel_owl": os.path.relpath(KERNEL_OWL, ROOT),
            "nfip_sfip_owl": os.path.relpath(NFIP_OWL, ROOT),
        },
    }
    with open(FINDINGS, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report


def main() -> None:
    r = run()
    print(f"findings: {r['finding_count']}  ->  {FINDINGS}")
    for f in r["findings"]:
        print(f"  [{f['id']}] Shape {f['shape']}: {f['verdict']}")
        print(f"           {f['title']}")


if __name__ == "__main__":
    main()
