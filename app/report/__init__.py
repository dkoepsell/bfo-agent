"""Full report: every audit, the statistics, and a digestible variant of the OWL.

One bundle that answers three different questions at once:

* what is in this ontology (statistics),
* what is wrong with it and what could not be looked at (the audits, plus the
  Aperture scope calculus),
* and what a tool would need in order to load it at all (the repaired variant).

The third of those is kept strictly separate from the first two. Results in this
bundle are about the artifact **as delivered**. The repaired variant is a second,
named artifact, and no audit result is ever computed from it and reported as
though it described the original. That separation is the same rule Aperture
enforces, and it is the reason the repair is limited to changes that add no
claims.
"""
from __future__ import annotations

from .bundle import build_report, write_bundle
from .repair import RepairResult, repair_for_digestibility

__all__ = [
    "RepairResult",
    "build_report",
    "repair_for_digestibility",
    "write_bundle",
]
