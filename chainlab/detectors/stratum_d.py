"""Stratum D detectors over norm tuples.

K-D3: a capacity is conferred but the corpus specifies no way to invoke it.
Stage 2 of norm_extractor_spec_v1.md §3 — no model call, a search over the
tuples the norm extractor already produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capacity import project_capacities
from ..model import CapacityRecord, Finding, NormTuple

DETECTOR_VERSION = "k-d3/1.0.0"


@dataclass
class KD3Result:
    capacities: list[CapacityRecord] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def n_external(self) -> int:
        """Externals are reported separately on purpose: a K-D3 rate that
        collapses once they are excluded is not a finding."""
        return sum(1 for c in self.capacities if c.verdict == "external")

    @property
    def n_pathway_found(self) -> int:
        return sum(1 for c in self.capacities if c.verdict == "pathway_found")

    @property
    def n_no_pathway(self) -> int:
        return sum(1 for c in self.capacities if c.verdict == "no_pathway")

    def summary(self) -> dict:
        return {
            "capacities": len(self.capacities),
            "external": self.n_external,
            "pathway_found": self.n_pathway_found,
            "no_pathway": self.n_no_pathway,
            "findings": len(self.findings),
        }


def detect_kd3(tuples: list[NormTuple], corpus_id: str, run_id: str) -> KD3Result:
    by_id = {t.tuple_id: t for t in tuples}
    capacities = project_capacities(tuples)
    findings: list[Finding] = []

    for cap in capacities:
        if cap.verdict != "no_pathway":
            continue
        source = by_id.get(cap.tuple_id)
        locus = source.chain_link if source else None
        findings.append(Finding(
            run_id=run_id,
            corpus_id=corpus_id,
            kernel_id="K-D3",
            stratum="D",
            locus=locus,
            detector_version=DETECTOR_VERSION,
            detection_method="structural",
            subjects=[cap.capacity],
            source_locators=[source.source_locator] if source and source.source_locator else [],
            justification=(
                f"Capacity conferred at {locus or 'an unassigned link'} "
                f"with no invocation pathway specified in corpus."
            ),
            # Externals were already excluded when the capacity was projected,
            # so what remains is grounded in the source text, not our translation.
            provenance="source",
            confidence=0.6 if (source and source.confidence == "low") else 0.8,
        ))

    return KD3Result(capacities=capacities, findings=findings)
