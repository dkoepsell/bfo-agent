"""A thin adapter over the chain binding that already exists.

Aperture does not reimplement binding. :func:`app.recognition.anchor_ok` decides
whether a term anchored at a given BFO fragment is admissible at a given locus,
and this module does nothing but run that decision over an artifact's classes
and count the result.

The counts feed exactly one thing: the advisory cross-check in Phase 2, which
warns when a link is declared ``performed`` but nothing in the vocabulary binds
to it. That warning never overrides the declaration.

**Why the permissive reading.** A class anchored at process is admissible at act,
facts and remedy all at once, and this module reports it at all three rather than
picking one. That makes the cross-check conservative: it fires only when not even
a generous reading finds anything, so when it does fire the signal is solid. A
canonical one-locus-per-anchor reading would produce more warnings and weaker
ones.

``input_digest`` covers the artifact, the adapter version and the chain anchors
together. If the upstream anchors change, the digest changes and any cached
resolution built on this binding is invalidated rather than silently reused.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .. import bfo_catalog
from .. import recognition as rec
from .probe import Closure, _ancestor_index, _named_classes

ADAPTER_VERSION = "1.0.0"


@dataclass(frozen=True)
class BindingResult:
    """Which chain loci the artifact's vocabulary could occupy."""

    input_digest: str
    adapter_version: str
    per_locus: dict[str, tuple[str, ...]]
    counts: dict[str, int]
    named_classes: int
    unanchored_classes: int

    def binds(self, locus: str) -> bool:
        return self.counts.get(locus, 0) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_digest": self.input_digest,
            "adapter_version": self.adapter_version,
            "named_classes": self.named_classes,
            "unanchored_classes": self.unanchored_classes,
            "counts": self.counts,
            "sample": {locus: list(iris[:25]) for locus, iris in self.per_locus.items()},
        }


def _anchor_signature() -> str:
    """A digest of the chain anchors this adapter binds against."""
    payload = json.dumps(
        {spec.locus.value: sorted(spec.anchors) for spec in rec.CHAIN},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def bind(closure: Closure) -> BindingResult:
    """Bind every named class in the artifact to the loci it could occupy."""
    ancestors = _ancestor_index(closure.merged)
    named = _named_classes(closure)

    per_locus: dict[str, list[str]] = {locus.value: [] for locus in rec.LOCUS_ORDER}
    unanchored = 0

    for cls in sorted(named):
        frags = {bfo_catalog.normalize_fragment(a) for a in ancestors.get(cls, set())}
        frags.add(bfo_catalog.normalize_fragment(cls))
        frags = {f for f in frags if f.startswith("BFO_")}
        if not frags:
            unanchored += 1
            continue
        bound = False
        for locus in rec.LOCUS_ORDER:
            if any(rec.anchor_ok(locus, frag) for frag in frags):
                per_locus[locus.value].append(cls)
                bound = True
        if not bound:
            unanchored += 1

    digest_payload = f"{closure.sha256}:{ADAPTER_VERSION}:{_anchor_signature()}"
    return BindingResult(
        input_digest="sha256:" + hashlib.sha256(
            digest_payload.encode("utf-8")).hexdigest(),
        adapter_version=ADAPTER_VERSION,
        per_locus={k: tuple(v) for k, v in per_locus.items()},
        counts={k: len(v) for k, v in per_locus.items()},
        named_classes=len(named),
        unanchored_classes=unanchored,
    )
