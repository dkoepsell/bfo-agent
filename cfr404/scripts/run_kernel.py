#!/usr/bin/env python3
"""Phase 6: run every kernel detector over the merged artifact.

Writes ``kernel/flags.json`` (the ledger) and ``kernel/flags.owl`` (the same flags as
``ck:hasContradictionCandidate`` annotations). The flags live outside
``ontology/cfr404.owl`` on purpose: a candidate is the detector's suspicion about the
artifact, not something the regulation says, and it must not enter the artifact's
class census.

All twelve primitives are operationalized. Where a primitive cannot be computed the
runner records NOT COMPUTED with the reason, which is a different claim from zero.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, XSD

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "kernel" / "detectors"))

from cfrlib import CFR, INSTRUMENT, PRIMITIVES, STRATUM, load, local  # noqa: E402
from base import Context  # noqa: E402

CK = Namespace("http://davidkoepsell.com/cfr404/kernel#")
ART = ROOT / "ontology" / "cfr404.owl"
CHUNKS = ROOT / "corpus" / "chunks"
LEDGER = ROOT / "kernel" / "flags.json"
FLAGS_OWL = ROOT / "kernel" / "flags.owl"
REASONER = ROOT / "kernel" / "reasoner-result.json"

MODULES = {
    "K-A1": "k_a1", "K-A2": "k_a2", "K-A3": "k_a3",
    "K-B1": "k_b1", "K-B2": "k_b2", "K-B3": "k_b3",
    "K-C1": "k_c1", "K-C2": "k_c2", "K-C3": "k_c3",
    "K-D1": "k_d1", "K-D2": "k_d2", "K-D3": "k_d3",
}

# A detector that raises the same flag across an entire class family is applying a
# blanket flag. Those are reported separately and their removal shown, so a density
# is never inflated by one decision repeated N times.
BLANKET_THRESHOLD = 40


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", default=str(ART))
    args = ap.parse_args()

    g = load(args.artifact)
    chunks = {p.stem: json.loads(p.read_text()) for p in CHUNKS.glob("*.json")}
    reasoner = json.loads(REASONER.read_text()) if REASONER.exists() else None
    ctx = Context.build(g, chunks, reasoner)

    all_flags = []
    status: dict[str, dict] = {}

    for pid, modname in MODULES.items():
        mod = importlib.import_module(modname)
        flags = mod.detect(ctx)

        # Blanket detection: one primitive raising a near-identical finding across a
        # large family is a single decision, not N findings.
        by_shape = Counter(f.evidence.split(" ", 2)[-1][:60] for f in flags)
        blanket_shapes = {s for s, n in by_shape.items() if n >= BLANKET_THRESHOLD}
        for f in flags:
            f.primitive = pid
            if f.evidence.split(" ", 2)[-1][:60] in blanket_shapes:
                f.blanket = True

        computed = True
        reason = None
        if pid == "K-A2" and (not reasoner or reasoner.get("verdict") is None):
            computed = False
            reason = ((reasoner or {}).get("reason")
                      or "no reasoner verdict is available, so unsatisfiability is unknown")
        if pid == "K-D1" and not flags:
            import k_d1
            st = k_d1.status()
            if not st.get("acquired"):
                computed = False
                reason = (
                    "SSA published ALJ disposition statistics could not be acquired "
                    "from this host (" + "; ".join(
                        f"{a['url']} -> {a.get('status') or a.get('error')}"
                        for a in st.get("attempts", [])[:4]) + "). K-D1 is a claim about "
                    "the world, not about the text, and the spec is explicit that "
                    "asserting the gap without the measured variance would be an "
                    "assertion rather than a detection.")

        status[pid] = {
            "primitive": pid,
            "stratum": STRATUM[pid],
            "description": PRIMITIVES[pid],
            "instrument": INSTRUMENT[pid],
            "detector_module": f"kernel/detectors/{modname}.py",
            "implemented": True,
            "computed": computed,
            "not_computed_reason": reason,
            "flags": len(flags) if computed else None,
            "blanket_flags": sum(1 for f in flags if f.blanket) if computed else None,
        }
        if computed:
            all_flags.extend(flags)
        print(f"{pid}: " + (f"{len(flags)} flag(s)" if computed else "NOT COMPUTED"))

    # ---- ledger -------------------------------------------------------------
    by_primitive = Counter(f.primitive for f in all_flags)
    by_locus = Counter(f.locus for f in all_flags)
    by_stratum = Counter(STRATUM[f.primitive] for f in all_flags)
    non_blanket = [f for f in all_flags if not f.blanket]

    ledger = {
        "generated": str(date.today()),
        "artifact": Path(args.artifact).name,
        "named_classes": len(ctx.classes),
        "primitives_implemented": len(MODULES),
        "primitives_computed": sum(1 for s in status.values() if s["computed"]),
        "total_flags": len(all_flags),
        "total_flags_excluding_blanket": len(non_blanket),
        "by_primitive": dict(sorted(by_primitive.items())),
        "by_stratum": dict(sorted(by_stratum.items())),
        "by_locus": dict(sorted(by_locus.items())),
        "status": status,
        "flag_semantics": (
            "Every entry is a CANDIDATE: what a detector observed, not a "
            "reasoner-confirmed contradiction. Precision is established by hand audit "
            "and reported, never assumed."),
        "flags": [
            {"primitive": f.primitive, "stratum": STRATUM[f.primitive], "term": f.term,
             "term_label": ctx.label.get(URIRef(f.term), local(f.term)),
             "locus": f.locus, "edge": f.edge, "section": f.section,
             "instrument": INSTRUMENT[f.primitive], "detector": f.detector,
             "blanket": f.blanket, "evidence": f.evidence}
            for f in sorted(all_flags, key=lambda x: (x.primitive, x.term))
        ],
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    # ---- flags as OWL annotations ------------------------------------------
    fg = Graph()
    fg.bind("ck", CK)
    fg.bind("cfr", CFR)
    iri = URIRef("http://davidkoepsell.com/cfr404/kernel/flags")
    fg.add((iri, RDF.type, OWL.Ontology))
    fg.add((iri, OWL.imports, URIRef("http://davidkoepsell.com/cfr404/kernel")))
    fg.add((iri, RDFS.label, Literal("cfr404 contradiction-candidate ledger")))
    fg.add((iri, DCTERMS.license, URIRef("https://creativecommons.org/licenses/by/4.0/")))
    fg.add((iri, RDFS.comment, Literal(
        "Kept out of ontology/cfr404.owl deliberately. These are findings about the "
        "artifact, not content of it.")))
    for i, f in enumerate(sorted(all_flags, key=lambda x: (x.primitive, x.term))):
        cand = CK[f"candidate-{i:05d}"]
        fg.add((cand, RDF.type, CK["ContradictionCandidate"]))
        fg.add((cand, CK["primitive"], Literal(f.primitive)))
        fg.add((cand, CK["stratum"], Literal(STRATUM[f.primitive])))
        fg.add((cand, CK["locus"], Literal(f.locus)))
        fg.add((cand, CK["instrument"], Literal(INSTRUMENT[f.primitive])))
        fg.add((cand, CK["detector"], Literal(f.detector)))
        fg.add((cand, CK["evidence"], Literal(f.evidence)))
        fg.add((cand, CFR.sourceSection, Literal(f.section)))
        fg.add((cand, CK["blanketFlag"], Literal(f.blanket, datatype=XSD.boolean)))
        if f.edge:
            fg.add((cand, CK["edge"], Literal(f.edge)))
        try:
            fg.add((URIRef(f.term), CK["hasContradictionCandidate"], cand))
        except Exception:
            pass
    fg.serialize(destination=str(FLAGS_OWL), format="pretty-xml")

    print()
    print(f"total flags {len(all_flags)} ({len(non_blanket)} excluding blanket)")
    print("by stratum:", dict(sorted(by_stratum.items())))
    print("by locus:", dict(sorted(by_locus.items())))
    print(f"wrote {LEDGER.relative_to(ROOT)} and {FLAGS_OWL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
