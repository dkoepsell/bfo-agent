"""Legal-genre drafting discipline (SPEC-legal-drafting.md sections 3.3 and 4).

Everything here is inert for philosophy-genre claims: a claim is legal only
when the extractor's legal profile produced it (``claim["genre"]`` is one of
``legal_source.LEGAL_GENRES``). For those claims the feed loop

1. shows the proposer the verbatim quote, locator, clause type, quantifier and
   modality (:func:`proposer_utterance`) plus rules Q1-Q5 (:data:`LEGAL_RULES`);
2. stamps provenance from the claim, not the paraphrase
   (:func:`stamp_provenance`); and
3. runs the five legal checks before the coherence gate (:func:`check`).
   In curated mode each finding is repaired in the proposal (or, for an
   unverified quote or unscoped definition, the claim is rejected); in
   faithful mode the proposal is committed as asserted and every finding is
   written to ``legal_findings.jsonl`` beside the ontology as evidence.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import bfo_catalog, relation_vocab
from .legal_source import is_legal_genre

BEARER_OF = "BFO_0000196"
INHERES_IN = "BFO_0000197"
# Bearer-side relations to a realizable or quality (BFO 2020 and RO aliases).
_BEARER_SIDE = {BEARER_OF, "RO_0000053", "RO_0000087", "RO_0000091", "RO_0000085"}
_INHERES = {INHERES_IN, "RO_0000052"}
_SUBCLASS = {"rdfs:subClassOf", "subClassOf", "subclassof",
             "http://www.w3.org/2000/01/rdf-schema#subClassOf"}
_RESTRICTION = re.compile(r"^\s*(\S+)\s+(some|only|value)\s+(\S+)\s*$")

UNIVERSAL_QUANTIFIERS = {"every", "definitional"}
MODAL_QUANTIFIERS = {"can"}

LEGAL_RULES = """LEGAL-SOURCE RULES (this claim comes from legal text; they override rule 4's
"mandatory relations" and rule 10's bearer-side wording):
L-0 Emit only the relations the SOURCE QUOTE states. When a kind has no stated
    relation, say so in `open_questions`; an isolated class is a reported gap,
    never a reason to invent an axiom.
Q1  A class-level restriction `C subClassOf p some D` (or a class-to-class
    relation with "quantifier": "some"/"only") ONLY when the claim's QUANTIFIER
    is "every" or "definitional". For "can", "some", a permission or a
    condition, emit NO class-level axiom: put the possibility in the subject
    entity's `possible_relations` list as "p some D", or, if the text names a
    particular, assert it between individuals.
Q2  For roles, dispositions, functions and qualities put the restriction on
    the DEPENDENT side (`DataProtectionOfficer subClassOf BFO_0000197 some
    NaturalPerson`), never on the bearer (`NaturalPerson subClassOf
    BFO_0000196 some DataProtectionOfficer`) unless the text says every such
    bearer has it.
Q3  A specifically dependent continuant class gets AT MOST ONE `inheres in`
    restriction: a quality or role has one bearer. If the text names
    alternative bearers, use the most specific class covering them and list
    the alternatives in `open_questions`.
Q4  Carry a definition's relative clauses as relations ("determines the
    purposes and means OF THE PROCESSING OF PERSONAL DATA"); a dangling object
    is an `open_question`, not a dropped phrase.
Q5  Generically dependent continuants (information, data, content) do not
    participate in processes directly: link the process to the bearer of a
    concretization (a record, a storage medium) or use `is concretized by`.
L-6 `source_span` must be a verbatim excerpt of the SOURCE QUOTE. Never
    paraphrase it.
L-7 A legal definition is scoped to its act: put the act's definiens in
    `definition` verbatim where the text gives one.
"""


def is_legal_claim(claim: dict | None) -> bool:
    return bool(claim) and is_legal_genre(claim.get("genre"))


def proposer_utterance(claim: dict) -> str:
    """The utterance the proposer sees. Philosophy claims: the claim text,
    unchanged (prompt cache and behaviour preserved). Legal claims: the
    restatement plus the verbatim quote and its metadata (spec 3.3)."""
    text = claim.get("claim") or ""
    if not is_legal_claim(claim):
        return text
    act = claim.get("act_title") or claim.get("act_id") or "unknown act"
    lines = [
        text,
        "",
        f"SOURCE QUOTE ({act}, {claim.get('locator') or 'unknown'}), verbatim:",
        f"\"{claim.get('source_quote') or ''}\"",
        f"CLAUSE TYPE: {claim.get('clause_type') or 'other'}; "
        f"QUANTIFIER: {claim.get('quantifier') or 'some'}; "
        f"MODALITY: {claim.get('modality') or 'none'}"
        + (f"; SUBJECT: {claim['subject']}" if claim.get("subject") else ""),
    ]
    return "\n".join(lines)


def provenance_text(claim: dict) -> str:
    """``source_text`` for provenance records: the verbatim quote for legal
    claims (spec principle 1), the claim text otherwise."""
    if is_legal_claim(claim) and claim.get("source_quote"):
        return claim["source_quote"]
    return claim.get("claim") or ""


def stamp_provenance(proposal, claim: dict) -> None:
    """Make every proposed entity's provenance the verbatim text (spec 3.3).

    An entity's own ``source_span`` is kept only when it is a verbatim excerpt
    of the claim's quote; otherwise it is replaced by the quote. The locator,
    act and paraphrase go in their own fields."""
    if not is_legal_claim(claim):
        return
    from .legal_source import quote_is_verbatim

    quote = claim.get("source_quote") or ""
    act = claim.get("act_id") or claim.get("act_title") or None
    for ent in getattr(proposal, "entities", []) or []:
        span = (getattr(ent, "source_span", None) or "").strip()
        if not span or not quote_is_verbatim(span, quote):
            ent.source_span = quote or None
        ent.source_locator = claim.get("locator") or "unknown"
        ent.source_act = act
        ent.claim_text = claim.get("claim") or None


# ------------------------------------------------------------- the checks

def _local(ref: str) -> str:
    s = (ref or "").strip()
    for sep in ("#", "/", ":"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


def _canon(p: str) -> str:
    return relation_vocab.canonical_relation_id(_local(p)) or _local(p)


def _bfo_category(ref: str, proposal, mgr) -> tuple[str | None, str | None]:
    """(kind, BFO fragment) for a reference: from the proposal's entities
    first, then the live ontology. Unknown -> (None, None)."""
    name = _local(ref)
    if name.startswith("BFO_"):
        return "class", name
    for ent in getattr(proposal, "entities", []) or []:
        for cand in (ent.iri_suggestion, ent.existing_iri):
            if cand and _local(cand) == name:
                return ent.kind, bfo_catalog.normalize_fragment(ent.bfo_type or "") or None
    world = getattr(mgr, "world", None)
    if world is None:
        return None, None
    try:
        from .ontology_manager import WORKING_IRI, _resolve_iri
        full = ref if ref.startswith("http") else _resolve_iri(ref, WORKING_IRI)
        obj = world[full]
        if obj is None:
            return None, None
        import owlready2
        if isinstance(obj, owlready2.ThingClass):
            for anc in obj.ancestors():
                frag = _local(getattr(anc, "iri", "") or "")
                if frag.startswith("BFO_") and frag not in ("BFO_0000001",
                                                             "BFO_0000002"):
                    best = frag
                    # Prefer the most specific BFO ancestor.
                    for other in obj.ancestors():
                        of = _local(getattr(other, "iri", "") or "")
                        if of.startswith("BFO_") and of != best and \
                                bfo_catalog.is_descendant_of(of, best):
                            best = of
                    return "class", best
            return "class", None
        return "individual", None
    except Exception:
        return None, None


def _is(frag: str | None, ancestor: str) -> bool:
    return bool(frag) and (frag == ancestor or bfo_catalog.is_descendant_of(frag, ancestor))


def _class_level_edges(proposal) -> list[tuple[int, str, str, str, str]]:
    """(index, subject, canonical property, quantifier, filler) for every
    class-level object-property axiom in the proposal: restriction-form
    subClassOf edges and class-to-class relations with a quantifier."""
    out = []
    for i, rel in enumerate(getattr(proposal, "relations", []) or []):
        p = (rel.p or "").strip()
        if p in _SUBCLASS:
            m = _RESTRICTION.match(rel.o or "")
            if m:
                out.append((i, rel.s, _canon(m.group(1)), m.group(2), m.group(3)))
        elif getattr(rel, "quantifier", None) in ("some", "only"):
            out.append((i, rel.s, _canon(p), rel.quantifier, rel.o))
    return out


def _existing_inheres_fillers(subject: str, mgr) -> list[str]:
    """Fillers of ``subject subClassOf inheres_in some F`` already in the
    ontology (best-effort; [] on any error)."""
    world = getattr(mgr, "world", None)
    if world is None:
        return []
    try:
        import owlready2
        from .ontology_manager import WORKING_IRI, _resolve_iri
        full = subject if subject.startswith("http") else _resolve_iri(subject, WORKING_IRI)
        cls = world[full]
        if not isinstance(cls, owlready2.ThingClass):
            return []
        out = []
        for sup in cls.is_a:
            prop = getattr(sup, "property", None)
            if prop is not None and _canon(getattr(prop, "iri", "")) in _INHERES:
                val = getattr(sup, "value", None)
                if val is not None and hasattr(val, "iri"):
                    out.append(val.iri)
        return out
    except Exception:
        return []


def check(proposal, claim: dict, mgr=None) -> list[dict]:
    """The five checks of spec 4.2. Returns findings, each
    ``{check, relation_index, subject, detail}``; [] for non-legal claims."""
    if not is_legal_claim(claim):
        return []
    findings: list[dict] = []
    quant = (claim.get("quantifier") or "").lower()
    modality = (claim.get("modality") or "").lower()
    clause = (claim.get("clause_type") or "").lower()

    if not claim.get("quote_verified"):
        findings.append({"check": "paraphrase-provenance", "relation_index": None,
                         "subject": claim.get("subject") or "",
                         "detail": "source_quote was not verified verbatim against the source"})
    if clause == "definition" and not claim.get("act_id"):
        findings.append({"check": "unscoped-definition", "relation_index": None,
                         "subject": claim.get("subject") or "",
                         "detail": "a legal definition with no act_id"})

    modal = (quant in MODAL_QUANTIFIERS or modality == "permission"
             or clause == "condition")
    edges = _class_level_edges(proposal)
    for i, s, p, q, o in edges:
        if modal:
            findings.append({
                "check": "modal-universal", "relation_index": i, "subject": s,
                "detail": f"class-level '{s} {p} {q} {o}' from a claim with "
                          f"quantifier={quant or '?'}, modality={modality or '?'}, "
                          f"clause_type={clause or '?'}"})
            continue
        if p in _BEARER_SIDE and quant not in UNIVERSAL_QUANTIFIERS:
            _, s_cat = _bfo_category(s, proposal, mgr)
            _, o_cat = _bfo_category(o, proposal, mgr)
            if _is(s_cat, bfo_catalog.INDEPENDENT_CONTINUANT) and \
                    (o_cat is None or _is(o_cat, bfo_catalog.SDC)):
                findings.append({
                    "check": "bearer-universal", "relation_index": i, "subject": s,
                    "filler": o,
                    "detail": f"every {s} is said to bear some {o}, but the claim's "
                              f"quantifier is {quant or 'unstated'}, not 'every'"})

    # Q3 / multi-bearer: count inheres-in restrictions per SDC subject, in the
    # proposal plus the ontology. Bearer-universal repairs add one each, so
    # count those too.
    inheres: dict[str, list[tuple[int | None, str]]] = {}
    for i, s, p, q, o in edges:
        if p in _INHERES and q == "some":
            inheres.setdefault(_local(s), []).append((i, o))
    for f in findings:
        if f["check"] == "bearer-universal":
            inheres.setdefault(_local(f["filler"]), []).append((None, f["subject"]))
    for subj, items in inheres.items():
        _, cat = _bfo_category(subj, proposal, mgr)
        if cat is not None and not _is(cat, bfo_catalog.SDC):
            continue
        existing = [e for e in _existing_inheres_fillers(subj, mgr)
                    if _local(e) not in {_local(o) for _, o in items}]
        bearers = [o for _, o in items] + existing
        if len(bearers) > 1:
            findings.append({
                "check": "multi-bearer", "relation_index": None, "subject": subj,
                "bearers": [_local(b) for b in bearers],
                "detail": f"{subj} would inhere in {len(bearers)} bearers "
                          f"({', '.join(_local(b) for b in bearers)}); a specifically "
                          f"dependent continuant has one bearer"})
    return findings


# ------------------------------------------------------------ the policy

REJECTING_CHECKS = {"paraphrase-provenance"}
CURATED_REJECTING_CHECKS = {"paraphrase-provenance", "unscoped-definition"}


def rejects(findings: list[dict], faithful: bool) -> list[dict]:
    """Findings that reject the whole claim in this mode (spec 4.2 table)."""
    which = REJECTING_CHECKS if faithful else CURATED_REJECTING_CHECKS
    return [f for f in findings if f["check"] in which]


def _subject_entity(proposal, subject: str):
    for ent in getattr(proposal, "entities", []) or []:
        for cand in (ent.iri_suggestion, ent.existing_iri):
            if cand and _local(cand) == _local(subject):
                return ent
    return None


def repair(proposal, findings: list[dict], mgr=None) -> list[str]:
    """Curated mode: rewrite the proposal so it says only what the text
    supports. Returns human-readable notes, one per change.

    * modal-universal: the class-level axiom is removed and kept as a
      possibility (``possible_relations``) on its subject.
    * bearer-universal: ``C bearer_of some R`` becomes ``R inheres_in some C``.
    * multi-bearer: the first bearer's restriction is kept, later ones are
      removed and listed in ``open_questions`` for a person to resolve.
    """
    from .schema import Relation

    notes: list[str] = []
    rels = list(getattr(proposal, "relations", []) or [])
    drop: set[int] = set()
    add: list = []

    for f in findings:
        i = f.get("relation_index")
        if f["check"] == "modal-universal" and i is not None and i not in drop:
            rel = rels[i]
            m = _RESTRICTION.match(rel.o or "") if (rel.p or "").strip() in _SUBCLASS else None
            possible = (f"{m.group(1)} {m.group(2)} {m.group(3)}" if m
                        else f"{rel.p} {rel.quantifier or 'some'} {rel.o}")
            ent = _subject_entity(proposal, rel.s)
            if ent is not None:
                ent.possible_relations = list(ent.possible_relations or []) + [possible]
            drop.add(i)
            notes.append(f"modal-universal: dropped class-level '{rel.s} {possible}'; "
                         f"kept as a possibility")
        elif f["check"] == "bearer-universal" and i is not None and i not in drop:
            rel = rels[i]
            drop.add(i)
            add.append(Relation(s=f["filler"], p="rdfs:subClassOf",
                                o=f"bfo:{INHERES_IN} some {rel.s}",
                                rationale="dependent-side form of a bearer-side "
                                          "relation (SPEC-legal-drafting Q2)"))
            notes.append(f"bearer-universal: '{rel.s} bearer_of some {f['filler']}' "
                         f"rewritten as '{f['filler']} inheres_in some {rel.s}'")

    new_rels = [r for k, r in enumerate(rels) if k not in drop] + add

    for f in findings:
        if f["check"] != "multi-bearer":
            continue
        subj = _local(f["subject"])
        # A bearer already in the ontology wins; every new one is removed.
        seen = bool(_existing_inheres_fillers(f["subject"], mgr))
        kept = []
        removed = []
        for r in new_rels:
            m = _RESTRICTION.match(r.o or "") if (r.p or "").strip() in _SUBCLASS else None
            is_inh = (_local(r.s) == subj and m and _canon(m.group(1)) in _INHERES) or \
                (_local(r.s) == subj and getattr(r, "quantifier", None) == "some"
                 and _canon(r.p or "") in _INHERES)
            if is_inh:
                if seen:
                    removed.append(m.group(3) if m else r.o)
                    continue
                seen = True
            kept.append(r)
        new_rels = kept
        if removed:
            proposal.open_questions = list(proposal.open_questions or []) + [
                f"multi-bearer: {subj} was also said to inhere in "
                f"{', '.join(_local(x) for x in removed)}; one bearer kept. Which "
                f"bearer, or which class covering all of them, does the text intend?"]
            notes.append(f"multi-bearer: kept one inheres_in restriction on {subj}, "
                         f"removed {len(removed)}")

    proposal.relations = new_rels
    return notes


def record_findings(working_path, claim: dict, findings: list[dict],
                    mode: str, action: str, job_id: str | None = None) -> None:
    """Append findings to ``legal_findings.jsonl`` beside the ontology
    (the faithful-mode ledger of spec 4.2; curated repairs are logged too).
    Never raises."""
    if not findings:
        return
    try:
        path = Path(working_path).resolve().parent / "legal_findings.jsonl"
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "claim_id": claim.get("id"),
            "act_id": claim.get("act_id"),
            "locator": claim.get("locator"),
            "source_quote": claim.get("source_quote"),
            "quantifier": claim.get("quantifier"),
            "modality": claim.get("modality"),
            "clause_type": claim.get("clause_type"),
            "fidelity": mode,
            "action": action,
            "findings": findings,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
