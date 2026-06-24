"""Shared BFO 2020 bundle: catalog, disjointness closure, relation signatures.

This module is deliberately free of any owlready2 / reasoner dependency so the
generation agent and any downstream tester can import the same source of truth
about what BFO asserts. Everything here is derived from the vendored
``ontology/bfo.owl`` (ISO/IEC 21838-2, BFO 2020) and pinned as plain data. The
drift-guard test in ``tests/test_bfo_catalog.py`` re-parses ``bfo.owl`` and
asserts these tables still match the file, so they cannot silently rot.

The lint tier of the coherence gate uses ``clash`` / ``straddles`` to catch
disjoint-parent straddles (the classic "Force is a Quality and Force is a
Disposition" defect) with no reasoner in the hot path.

Note on BFO structure (verified against bfo.owl): Function (BFO_0000034) is a
subClassOf Disposition (BFO_0000016), NOT a sibling. So Function and Disposition
do not clash. The only disjoint realizable pair is Role vs Disposition.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Fragment of the BFO IRI namespace, e.g. BFO_0000019 ->
# http://purl.obolibrary.org/obo/BFO_0000019
BFO_OBO_PREFIX = "http://purl.obolibrary.org/obo/"


# ---------------------------------------------------------------------------
# Catalog: fragment -> (immediate BFO parent fragment or None, human label).
# Derived from bfo.owl is_a edges among BFO_ classes.
# ---------------------------------------------------------------------------
_CATALOG: dict[str, tuple[Optional[str], str]] = {
    "BFO_0000001": (None, "entity"),
    "BFO_0000002": ("BFO_0000001", "continuant"),
    "BFO_0000003": ("BFO_0000001", "occurrent"),
    "BFO_0000004": ("BFO_0000002", "independent continuant"),
    "BFO_0000006": ("BFO_0000141", "spatial region"),
    "BFO_0000008": ("BFO_0000003", "temporal region"),
    "BFO_0000009": ("BFO_0000006", "two-dimensional spatial region"),
    "BFO_0000011": ("BFO_0000003", "spatiotemporal region"),
    "BFO_0000015": ("BFO_0000003", "process"),
    "BFO_0000016": ("BFO_0000017", "disposition"),
    "BFO_0000017": ("BFO_0000020", "realizable entity"),
    "BFO_0000018": ("BFO_0000006", "zero-dimensional spatial region"),
    "BFO_0000019": ("BFO_0000020", "quality"),
    "BFO_0000020": ("BFO_0000002", "specifically dependent continuant"),
    "BFO_0000023": ("BFO_0000017", "role"),
    "BFO_0000024": ("BFO_0000040", "fiat object part"),
    "BFO_0000026": ("BFO_0000006", "one-dimensional spatial region"),
    "BFO_0000027": ("BFO_0000040", "object aggregate"),
    "BFO_0000028": ("BFO_0000006", "three-dimensional spatial region"),
    "BFO_0000029": ("BFO_0000141", "site"),
    "BFO_0000030": ("BFO_0000040", "object"),
    "BFO_0000031": ("BFO_0000002", "generically dependent continuant"),
    "BFO_0000034": ("BFO_0000016", "function"),
    "BFO_0000035": ("BFO_0000003", "process boundary"),
    "BFO_0000038": ("BFO_0000008", "one-dimensional temporal region"),
    "BFO_0000040": ("BFO_0000004", "material entity"),
    "BFO_0000140": ("BFO_0000141", "continuant fiat boundary"),
    "BFO_0000141": ("BFO_0000004", "immaterial entity"),
    "BFO_0000142": ("BFO_0000140", "fiat line"),
    "BFO_0000145": ("BFO_0000019", "relational quality"),
    "BFO_0000146": ("BFO_0000140", "fiat surface"),
    "BFO_0000147": ("BFO_0000140", "fiat point"),
    "BFO_0000148": ("BFO_0000008", "zero-dimensional temporal region"),
    "BFO_0000182": ("BFO_0000015", "history"),
    "BFO_0000202": ("BFO_0000038", "temporal interval"),
    "BFO_0000203": ("BFO_0000148", "temporal instant"),
}

# Convenience views.
BFO_PARENT: dict[str, Optional[str]] = {f: p for f, (p, _) in _CATALOG.items()}
BFO_LABEL: dict[str, str] = {f: lbl for f, (_, lbl) in _CATALOG.items()}


# ---------------------------------------------------------------------------
# Disjointness: the asserted AllDisjointClasses groups, copied verbatim from
# bfo.owl. All BFO disjointness is asserted at sibling level only; the
# transitive closure that catches cross-level straddles is computed by clash().
# ---------------------------------------------------------------------------
DISJOINT_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"BFO_0000002", "BFO_0000003"}),                  # continuant / occurrent
    frozenset({"BFO_0000004", "BFO_0000020", "BFO_0000031"}),   # IC / SDC / GDC
    frozenset({"BFO_0000017", "BFO_0000019"}),                  # realizable / quality
    frozenset({"BFO_0000016", "BFO_0000023"}),                  # disposition / role
    frozenset({"BFO_0000040", "BFO_0000141"}),                  # material / immaterial
    frozenset({"BFO_0000006", "BFO_0000029", "BFO_0000140"}),   # spatial region / site / cont fiat boundary
    frozenset({"BFO_0000008", "BFO_0000011", "BFO_0000015", "BFO_0000035"}),  # temporal / spatiotemporal / process / process boundary
    frozenset({"BFO_0000009", "BFO_0000018", "BFO_0000026", "BFO_0000028"}),  # 2D / 0D / 1D / 3D spatial regions
    frozenset({"BFO_0000038", "BFO_0000148"}),                  # 1D / 0D temporal region
    frozenset({"BFO_0000142", "BFO_0000146", "BFO_0000147"}),   # fiat line / surface / point
)

# Pairwise expansion of the asserted groups (the directly-disjoint pairs).
DISJOINT_PAIRS: frozenset[frozenset[str]] = frozenset(
    frozenset({a, b})
    for grp in DISJOINT_GROUPS
    for a in grp
    for b in grp
    if a < b
)


def normalize_fragment(value: str) -> str:
    """Reduce any BFO reference to its bare fragment (BFO_00000xx).

    Accepts full IRIs, ``bfo:BFO_...``, ``obo:BFO_...``, or bare fragments.
    Non-BFO values pass through unchanged so callers can detect them.
    """
    s = value.strip()
    if "#" in s:
        s = s.rsplit("#", 1)[-1]
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    if ":" in s and s.split(":", 1)[1].startswith("BFO_"):
        s = s.split(":", 1)[1]
    return s


def bfo_ancestors(fragment: str) -> list[str]:
    """Return self plus all BFO ancestors via BFO_PARENT, nearest first.

    A fragment not in the catalog (e.g. a working-ontology class that was not
    resolved to its BFO anchor) yields just itself, so clash() degrades to a
    no-op rather than raising.
    """
    frag = normalize_fragment(fragment)
    chain: list[str] = []
    cur: Optional[str] = frag
    seen: set[str] = set()
    while cur is not None and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = BFO_PARENT.get(cur)
    return chain


def clash(cat_a: str, cat_b: str) -> bool:
    """True iff cat_a and cat_b descend from an asserted-disjoint BFO pair.

    This is the transitive closure of the asserted sibling disjointness:
    Quality (BFO_0000019) clashes with Disposition (BFO_0000016) because
    Quality's ancestor BFO_0000019 is disjoint from Disposition's ancestor
    Realizable (BFO_0000017). Function (BFO_0000034) does NOT clash with
    Disposition, because Function is a subClassOf Disposition.
    """
    anc_a = bfo_ancestors(cat_a)
    anc_b = bfo_ancestors(cat_b)
    for x in anc_a:
        for y in anc_b:
            if x != y and frozenset({x, y}) in DISJOINT_PAIRS:
                return True
    return False


def straddles(parent_bfo_categories: Iterable[str]) -> tuple[bool, Optional[tuple[str, str]]]:
    """Test a resulting parent set for any clashing pair.

    Returns (True, (a, b)) for the first clashing pair found, else (False, None).
    """
    cats = [normalize_fragment(c) for c in parent_bfo_categories]
    for i, a in enumerate(cats):
        for b in cats[i + 1:]:
            if clash(a, b):
                return True, (a, b)
    return False, None


def describe_clash(a: str, b: str) -> str:
    """Human-readable reason naming the disjoint categories, no em-dashes."""
    la = BFO_LABEL.get(normalize_fragment(a), a)
    lb = BFO_LABEL.get(normalize_fragment(b), b)
    return (
        f"BFO disjointness violation: a class cannot be both a {la} "
        f"({normalize_fragment(a)}) and a {lb} ({normalize_fragment(b)}); "
        f"these BFO categories are disjoint."
    )


# ---------------------------------------------------------------------------
# Relation signatures: domain / range / characteristics for the BFO relations
# the proposer is allowed to use. Used by the gate's reasoner tier (so the
# loaded dry-run world carries them) and by Task 4 scaffolding.
# ---------------------------------------------------------------------------
def relation_signatures() -> dict[str, dict]:
    """Return {fragment: {label, domain, range, characteristics}} for BFO relations."""
    # IRIs are the BFO 2020 native relations as they appear in bfo.owl.
    return {
        "BFO_0000050": {"label": "part of", "domain": None, "range": None,
                        "characteristics": ["transitive"]},
        "BFO_0000051": {"label": "has part", "domain": None, "range": None,
                        "characteristics": ["transitive"]},
        "BFO_0000197": {"label": "inheres in", "domain": "BFO_0000020",
                        "range": "BFO_0000004", "characteristics": []},
        "BFO_0000196": {"label": "bearer of", "domain": "BFO_0000004",
                        "range": "BFO_0000020", "characteristics": []},
        "BFO_0000054": {"label": "has realization", "domain": "BFO_0000017",
                        "range": "BFO_0000015", "characteristics": []},
        "BFO_0000055": {"label": "realizes", "domain": "BFO_0000015",
                        "range": "BFO_0000017", "characteristics": []},
        "BFO_0000056": {"label": "participates in", "domain": "BFO_0000002",
                        "range": "BFO_0000003", "characteristics": []},
    }


# Useful category groupings for the gate / scaffolding.
QUALITY = "BFO_0000019"
REALIZABLE = "BFO_0000017"
ROLE = "BFO_0000023"
DISPOSITION = "BFO_0000016"
FUNCTION = "BFO_0000034"
SDC = "BFO_0000020"
INDEPENDENT_CONTINUANT = "BFO_0000004"
PROCESS = "BFO_0000015"
CONTINUANT = "BFO_0000002"
OCCURRENT = "BFO_0000003"


def is_descendant_of(fragment: str, ancestor: str) -> bool:
    """True iff fragment is ancestor or a BFO descendant of it."""
    return normalize_fragment(ancestor) in bfo_ancestors(fragment)
