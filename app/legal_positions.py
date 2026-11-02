"""Legal positions (LOF ODP-003/004) in proposals of legal claims.

The second 2018/1725 draft put every "shall", "may" and "has the right to"
into an ODP-003 position, which is right, and then left the positions
unconnected: none of 266 position individuals was ``held by`` anyone,
``grounded in`` the act or the ``correlative of`` another position; 148
position classes said every such position is *realized* (every right to
withdraw consent is exercised, every duty to notify a breach performed),
which leaves no room for the breach the remedy end of the minimal legal
chain depends on; 61 said a duty inheres in a role (the controller role)
rather than in whoever bears it; and 166 generic duties of "the controller"
were single individuals, as if all controllers shared one duty particular.

Two passes, both on the proposal before the gate:

:func:`complete` (both fidelity modes; it adds what the kernel requires and
the claim's own act supplies, never content the text lacks):

* every new position is ``grounded in`` the act's legal content;
* a position that inheres in a holder is also ``held by`` it;
* a position from a remedy clause is also a remedial position (ODP-004).

:func:`check` / :func:`repair` (curated repairs, faithful records):

* ``realization-universal``: ``P subClassOf has_realization some X`` on a
  position becomes ``has_realization only X`` (if realized, then in X);
* ``role-inherence``: ``P subClassOf inheres_in some R`` with R a role
  becomes ``P held_by some H`` and ``P inheres_in some H`` with
  ``H subClassOf has_role some R``, H a material entity;
* ``unheld-position-individual``: a position individual with no holder
  becomes a class under its LOF position class, so it is the position every
  holder of the role has, not one particular shared by all of them.
"""
from __future__ import annotations

import re

from . import bfo_catalog

LOF = "https://w3id.org/lof/"
POSITION = "ODP003_0001000"
HELD_BY = "ODP003_0001100"
GROUNDED_IN = "ODP003_0001101"
REMEDIAL_POSITION = "ODP004_0001002"
HAS_REALIZATION = "BFO_0000054"
INHERES_IN = "BFO_0000197"
HAS_ROLE = "RO_0000087"
MATERIAL_ENTITY = "BFO_0000040"
_INHERES = {INHERES_IN, "RO_0000052"}
_SUBCLASS = {"rdfs:subClassOf", "subClassOf", "subclassof",
             "http://www.w3.org/2000/01/rdf-schema#subClassOf"}
_TYPE = {"rdf:type", "type", "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", "a"}
_RESTRICTION = re.compile(r"^\s*(\S+)\s+(some|only|value)\s+(\S+)\s*$")
CHECKS = {"realization-universal", "role-inherence", "unheld-position-individual"}

RULES = """LEGAL POSITION RULES (LOF ODP-003/004; they refine the kernel list above):
P-1 A position borne by every holder of a role ("the controller shall ...")
    is a CLASS under the LOF position class (duty, claim, liberty, power,
    ...), with `held by some <holder>`. Make it an individual only when the
    text names its holder (the EDPS, the Commission), and assert
    `held by` that individual.
P-2 A position is held by, and inheres in, a material entity: the person or
    body that bears the role, never the role itself. Write `held by some
    ControllerHolder` (a material entity that has the controller role), not
    `inheres in some Controller`.
P-3 A position may go unrealized: a duty can be breached and a right never
    exercised. Relate a position to the conduct it is about with
    `has realization` and "quantifier": "only", never "some".
P-4 When the text names the party a duty is owed to, emit the correlative
    claim (`correlative of`) held by that party; otherwise say so in
    `open_questions`.
P-5 A right or power to a remedy (complaint, judicial remedy, compensation,
    fines) is also a remedial position; relate it to the position whose
    breach it answers with `remedies` when the text names it.
"""


def _local(ref: str) -> str:
    s = (ref or "").strip()
    for sep in ("#", "/", ":"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


def _canon(p: str) -> str:
    from .relation_vocab import canonical_relation_id
    return canonical_relation_id(_local(p)) or _local(p)


def _is_lof_position_ref(ref: str) -> bool:
    return _local(ref).startswith("ODP003_00010") and _local(ref) != HELD_BY


def _entity(proposal, ref: str):
    for ent in getattr(proposal, "entities", []) or []:
        for cand in (ent.iri_suggestion, ent.existing_iri):
            if cand and _local(cand) == _local(ref):
                return ent
    return None


def _world_is_position(ref: str, mgr) -> bool:
    world = getattr(mgr, "world", None)
    if world is None or not ref:
        return False
    try:
        import owlready2
        from .ontology_manager import WORKING_IRI, _resolve_iri
        full = ref if ref.startswith("http") else _resolve_iri(ref, WORKING_IRI)
        obj = world[full]
        if obj is None:
            return False
        if isinstance(obj, owlready2.ThingClass):
            classes = [obj]
        else:
            classes = [t for t in getattr(obj, "is_a", []) if isinstance(t, owlready2.ThingClass)]
        for c in classes:
            for anc in c.ancestors():
                if _is_lof_position_ref(getattr(anc, "iri", "") or ""):
                    return True
    except Exception:
        return False
    return False


def is_position(ref: str, proposal, mgr=None, _seen=None) -> bool:
    """True when ``ref`` (a class or individual in the proposal or the
    ontology) is an LOF legal position or an instance of one."""
    if _is_lof_position_ref(ref):
        return True
    _seen = _seen or set()
    if _local(ref) in _seen:
        return False
    _seen.add(_local(ref))
    ent = _entity(proposal, ref)
    if ent is not None:
        if ent.parent_class and is_position(ent.parent_class, proposal, mgr, _seen):
            return True
        for rel in getattr(proposal, "relations", []) or []:
            if _local(rel.s) == _local(ref) and (rel.p or "").strip() in _SUBCLASS | _TYPE \
                    and not _RESTRICTION.match(rel.o or "") \
                    and is_position(rel.o, proposal, mgr, _seen):
                return True
        if not ent.is_new and ent.existing_iri:
            return _world_is_position(ent.existing_iri, mgr)
        return False
    return _world_is_position(ref, mgr)


def _category(ref: str, proposal, mgr):
    from .legal_drafting import _bfo_category
    return _bfo_category(ref, proposal, mgr)


def _has(proposal, s: str, p: str, o: str) -> bool:
    for rel in getattr(proposal, "relations", []) or []:
        if _local(rel.s) == _local(s) and _canon(rel.p) == _canon(p) and _local(rel.o) == _local(o):
            return True
        m = _RESTRICTION.match(rel.o or "") if (rel.p or "").strip() in _SUBCLASS else None
        if m and _local(rel.s) == _local(s) and _canon(m.group(1)) == _canon(p) \
                and _local(m.group(3)) == _local(o):
            return True
    return False


def _edges(proposal):
    """(index, subject, canonical property, quantifier or None, object) for
    every object-property relation: restriction-form subClassOf edges,
    class-level relations with a quantifier, and individual-level ones
    (quantifier None)."""
    out = []
    for i, rel in enumerate(getattr(proposal, "relations", []) or []):
        p = (rel.p or "").strip()
        if p in _SUBCLASS or p in _TYPE:
            # "x rdf:type (p some F)" on an individual reads like a
            # class-level restriction for these checks.
            m = _RESTRICTION.match(rel.o or "")
            if m:
                out.append((i, rel.s, _canon(m.group(1)), m.group(2), m.group(3)))
        elif p.startswith("owl:") or p.startswith("rdfs:"):
            continue
        else:
            out.append((i, rel.s, _canon(p), getattr(rel, "quantifier", None), rel.o))
    return out


def _new_positions(proposal, mgr):
    return [e for e in getattr(proposal, "entities", []) or []
            if e.is_new and not getattr(e, "illustrative", False)
            and is_position(e.iri_suggestion or "", proposal, mgr)]


# ------------------------------------------------------------- complete

def complete(proposal, claim: dict, mgr=None) -> list[str]:
    """Add the links ODP-003/004 require that the claim itself supplies.
    Returns notes, one per added relation."""
    from .schema import Relation
    from .legal_kernel import act_iris

    notes: list[str] = []
    act_id = claim.get("act_id") or ""
    content = act_iris(act_id)["content"] if act_id else None
    add = []
    positions = _new_positions(proposal, mgr)
    pos_names = {_local(e.iri_suggestion or "") for e in positions}
    for ent in positions:
        s = ent.iri_suggestion
        cls = ent.kind == "class"
        if content and not _has(proposal, s, "lof:" + GROUNDED_IN, content):
            add.append(Relation(s=s, p="lof:" + GROUNDED_IN, o=content,
                                quantifier="value" if cls else None,
                                rationale="ODP-003: a legal position is grounded in the "
                                          "legal content of the act it comes from"))
            notes.append(f"positions: {_local(s)} grounded in {act_id}")
        if (claim.get("clause_type") or "") == "remedy" and \
                not is_position_of(s, REMEDIAL_POSITION, proposal):
            add.append(Relation(s=s, p="rdfs:subClassOf" if cls else "rdf:type",
                                o="lof:" + REMEDIAL_POSITION,
                                rationale="ODP-004: a position from a remedy clause is "
                                          "a remedial position"))
            notes.append(f"positions: {_local(s)} is a remedial position")
    for i, s, p, q, o in _edges(proposal):
        if _local(s) not in pos_names or p not in _INHERES:
            continue
        _, cat = _category(o, proposal, mgr)
        holder_is_ic = bool(cat) and (cat == bfo_catalog.INDEPENDENT_CONTINUANT or
                                      bfo_catalog.is_descendant_of(cat, bfo_catalog.INDEPENDENT_CONTINUANT))
        # Individual-level (q None) or hasValue: the named holder. Class
        # level: only a material-entity filler; a role filler is a
        # role-inherence finding, repaired separately.
        if q in (None, "value") or (q == "some" and holder_is_ic):
            if not _has(proposal, s, "lof:" + HELD_BY, o):
                add.append(Relation(s=s, p="lof:" + HELD_BY, o=o, quantifier=q,
                                    rationale="ODP-003: the bearer of a position is its holder"))
                notes.append(f"positions: {_local(s)} held by {_local(o)}")
    proposal.relations = list(proposal.relations or []) + add
    return notes


def is_position_of(ref: str, lof_cls: str, proposal) -> bool:
    ent = _entity(proposal, ref)
    if ent is not None and ent.parent_class and _local(ent.parent_class) == lof_cls:
        return True
    return any(_local(r.s) == _local(ref) and _local(r.o) == lof_cls
               for r in getattr(proposal, "relations", []) or [])


# --------------------------------------------------------- check / repair

def check(proposal, claim: dict, mgr=None) -> list[dict]:
    findings: list[dict] = []
    for i, s, p, q, o in _edges(proposal):
        if not is_position(s, proposal, mgr):
            continue
        if p == HAS_REALIZATION and q == "some":
            findings.append({
                "check": "realization-universal", "relation_index": i, "subject": s,
                "filler": o,
                "detail": f"every {_local(s)} would be realized in some {_local(o)}; a "
                          f"position can go unrealized (breach, a right never exercised)"})
        elif p in _INHERES | {HELD_BY} and q == "some":
            _, cat = _category(o, proposal, mgr)
            if cat and (cat == bfo_catalog.ROLE or bfo_catalog.is_descendant_of(cat, bfo_catalog.ROLE)):
                findings.append({
                    "check": "role-inherence", "relation_index": i, "subject": s,
                    "filler": o,
                    "detail": f"{_local(s)} would inhere in the role {_local(o)}; a position "
                              f"inheres in the person or body bearing the role"})
    for ent in _new_positions(proposal, mgr):
        if ent.kind != "individual":
            continue
        s = ent.iri_suggestion
        held = any(_local(es) == _local(s) and ep in _INHERES | {HELD_BY} and eq is None
                   for _, es, ep, eq, _o in _edges(proposal))
        if not held:
            findings.append({
                "check": "unheld-position-individual", "relation_index": None,
                "subject": s,
                "detail": f"{_local(s)} is one position individual with no holder; a "
                          f"position every holder of a role has is a class"})
    return findings


def _is_individual(ref: str, proposal, mgr) -> bool:
    ent = _entity(proposal, ref or "")
    if ent is not None:
        return ent.kind == "individual"
    world = getattr(mgr, "world", None)
    if world is None or not ref:
        return False
    try:
        import owlready2
        from .ontology_manager import WORKING_IRI, _resolve_iri
        obj = world[ref if ref.startswith("http") else _resolve_iri(ref, WORKING_IRI)]
        return obj is not None and not isinstance(obj, (owlready2.ThingClass,
                                                       owlready2.PropertyClass))
    except Exception:
        return False


def _holder_entity(role_ref: str, proposal, mgr):
    """The material-entity class that bears ``role_ref``: reused when the
    proposal or ontology has it, else a new entity plus its has_role edge."""
    from .schema import Entity, Relation

    name = f"{_local(role_ref)}Holder"
    ref = f"working:{name}"
    if _entity(proposal, ref) is not None:
        return ref, []
    existing = None
    world = getattr(mgr, "world", None)
    if world is not None:
        try:
            from .ontology_manager import WORKING_IRI, _resolve_iri
            existing = world[_resolve_iri(ref, WORKING_IRI)]
        except Exception:
            existing = None
    role_ent = _entity(proposal, role_ref)
    role_label = (role_ent.label if role_ent is not None else _local(role_ref))
    proposal.entities = list(proposal.entities or []) + [Entity(
        label=f"{role_label} holder", iri_suggestion=ref, bfo_type=MATERIAL_ENTITY,
        bfo_label="material entity", kind="class", parent_class=None,
        rationale="P-2: the person or body that bears the role holds the position",
        is_new=existing is None,
        existing_iri=getattr(existing, "iri", None),
        definition=None if existing is not None else
        f"A material entity that has the role {role_label}.")]
    if existing is not None:
        return ref, []
    return ref, [Relation(s=ref, p="rdfs:subClassOf", o=f"obo:{HAS_ROLE} some {role_ref}",
                          rationale="P-2: a holder bears the role")]


def repair(proposal, findings: list[dict], mgr=None) -> list[str]:
    """Curated repairs. Unheld individuals become classes FIRST; the
    realization and role checks are then re-run on the result, so a
    converted position's restrictions (including ones it had as rdf:type
    expressions) are repaired too."""
    notes = _convert_unheld(proposal, [f for f in findings
                                       if f["check"] == "unheld-position-individual"], mgr)
    if notes:
        findings = [f for f in check(proposal, {}, mgr)
                    if f["check"] != "unheld-position-individual"]
    return notes + _repair_restrictions(proposal, findings, mgr)


def _repair_restrictions(proposal, findings: list[dict], mgr=None) -> list[str]:
    from .schema import Relation

    notes: list[str] = []
    rels = list(getattr(proposal, "relations", []) or [])
    drop: set[int] = set()
    add: list = []
    for f in findings:
        i = f.get("relation_index")
        if f["check"] == "realization-universal" and i is not None and i not in drop:
            drop.add(i)
            add.append(Relation(s=rels[i].s, p="obo:" + HAS_REALIZATION, o=f["filler"],
                                quantifier="only",
                                rationale="P-3: if realized, then in this conduct"))
            notes.append(f"realization-universal: {_local(f['subject'])} has_realization "
                         f"some -> only {_local(f['filler'])}")
        elif f["check"] == "role-inherence" and i is not None and i not in drop:
            drop.add(i)
            holder, extra = _holder_entity(f["filler"], proposal, mgr)
            add.extend(extra)
            s = rels[i].s
            add.append(Relation(s=s, p="lof:" + HELD_BY, o=holder, quantifier="some",
                                rationale="P-2: held by the bearer of the role"))
            add.append(Relation(s=s, p="obo:" + INHERES_IN, o=holder, quantifier="some",
                                rationale="P-2: inheres in the bearer of the role"))
            notes.append(f"role-inherence: {_local(s)} now held by some {_local(holder)} "
                         f"(a bearer of {_local(f['filler'])})")
    proposal.relations = [r for k, r in enumerate(rels) if k not in drop] + add
    return notes


def _convert_unheld(proposal, findings: list[dict], mgr=None) -> list[str]:
    from .schema import Relation

    notes: list[str] = []
    new_rels = list(getattr(proposal, "relations", []) or [])
    for f in findings:
        ent = _entity(proposal, f["subject"])
        if ent is None:
            continue
        s = ent.iri_suggestion
        ent.kind = "class"
        kept = []
        for r in new_rels:
            if _local(r.s) != _local(s):
                if _local(r.o or "") == _local(s) and (r.p or "").strip() not in _SUBCLASS:
                    # Another individual pointed at the old particular: keep
                    # it as a question, not an assertion about a class.
                    proposal.open_questions = list(proposal.open_questions or []) + [
                        f"{_local(r.s)} {_local(r.p)} {_local(s)}: {_local(s)} is now a class "
                        f"of positions; which one is meant?"]
                    continue
                kept.append(r)
                continue
            p = (r.p or "").strip()
            if p in _TYPE:
                if _is_lof_position_ref(r.o) and not ent.parent_class:
                    ent.parent_class = r.o
                kept.append(Relation(s=r.s, p="rdfs:subClassOf", o=r.o, rationale=r.rationale))
            elif p in _SUBCLASS:
                kept.append(r)
            elif getattr(r, "quantifier", None) is None:
                if _is_individual(r.o, proposal, mgr) and _canon(p) == HAS_REALIZATION:
                    # "every such duty is realized in this one process" is
                    # not what the text says: keep it as a possibility.
                    ent.possible_relations = list(ent.possible_relations or []) + [
                        f"{HAS_REALIZATION} value {_local(r.o)}"]
                    continue
                if _is_individual(r.o, proposal, mgr):
                    q = "value"
                elif _canon(p) == HAS_REALIZATION:
                    q = "only"
                else:
                    q = "some"
                kept.append(Relation(s=r.s, p=r.p, o=r.o, quantifier=q,
                                     rationale=r.rationale))
            else:
                kept.append(r)
        new_rels = kept
        notes.append(f"unheld-position-individual: {_local(s)} is now a class of positions")
    proposal.relations = new_rels
    return notes
