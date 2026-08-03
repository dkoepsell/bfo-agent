"""Dispersion measures for chain binding, and the gates that read them.

The failure this addresses: 3,383 bindings over 1,009 classes, published sample
sets for criteria and effect identical at 25 of 25, remedy capturing 79 percent
of the ontology, and one anatomical class bound to authority, criteria and effect
at once. The section reported ok, with seven tidy counts and no dispersion
figure at all.

Seven tidy counts cannot show a smear. These measures can, and every one is
computed over the full sets rather than over the published sample, because a
sample of 25 was how the identical link sets stayed invisible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any, Iterable, Optional

# Gate thresholds. A binding adapter mapping a taxonomy onto seven legal links
# will smear across them; these are the shapes that smear takes.
DISPERSION_MAX_MEAN = 1.5
EXCLUSIVITY_MIN = 0.50
CAPTURE_MAX_PCT = 60.0
JACCARD_MAX = 0.60


@dataclass(frozen=True)
class Dispersion:
    bindings_total: int
    bound_classes: int
    unbound_classes: int
    bindings_per_class_mean: float
    bindings_per_class_median: float
    bindings_per_class_max: int
    exclusivity_rate: float
    capture_pct: dict[str, float]
    jaccard: dict[str, dict[str, float]]
    top_multiplicity: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bindings_total": self.bindings_total,
            "bound_classes": self.bound_classes,
            "unbound_classes": self.unbound_classes,
            "bindings_per_class_mean": round(self.bindings_per_class_mean, 4),
            "bindings_per_class_median": self.bindings_per_class_median,
            "bindings_per_class_max": self.bindings_per_class_max,
            "exclusivity_rate": round(self.exclusivity_rate, 4),
            "capture_pct": {k: round(v, 2) for k, v in self.capture_pct.items()},
            "jaccard": {a: {b: round(v, 4) for b, v in row.items()}
                        for a, row in self.jaccard.items()},
            "top_multiplicity": list(self.top_multiplicity),
        }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def compute_dispersion(per_locus: dict[str, Iterable[str]],
                       named_classes: int,
                       unbound_classes: int = 0) -> Dispersion:
    """Measure how far the binding spreads, over the full sets.

    ``per_locus`` maps each link to every class bound to it, not to a sample.
    """
    sets = {locus: set(members) for locus, members in (per_locus or {}).items()}

    multiplicity: dict[str, int] = {}
    for members in sets.values():
        for cls in members:
            multiplicity[cls] = multiplicity.get(cls, 0) + 1

    counts = list(multiplicity.values())
    bound = len(multiplicity)
    exclusive = sum(1 for n in counts if n == 1)

    denominator = named_classes or bound or 1
    capture = {locus: 100 * len(members) / denominator
               for locus, members in sets.items()}

    loci = sorted(sets)
    jaccard = {a: {b: (1.0 if a == b else _jaccard(sets[a], sets[b]))
                   for b in loci} for a in loci}

    top = sorted(multiplicity.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    top_multiplicity = [
        {"class": cls, "links": n,
         "bound_to": sorted(l for l in loci if cls in sets[l])}
        for cls, n in top
    ]

    return Dispersion(
        bindings_total=sum(len(m) for m in sets.values()),
        bound_classes=bound,
        unbound_classes=unbound_classes,
        bindings_per_class_mean=mean(counts) if counts else 0.0,
        bindings_per_class_median=median(counts) if counts else 0.0,
        bindings_per_class_max=max(counts) if counts else 0,
        exclusivity_rate=(exclusive / bound) if bound else 0.0,
        capture_pct=capture,
        jaccard=jaccard,
        top_multiplicity=top_multiplicity,
    )


def dispersion_gates(d: Dispersion) -> list[dict[str, Any]]:
    """The four gates. Any failure makes the binding section unreliable.

    Returned as plain dicts so the caller can build whatever Gate type it uses
    without this module depending on the report layer.
    """
    gates: list[dict[str, Any]] = []

    gates.append({
        "id": "binding.dispersion",
        "passed": d.bindings_per_class_mean <= DISPERSION_MAX_MEAN,
        "detail": (f"mean {d.bindings_per_class_mean:.2f} links per bound class "
                   f"(threshold {DISPERSION_MAX_MEAN}). A class belongs at one "
                   f"link; a high mean means the adapter is spreading rather "
                   f"than deciding."),
    })

    gates.append({
        "id": "binding.exclusivity",
        "passed": d.exclusivity_rate >= EXCLUSIVITY_MIN,
        "detail": (f"{d.exclusivity_rate:.0%} of bound classes are bound to "
                   f"exactly one link (threshold {EXCLUSIVITY_MIN:.0%})."),
    })

    worst_link, worst_pct = "", 0.0
    for locus, pct in d.capture_pct.items():
        if pct > worst_pct:
            worst_link, worst_pct = locus, pct
    gates.append({
        "id": "binding.capture",
        "passed": worst_pct <= CAPTURE_MAX_PCT,
        "detail": (f"largest capture is {worst_link} at {worst_pct:.0f}% of all "
                   f"classes (threshold {CAPTURE_MAX_PCT:.0f}%). One link holding "
                   f"most of the ontology is not a binding."),
    })

    worst_pair, worst_j = ("", ""), 0.0
    for a, row in d.jaccard.items():
        for b, value in row.items():
            if a < b and value > worst_j:
                worst_pair, worst_j = (a, b), value
    gates.append({
        "id": "binding.jaccard",
        "passed": worst_j <= JACCARD_MAX,
        "detail": (f"most similar link pair is {worst_pair[0]}/{worst_pair[1]} at "
                   f"{worst_j:.2f} (threshold {JACCARD_MAX}). Two links holding "
                   f"the same classes are not two links."),
    })

    return gates


def render_jaccard(d: Dispersion) -> list[str]:
    """The matrix as markdown, with tripped cells marked.

    Rendered whenever any gate trips, because the number that explains the smear
    is the one showing two links holding the same set.
    """
    loci = sorted(d.jaccard)
    if not loci:
        return []
    lines = ["| | " + " | ".join(loci) + " |",
             "|---|" + "|".join(["---"] * len(loci)) + "|"]
    for a in loci:
        cells = []
        for b in loci:
            value = d.jaccard[a][b]
            if a != b and value > JACCARD_MAX:
                cells.append(f"**{value:.2f}**")
            else:
                cells.append(f"{value:.2f}")
        lines.append(f"| **{a}** | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"Cells in bold exceed the {JACCARD_MAX} threshold: those links "
                 f"are holding substantially the same classes.")
    return lines
