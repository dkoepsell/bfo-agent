"""Chain links are asserted in the artifact, not inferred by the auditor.

This is the root fix for the binding smear. The adapter produced 3,383 bindings
over 1,009 classes because it was asked to map an anatomical taxonomy onto seven
legal links, so it mapped almost everything onto most of them. Asked an
unanswerable question, it answered anyway.

So the question changes. A class states its own link with ``sool:mlcLink``, and
the adapter's job becomes verification: read what is asserted, report coverage
per link, name the gaps. Where inference is genuinely wanted it runs as a
separate suggestion whose output is a proposal for a person to accept, never
written into the audited artifact and never counted as a binding.

The rule this enforces: the auditor never manufactures domain structure. If a
link is not asserted, it is reported absent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import rdflib
from rdflib import RDF, URIRef

SOOL = "http://davidkoepsell.com/sool#"
MLC_LINK = URIRef(SOOL + "mlcLink")
MLC_LINK_JUSTIFICATION = URIRef(SOOL + "mlcLinkJustification")

LINK_NAMESPACE = "http://davidkoepsell.com/sool/chain#"

# The seven canonical link IRIs a class may name.
LINKS = ("authority", "criteria", "assessor", "facts", "act", "effect", "remedy")
LINK_IRIS = {locus: LINK_NAMESPACE + locus for locus in LINKS}
IRI_TO_LINK = {v: k for k, v in LINK_IRIS.items()}


def _link_of(value) -> Optional[str]:
    """Resolve an asserted value to a canonical link name, or None."""
    text = str(value).strip()
    if text in IRI_TO_LINK:
        return IRI_TO_LINK[text]
    tail = text.rsplit("#", 1)[-1].rsplit("/", 1)[-1].lower()
    return tail if tail in LINKS else None


def read_asserted_links(source) -> dict[str, tuple[str, ...]]:
    """``link -> classes that assert it``. Reads only; infers nothing."""
    g = source if isinstance(source, rdflib.Graph) else rdflib.Graph()
    if not isinstance(source, rdflib.Graph):
        g.parse(str(source))

    out: dict[str, list[str]] = {locus: [] for locus in LINKS}
    for subject, value in g.subject_objects(MLC_LINK):
        if not isinstance(subject, URIRef):
            continue
        link = _link_of(value)
        if link:
            out[link].append(str(subject))
    return {k: tuple(sorted(set(v))) for k, v in out.items()}


@dataclass(frozen=True)
class Verification:
    """What the artifact asserts about its own chain, and where the gaps are."""

    asserted: dict[str, tuple[str, ...]]
    named_classes: int
    multi_linked: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unjustified_multi: tuple[str, ...] = ()

    @property
    def total_assertions(self) -> int:
        return sum(len(v) for v in self.asserted.values())

    @property
    def empty_links(self) -> tuple[str, ...]:
        return tuple(l for l in LINKS if not self.asserted.get(l))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "verification",
            "note": ("Links are read from sool:mlcLink assertions in the "
                     "artifact. Nothing here is inferred: a link with no "
                     "asserted member is reported absent, not guessed at."),
            "counts": {k: len(v) for k, v in self.asserted.items()},
            "total_assertions": self.total_assertions,
            "named_classes": self.named_classes,
            "empty_links": list(self.empty_links),
            "coverage_pct": {
                k: round(100 * len(v) / self.named_classes, 2) if self.named_classes
                else 0.0
                for k, v in self.asserted.items()
            },
            "multi_linked": {k: list(v) for k, v in self.multi_linked.items()},
            "unjustified_multi": list(self.unjustified_multi),
            "sample": {k: list(v[:25]) for k, v in self.asserted.items()},
        }


def verify(source, named_classes: int = 0) -> Verification:
    """Read the asserted links and report coverage and gaps.

    A class may carry more than one link only with ``sool:mlcLinkJustification``.
    One without is reported, because multiple membership is exactly how the
    smear looked and it should have to be argued for.
    """
    g = source if isinstance(source, rdflib.Graph) else rdflib.Graph()
    if not isinstance(source, rdflib.Graph):
        g.parse(str(source))

    asserted = read_asserted_links(g)

    if not named_classes:
        from rdflib import OWL

        named_classes = len({s for s in g.subjects(RDF.type, OWL.Class)
                             if isinstance(s, URIRef)})

    per_class: dict[str, list[str]] = {}
    for link, members in asserted.items():
        for member in members:
            per_class.setdefault(member, []).append(link)

    multi = {c: tuple(sorted(links)) for c, links in per_class.items()
             if len(links) > 1}
    justified = {str(s) for s in g.subjects(MLC_LINK_JUSTIFICATION, None)}
    unjustified = tuple(sorted(c for c in multi if c not in justified))

    return Verification(
        asserted=asserted,
        named_classes=named_classes,
        multi_linked=multi,
        unjustified_multi=unjustified,
    )


def suggest(per_locus_bindings: dict[str, Iterable[str]],
            artifact_sha256: str = "") -> dict[str, Any]:
    """Turn an inferred binding into a proposal for a person to accept.

    Never written into the audited artifact and never counted as a binding. The
    output is a file a human reads, edits and applies, which is the only place
    inference belongs once the auditor has stopped doing it silently.
    """
    proposals = []
    for link, members in sorted((per_locus_bindings or {}).items()):
        for member in sorted(set(members)):
            proposals.append({
                "class": member,
                "proposed_link": link,
                "accepted": False,
            })
    return {
        "kind": "mlc_link_proposal",
        "artifact_sha256": artifact_sha256,
        "note": ("A proposal, not a binding. Nothing here is asserted in the "
                 "artifact and nothing here was counted in any audit. Set "
                 "accepted to true on the rows you agree with, then apply."),
        "proposal_count": len(proposals),
        "proposals": proposals,
    }
