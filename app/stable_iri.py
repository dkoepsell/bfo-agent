"""Deterministic individual IRIs (bfo-agent-spec.md FR-6, MC-3).

FR-6: two runs over the same source against the same kernel version must produce
the same set of IRIs; individual IRIs may differ from the source label but MUST
be stable-hashed from the source so diffs are meaningful. MC-3 adds: same input
-> byte-identical output.

This is BFO-general (it applies to every ontology, not just SOoL). The hash is a
pure function of the entity's content -- no randomness, no timestamps -- so it is
reproducible across runs and machines.
"""
from __future__ import annotations

import hashlib
import re

_SLUG = re.compile(r"[^A-Za-z0-9]+")


def _slug(label: str, maxlen: int = 32) -> str:
    s = _SLUG.sub("_", (label or "").strip()).strip("_")
    return (s[:maxlen] or "ind")


def content_hash(*parts: str, length: int = 10) -> str:
    """Stable hex digest of the given content parts. Deterministic: sha256 over
    a canonical join, truncated. No Date/random, so it is resume-safe."""
    canonical = "".join((p or "").strip() for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def stable_local_name(label: str, bfo_type: str = "", source: str = "",
                      prefix: str | None = None) -> str:
    """A deterministic, diffable local name for an individual.

    Form: ``<slug>_<hash>`` where the hash is taken over (label, bfo_type,
    source). Same content -> same name on every run (FR-6); different content ->
    different name. ``source`` lets a caller scope the hash to a case/scenario so
    two cases that mention the same label still get distinct individuals.
    """
    h = content_hash(label, bfo_type, source)
    base = prefix if prefix else _slug(label)
    return f"{base}_{h}"


def _ref_local(ref: str) -> str:
    s = (ref or "").split("#")[-1]
    s = s.split("/")[-1]
    return s.split(":")[-1]


def remap_proposal(proposal, source: str = ""):
    """Return a copy of ``proposal`` with every newly-minted individual given a
    stable, content-hashed local name (FR-6), keeping relation endpoints in sync.

    Pure function: classes and reused (existing_iri) entities are left untouched;
    only new individuals are remapped, and any relation ``s``/``o`` that pointed
    at a remapped individual is rewritten to match. Reproducible across runs.
    """
    name_map: dict[str, str] = {}
    new_entities = []
    for ent in proposal.entities:
        is_individual = getattr(ent, "kind", "individual") != "class"
        reused = bool(getattr(ent, "existing_iri", ""))
        if is_individual and not reused:
            old_local = _ref_local(getattr(ent, "iri_suggestion", "")
                                   or getattr(ent, "label", ""))
            new_local = stable_local_name(
                getattr(ent, "label", ""), getattr(ent, "bfo_type", ""), source
            )
            if old_local:
                name_map[old_local] = new_local
            new_entities.append(
                ent.model_copy(update={"iri_suggestion": f"working:{new_local}"})
            )
        else:
            new_entities.append(ent)

    new_relations = []
    for rel in proposal.relations:
        upd = {}
        for field in ("s", "o"):
            loc = _ref_local(getattr(rel, field, ""))
            if loc in name_map:
                upd[field] = f"working:{name_map[loc]}"
        new_relations.append(rel.model_copy(update=upd) if upd else rel)

    return proposal.model_copy(
        update={"entities": new_entities, "relations": new_relations}
    )
