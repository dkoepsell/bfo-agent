"""OWL -> LADR (Prover9/Mace4) translation (fol-gate-spec.md TR-1..TR-6).

Two signature modes:

  Mode A (untemporalized, self-contained): each class becomes a unary
  predicate, each object property a binary predicate. The BFO skeleton
  (hierarchy + disjointness from bfo_catalog) is folded in so the clause set
  is a standalone cross-check of the owlready2/HermiT path.

  Mode B (BFO-2020 conformance): class membership is bridged into the exact
  signature of the vendored ISO/IEC 21838-2 Prover9 axioms --
  ``instanceOf(P, classConstant, T)`` / ``existsAt(P, T)`` -- so the module
  can be merged with the real first-order BFO axioms.

Temporal bridge assumptions (TR-5), chosen so a Mode-B inconsistency can only
stem from something the OWL genuinely asserts:
  * rigidity: an OWL class assertion ``A(a)`` becomes
    ``all T (existsAt(a,T) -> instanceOf(a, A, T))``;
  * snapshot witness: OWL relation assertions are facts about one shared
    witness time ``t_abox`` at which every ABox individual exists (OWL has no
    time argument; asserting the relations at a single common snapshot is the
    weakest reading that still lets the temporalized axioms see them).

Nothing here reads or writes the ontology file beyond loading it (FG-0).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import bfo_catalog
from . import config

BFO_PREFIX = "http://purl.obolibrary.org/obo/"
ABOX_TIME = "t_abox"

# BFO 2020 OWL object properties -> vendored-axiom predicates.
# (predicate, arity, swap_args). Arity 3 predicates take the witness time as
# the final argument. Verified against ontology/bfo-2020-fol/*.prover9.
BFO_REL_MAP: dict[str, tuple[str, int, bool]] = {
    "BFO_0000197": ("inheresIn", 2, False),
    "BFO_0000196": ("bearerOf", 2, False),
    "BFO_0000176": ("continuantPartOf", 3, False),
    "BFO_0000178": ("hasContinuantPart", 3, False),
    "BFO_0000056": ("participatesIn", 3, False),
    "BFO_0000057": ("hasParticipant", 3, False),
    "BFO_0000055": ("realizes", 2, False),
    # "r has-realization p" is realizes(p, r) in the axioms.
    "BFO_0000054": ("realizes", 2, True),
    "BFO_0000132": ("occurrentPartOf", 2, False),
    "BFO_0000117": ("occurrentPartOf", 2, True),
    "BFO_0000066": ("occursIn", 2, False),
}


@dataclass
class Translation:
    mode: str
    formulas: list[str] = field(default_factory=list)
    # TR-3: axioms outside the whitelist, reported, never silently dropped.
    skipped: list[dict] = field(default_factory=list)
    # TR-5: Mode-B relations with no temporalized mapping (kept binary).
    unbridged: list[dict] = field(default_factory=list)
    symbols: dict[str, str] = field(default_factory=dict)
    _seen: set = field(default_factory=set, repr=False)

    def add(self, formula: str) -> None:
        # Set-backed dedup: real ontologies emit tens of thousands of
        # formulas; a list-membership check here is quadratic wall-clock.
        if formula not in self._seen:
            self._seen.add(formula)
            self.formulas.append(formula)

    def skip(self, axiom: str, reason: str) -> None:
        self.skipped.append({"axiom": axiom, "reason": reason})

    def sos_block(self) -> str:
        body = "\n".join(f"  {f}" for f in self.formulas)
        return (
            "set(prolog_style_variables).\n\n"
            "formulas(sos).\n" + body + "\nend_of_list.\n"
        )


# ------------------------------------------------------------- symbols
def _camel(label: str) -> str:
    words = re.split(r"[\s\-]+", label.strip())
    return words[0].lower() + "".join(w.capitalize() for w in words[1:] if w)


def _declared_bfo_constants() -> set[str]:
    """Constants declared ``universal(...)`` in the vendored axioms (TR-6)."""
    path = Path(config.FOL_AXIOMS_DIR) / "universal-declaration.prover9"
    if not path.exists():
        return set()
    return set(re.findall(r"universal\(([a-zA-Z]+)\)", path.read_text()))


class _Unsupported(Exception):
    """An axiom outside the translation whitelist (TR-2/TR-3): the caller
    records it as SKIPPED, never drops it silently."""


def bfo_class_constant(fragment: str, declared: set[str]) -> Optional[str]:
    """Map a BFO_... fragment to its axiom-file constant, or None for the
    trivially-droppable root (entity). A fragment the catalog does not know
    (e.g. a bfo.owl class outside the curated kernel) is _Unsupported --
    the axiom mentioning it gets skipped; a catalog-known constant missing
    from the vendored declarations is a genuinely stale table (TR-6)."""
    label = bfo_catalog.BFO_LABEL.get(fragment)
    if label is None:
        raise _Unsupported(f"BFO class {fragment} outside the catalog kernel")
    const = _camel(label)
    if const in declared:
        return const
    if fragment == "BFO_0000001":  # entity: not a declared universal; a
        return None                # subClassOf-entity axiom is trivial anyway
    raise ValueError(
        f"BFO constant {const!r} ({fragment}) not declared in the vendored "
        f"universal-declaration.prover9 -- stale mapping table (TR-6)"
    )


class _Symbols:
    """Deterministic, collision-checked IRI -> LADR symbol minting (TR-6)."""

    def __init__(self, mode: str, declared: set[str]):
        self.mode = mode
        self.declared = declared
        self.by_iri: dict[str, str] = {}
        self.taken: set[str] = set(declared)

    def _mint(self, prefix: str, iri: str) -> str:
        local = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        base = prefix + re.sub(r"[^A-Za-z0-9_]", "_", local).lower()
        sym = base
        n = 2
        while sym in self.taken and self.by_iri.get(iri) != sym:
            sym = f"{base}_{n}"
            n += 1
        self.taken.add(sym)
        self.by_iri[iri] = sym
        return sym

    def cls(self, iri: str) -> Optional[str]:
        if iri in self.by_iri:
            return self.by_iri[iri]
        frag = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        if frag.startswith("BFO_") and self.mode == "B":
            const = bfo_class_constant(frag, self.declared)
            if const is None:
                return None
            self.by_iri[iri] = const
            self.taken.add(const)
            return const
        prefix = "c_" if self.mode == "A" else "w_"
        return self._mint(prefix, iri)

    def ind(self, iri: str) -> str:
        return self.by_iri.get(iri) or self._mint("i_", iri)

    def rel(self, iri: str) -> tuple[str, int, bool]:
        """(predicate, arity, swap). Mode B maps known BFO relations onto the
        vendored temporalized signature; everything else stays binary."""
        frag = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        if self.mode == "B" and frag in BFO_REL_MAP:
            return BFO_REL_MAP[frag]
        if iri in self.by_iri:
            return (self.by_iri[iri], 2, False)
        return (self._mint("r_", iri), 2, False)


# ------------------------------------------------------- expression builder
class _Emitter:
    def __init__(self, tr: Translation, syms: _Symbols):
        self.tr = tr
        self.syms = syms
        self._var = 0

    def fresh(self) -> str:
        self._var += 1
        return f"V{self._var}"

    def member(self, x: str, cls_sym: Optional[str], t: str) -> str:
        if cls_sym is None:  # owl:Thing / BFO entity root -- tautology
            return f"({x} = {x})"
        if self.tr.mode == "A":
            return f"{cls_sym}({x})"
        return f"instanceOf({x},{cls_sym},{t})"

    def relate(self, prop, x: str, y: str, t: str) -> str:
        iri = getattr(prop, "iri", None)
        if iri is None and isinstance(prop, str) and \
                prop.startswith(("http://", "https://")):
            # owlready2 hands back a bare IRI string when the file references
            # a property it never materialized (seen on live corpora); the
            # IRI itself is usable.
            iri = prop
        if not iri:
            # Genuinely malformed restriction axioms (the persisted
            # "#_:label" bug) get skipped per TR-3 instead of crashing.
            raise _Unsupported(f"malformed property reference {prop!r}")
        pred, arity, swap = self.syms.rel(iri)
        a, b = (y, x) if swap else (x, y)
        if self.tr.mode == "B" and arity == 3:
            return f"{pred}({a},{b},{t})"
        return f"{pred}({a},{b})"

    def expr(self, x: str, cexpr, t: str) -> str:
        """Formula stating ``x`` is in the class expression at time ``t``."""
        import owlready2 as owl

        if isinstance(cexpr, owl.ThingClass):
            if cexpr is owl.Thing:
                return f"({x} = {x})"
            if cexpr is owl.Nothing:
                return f"(-({x} = {x}))"
            return self.member(x, self.syms.cls(cexpr.iri), t)
        if isinstance(cexpr, owl.And):
            return "(" + " & ".join(self.expr(x, c, t) for c in cexpr.Classes) + ")"
        if isinstance(cexpr, owl.Or):
            return "(" + " | ".join(self.expr(x, c, t) for c in cexpr.Classes) + ")"
        if isinstance(cexpr, owl.Not):
            return f"(-{self.expr(x, cexpr.Class, t)})"
        if isinstance(cexpr, owl.Restriction):
            from owlready2.class_construct import ONLY, SOME

            if cexpr.type == SOME:
                y = self.fresh()
                return (f"(exists {y} ({self.relate(cexpr.property, x, y, t)}"
                        f" & {self.expr(y, cexpr.value, t)}))")
            if cexpr.type == ONLY:
                y = self.fresh()
                return (f"(all {y} ({self.relate(cexpr.property, x, y, t)}"
                        f" -> {self.expr(y, cexpr.value, t)}))")
            raise _Unsupported(f"restriction type {cexpr.type}")
        raise _Unsupported(type(cexpr).__name__)


# ------------------------------------------------------------- translation
def _is_bfo(iri: str) -> bool:
    frag = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    return frag.startswith(("BFO_", "RO_", "IAO_"))


def _quant_head(tr: Translation) -> tuple[str, str, str]:
    """(quantifier prefix, subject var, time var) for a class-level axiom."""
    if tr.mode == "A":
        return "all X ", "X", ""
    return "all X all T ", "X", "T"


def _emit_bfo_skeleton(tr: Translation, em: _Emitter, syms: _Symbols) -> None:
    """BFO hierarchy + disjointness from the catalog. Mode A needs it (no
    axiom files are merged); Mode B gets the disjointness too -- redundant
    with the vendored axioms at worst, and it covers catalog pairs the
    sub-theory profile may not load."""
    q, x, t = _quant_head(tr)
    for child, parent in bfo_catalog.BFO_PARENT.items():
        if parent is None:
            continue
        c = syms.cls(BFO_PREFIX + child)
        p = syms.cls(BFO_PREFIX + parent)
        if c is None or p is None:
            continue
        if tr.mode == "A":
            tr.add(f"{q}({em.member(x, c, t)} -> {em.member(x, p, t)}).")
    for group in bfo_catalog.DISJOINT_GROUPS:
        members = sorted(group)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                ca, cb = syms.cls(BFO_PREFIX + a), syms.cls(BFO_PREFIX + b)
                if ca is None or cb is None:
                    continue
                tr.add(f"{q}(-({em.member(x, ca, t)} & {em.member(x, cb, t)})).")


def _translate_classes(tr, em, syms, world) -> None:
    import owlready2 as owl

    q, x, t = _quant_head(tr)
    for cls in world.classes():
        if _is_bfo(cls.iri):
            continue  # skeleton comes from the catalog, not the file walk
        c = syms.cls(cls.iri)
        if tr.mode == "B":
            tr.add(f"universal({c}).")
        for parent in cls.is_a:
            if parent is owl.Thing:
                continue
            try:
                tr.add(f"{q}({em.member(x, c, t)} -> {em.expr(x, parent, t)}).")
            except _Unsupported as e:
                tr.skip(f"{cls.name} subClassOf {parent!r}", str(e))
        for eq in cls.equivalent_to:
            try:
                tr.add(f"{q}({em.member(x, c, t)} <-> {em.expr(x, eq, t)}).")
            except _Unsupported as e:
                tr.skip(f"{cls.name} equivalentTo {eq!r}", str(e))


def _translate_disjoints(tr, em, syms, world) -> None:
    import owlready2 as owl

    q, x, t = _quant_head(tr)
    for onto in world.ontologies.values():
        for dis in onto.disjoints():
            ents = list(dis.entities)
            if all(isinstance(e, owl.ThingClass) for e in ents):
                for i, a in enumerate(ents):
                    for b in ents[i + 1:]:
                        try:
                            tr.add(f"{q}(-({em.expr(x, a, t)} & "
                                   f"{em.expr(x, b, t)})).")
                        except _Unsupported as e:
                            tr.skip(f"disjoint({a!r},{b!r})", str(e))
            elif all(isinstance(e, owl.Thing) for e in ents):
                for i, a in enumerate(ents):
                    for b in ents[i + 1:]:
                        tr.add(f"-({syms.ind(a.iri)} = {syms.ind(b.iri)}).")
            else:
                tr.skip(f"AllDisjoint({ents!r})", "mixed/unsupported disjoint")


def _translate_properties(tr, em, syms, world) -> None:
    import owlready2 as owl

    for prop in world.object_properties():
        if tr.mode == "B" and _is_bfo(prop.iri):
            frag = prop.iri.rsplit("/", 1)[-1]
            if frag not in BFO_REL_MAP:
                tr.unbridged.append(
                    {"property": prop.iri,
                     "reason": "no temporalized mapping; kept binary"}
                )
            continue  # the vendored axioms already govern mapped relations
        x, y, z = "X", "Y", "Z"
        for dom in prop.domain:
            try:
                tr.add(f"all X all Y ({em.relate(prop, x, y, ABOX_TIME)} -> "
                       f"{em.expr(x, dom, ABOX_TIME if tr.mode == 'B' else '')}).")
            except _Unsupported as e:
                tr.skip(f"domain({prop.name})", str(e))
        for rng in prop.range:
            try:
                tr.add(f"all X all Y ({em.relate(prop, x, y, ABOX_TIME)} -> "
                       f"{em.expr(y, rng, ABOX_TIME if tr.mode == 'B' else '')}).")
            except _Unsupported as e:
                tr.skip(f"range({prop.name})", str(e))
        for sup in prop.is_a:
            if isinstance(sup, owl.ObjectPropertyClass) and sup is not owl.ObjectProperty:
                tr.add(f"all X all Y ({em.relate(prop, x, y, ABOX_TIME)} -> "
                       f"{em.relate(sup, x, y, ABOX_TIME)}).")
            elif sup.__class__.__name__ == "Inverse":
                tr.skip(f"{prop.name} subPropertyOf inverse", "inverse-of axiom")
        if owl.TransitiveProperty in prop.is_a:
            tr.add(f"all X all Y all Z (({em.relate(prop, x, y, ABOX_TIME)} & "
                   f"{em.relate(prop, y, z, ABOX_TIME)}) -> "
                   f"{em.relate(prop, x, z, ABOX_TIME)}).")
        if owl.SymmetricProperty in prop.is_a:
            tr.add(f"all X all Y ({em.relate(prop, x, y, ABOX_TIME)} -> "
                   f"{em.relate(prop, y, x, ABOX_TIME)}).")


def _translate_individuals(tr, em, syms, world) -> None:
    import owlready2 as owl

    obj_props = list(world.object_properties())
    data_props = list(world.data_properties())
    if data_props:
        tr.skip(
            f"{len(data_props)} data properties",
            "data-property assertions are outside the whitelist (TR-2) and "
            "are not walked",
        )
    for ind in world.individuals():
        i = syms.ind(ind.iri)
        if tr.mode == "B":
            # Snapshot witness: every ABox individual exists at t_abox.
            tr.add(f"existsAt({i},{ABOX_TIME}).")
        for cexpr in ind.is_a:
            if cexpr is owl.Thing:
                continue
            try:
                if tr.mode == "A":
                    tr.add(f"{em.expr(i, cexpr, '')}.")
                else:
                    # Rigidity (TR-5): membership at every time of existence.
                    tr.add(f"all T (existsAt({i},T) -> "
                           f"{em.expr(i, cexpr, 'T')}).")
            except _Unsupported as e:
                tr.skip(f"{ind.name} type {cexpr!r}", str(e))
        # Deliberately not ind.get_properties(): owlready2 raises there when
        # a file references undeclared predicates (seen on live corpora).
        # Iterating the declared object properties is robust; annotations are
        # not logical content, and data-property assertions are reported once
        # below rather than walked.
        for prop in obj_props:
            try:
                values = prop[ind]
            except Exception:
                continue
            for value in values:
                if not isinstance(value, owl.Thing):
                    tr.skip(f"{ind.name}.{prop.name}", "non-individual value")
                    continue
                tr.add(f"{em.relate(prop, i, syms.ind(value.iri), ABOX_TIME)}.")


def translate_world(world, mode: str = "A") -> Translation:
    """Translate every whitelisted axiom reachable in ``world`` (TR-2)."""
    assert mode in ("A", "B")
    tr = Translation(mode=mode)
    declared = _declared_bfo_constants() if mode == "B" else set()
    syms = _Symbols(mode, declared)
    em = _Emitter(tr, syms)

    if mode == "B":
        # The shared snapshot time is itself a temporal region (the axioms'
        # self-instantiation convention for temporal regions).
        tr.add(f"instanceOf({ABOX_TIME},temporalRegion,{ABOX_TIME}).")

    _emit_bfo_skeleton(tr, em, syms)
    _translate_classes(tr, em, syms, world)
    _translate_disjoints(tr, em, syms, world)
    _translate_properties(tr, em, syms, world)
    _translate_individuals(tr, em, syms, world)
    tr.symbols = dict(syms.by_iri)
    return tr


def translate_file(owl_path: Path, mode: str = "A") -> Translation:
    """Load an OWL file read-only into a fresh world and translate it."""
    import owlready2 as owl

    world = owl.World()
    world.get_ontology(Path(owl_path).absolute().as_uri()).load()
    return translate_world(world, mode=mode)
