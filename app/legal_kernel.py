"""The LOF kernel, legal acts in the graph, and scoped definitions
(SPEC-legal-drafting.md sections 2.2, 5 and 6.1; layers L4 and L5).

* :func:`merge_lof` copies the four vendored Legal Ontology Foundry pattern
  releases (``ontology/kernels/lof/``, CC BY 4.0, v0.1.0) into a working
  ontology once, so the proposer can specialize ``legal role``, ``duty`` and
  so on instead of minting them.
* :func:`ensure_act` puts each source act in the graph before its claims:
  legal content, the enactment that establishes it, and the legal document
  that carries it (ODP-001).
* :func:`record_definition` turns a committed legal definition into a
  definition individual that is part of the act's content, writes the
  verbatim definiens and its source on the defined class, and, when a second
  act defines the same term differently, creates one scoped sense per act
  under the shared class. An earlier definition is never overwritten.

All writes go through the manager's owlready2 world as rdflib triples (the
pattern of ``content_quality.annotate_new_class``) and are followed by a save.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

LOF_DIR = Path(__file__).resolve().parent.parent / "ontology" / "kernels" / "lof"
LOF_NS = "https://w3id.org/lof/"
LOF_VERSION = "0.1.0"
LOF_FILES = [f"lof-odp-00{n}-base.owl" for n in (1, 2, 3, 4)]

OBO = "http://purl.obolibrary.org/obo/"
IAO_DEFINITION = OBO + "IAO_0000115"
OWL_ANNOTATION = "http://www.w3.org/2002/07/owl#AnnotationProperty"
IAO_DEFINITION_SOURCE = OBO + "IAO_0000119"
IS_CARRIER_OF = OBO + "BFO_0000101"
CONTINUANT_PART_OF = OBO + "BFO_0000176"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
OWL_INDIVIDUAL = "http://www.w3.org/2002/07/owl#NamedIndividual"
DCT = "http://purl.org/dc/terms/"

# The terms the proposer is pointed at (spec 6.1), with a short gloss.
LEGAL_ACT = LOF_NS + "ODP001_0001000"
LEGAL_CONTENT = LOF_NS + "ODP001_0001001"
LEGAL_DOCUMENT = LOF_NS + "ODP001_0001002"
ENACTMENT = LOF_NS + "ODP001_0001003"
ESTABLISHES = LOF_NS + "ODP001_0001100"
LEGAL_ROLE = LOF_NS + "ODP002_0001000"

LOF_TERMS = [
    ("lof:ODP001_0001000", "legal act", "a process that creates, changes or ends legal content (an occurrent)"),
    ("lof:ODP001_0001001", "legal content", "the content of an act: a generically dependent continuant"),
    ("lof:ODP001_0001002", "legal document", "a material entity carrying legal content (a published text)"),
    ("lof:ODP001_0001003", "enactment", "the legal act that establishes an instrument's content"),
    ("lof:ODP001_0001004", "contract formation", "a legal act forming a contract"),
    ("lof:ODP002_0001000", "legal role", "a role borne by a party in virtue of legal content: controller, officer, authority"),
    ("lof:ODP002_0001001", "conferred legal role", "a legal role conferred by a conferral act (designated, appointed)"),
    ("lof:ODP002_0001002", "role conferral act", "the act that confers a legal role (designation, appointment)"),
    ("lof:ODP002_0001003", "role termination act", "the act that ends a legal role"),
    ("lof:ODP003_0001000", "legal position", "a Hohfeldian position: a specifically dependent continuant"),
    ("lof:ODP003_0001001", "claim", "a claim-right; correlative of a duty"),
    ("lof:ODP003_0001002", "duty", "what a party shall do or refrain from ('shall', 'shall not')"),
    ("lof:ODP003_0001003", "liberty", "a privilege ('may'); correlative of a no-claim"),
    ("lof:ODP003_0001004", "no-claim", "correlative of a liberty"),
    ("lof:ODP003_0001005", "power", "the ability to change legal positions ('shall have the power to')"),
    ("lof:ODP003_0001006", "liability", "correlative of a power"),
    ("lof:ODP003_0001007", "immunity", "protection from a power"),
    ("lof:ODP003_0001008", "disability", "correlative of an immunity"),
    ("lof:ODP004_0001000", "norm-establishing power", "a power whose exercise establishes norms"),
    ("lof:ODP004_0001001", "triggering occurrence", "the fact or event a norm is conditioned on ('where', 'in the case of')"),
    ("lof:ODP004_0001002", "remedial position", "a position providing a remedy (complaint, compensation, fine)"),
    ("lof:ODP004_0001003", "fully grounded legal position", "a position with its full chain"),
]
LOF_PROPERTIES = [
    ("lof:ODP001_0001100", "establishes", "legal act -> legal content"),
    ("lof:ODP002_0001101", "conferred by", "conferred legal role -> legal content"),
    ("lof:ODP003_0001102", "correlative of", "legal position -> legal position"),
    ("lof:ODP004_0001102", "triggered by", "legal position -> occurrent"),
    ("lof:ODP004_0001103", "directed at", "legal position -> its addressee"),
    ("lof:ODP004_0001104", "remedies", "remedial position -> legal position"),
    ("lof:ODP004_0001105", "remedy provided by", "legal position -> legal content"),
]


def kernel_rules() -> str:
    """Kernel companion list for the legal proposer rules (spec 6.1)."""
    lines = ["LOF KERNEL (Legal Ontology Foundry patterns, already in this ontology).",
             "Specialize these before minting: a designated or appointed party's",
             "function is a subclass of legal role; 'shall' is a duty, 'may' a liberty,",
             "'has the right to' a claim, 'shall have the power to' a power, each with",
             "its correlative; triggers, generating acts and remedies use ODP-004.",
             "Use the lof: prefix exactly as shown:"]
    for iri, label, gloss in LOF_TERMS:
        lines.append(f"  {iri}  {label}: {gloss}")
    for iri, label, gloss in LOF_PROPERTIES:
        lines.append(f"  {iri}  {label} ({gloss})")
    return "\n".join(lines) + "\n"


def candidates() -> list[dict]:
    """LOF classes as proposer reuse candidates (``canonical`` so the reuse
    ranking prefers them)."""
    return [{"iri": LOF_NS + iri.split(":", 1)[1], "label": label,
             "parents": [], "canonical": True}
            for iri, label, _ in LOF_TERMS]


# ------------------------------------------------------------ the merge

def has_lof(mgr) -> bool:
    try:
        return mgr.world[LEGAL_ROLE] is not None
    except Exception:
        return False


def lof_graph():
    """The four vendored LOF base files as one rdflib graph, ontology
    headers removed (their imports point at the network)."""
    import rdflib
    from rdflib.namespace import OWL, RDF

    g = rdflib.Graph()
    for name in LOF_FILES:
        g.parse(LOF_DIR / name)
    for ont in list(g.subjects(RDF.type, OWL.Ontology)):
        g.remove((ont, None, None))
    return g


def merge_lof(mgr) -> bool:
    """Copy the LOF kernel into ``mgr``'s working ontology. Idempotent.
    Returns True when it merged, False when already present."""
    if has_lof(mgr):
        return False
    import rdflib

    mgr.save()
    path = Path(mgr.working_path)
    g = rdflib.Graph()
    g.parse(path)
    for t in lof_graph():
        g.add(t)
    g.serialize(path, format="xml")
    mgr._load()
    return True


# ------------------------------------------------------------ writing

def _graph(mgr):
    return mgr.world.as_rdflib_graph()


def _node(o):
    """The rdflib node for an object value. rdflib's ``Literal`` and
    ``URIRef`` are both ``str`` subclasses, so they must be recognised
    before a plain ``str`` (an IRI) is wrapped: testing ``isinstance(o, str)``
    alone turned every literal (definiens, scope, label, source) into an
    IRI made of its text."""
    from rdflib.term import Node, URIRef
    if o is None or isinstance(o, Node):
        return o
    return URIRef(o)


def _put(mgr, s, p, o) -> None:
    from rdflib import URIRef
    g = _graph(mgr)
    with mgr.working:
        g.add((URIRef(s), URIRef(p), _node(o)))


def _remove(mgr, s, p, o) -> None:
    """Remove ``(s, p, o)``; ``o=None`` removes every object. owlready2's
    rdflib view ignores a wildcard remove, so each object is removed by
    exact match."""
    from rdflib import URIRef
    g = _graph(mgr)
    S, P = URIRef(s), URIRef(p)
    targets = list(g.objects(S, P)) if o is None else [_node(o)]
    with mgr.working:
        for t in targets:
            g.remove((S, P, t))


def _lit(text: str, lang: str | None = None):
    from rdflib import Literal
    return Literal(text, lang=lang) if lang else Literal(text)


def _objects(mgr, s, p) -> list:
    from rdflib import URIRef
    return list(_graph(mgr).objects(URIRef(s), URIRef(p)))


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    return s or hashlib.sha1((text or "").encode()).hexdigest()[:8]


def _working(local: str) -> str:
    from .ontology_manager import WORKING_IRI
    return f"{WORKING_IRI}#{local}"


def act_source_url(act_id: str) -> str:
    """A dereferenceable source for an act id (CELEX -> EUR-Lex)."""
    if re.fullmatch(r"3\d{4}[A-Z]\d{4}", act_id or ""):
        return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{act_id}"
    if (act_id or "").startswith("http"):
        return act_id
    return ""


def _declare_annotations(mgr) -> None:
    """Declare the annotation properties legal drafting writes (idempotent),
    so a draft stays in OWL 2 DL for the OWL API (ROBOT, Protege)."""
    from . import relation_vocab
    for iri in [relation_vocab.bfoagent(n) for n in relation_vocab.BFOAGENT_ANNOTATIONS] + \
            [IAO_DEFINITION, IAO_DEFINITION_SOURCE]:
        if OWL_ANNOTATION not in [str(o) for o in _objects(mgr, iri, RDF_TYPE)]:
            _put(mgr, iri, RDF_TYPE, OWL_ANNOTATION)


def act_iris(act_id: str) -> dict:
    slug = _slug(act_id)
    return {"content": _working(f"Act_{slug}_Content"),
            "enactment": _working(f"Act_{slug}_Enactment"),
            "document": _working(f"Act_{slug}_Document")}


def ensure_act(mgr, act_id: str, act_title: str = "", language: str = "en",
               adoption_date: str | None = None) -> dict | None:
    """Spec 2.2: the act's legal content, the enactment establishing it, and
    the legal document carrying it. Idempotent; None without an act id."""
    if not act_id:
        return None
    _declare_annotations(mgr)
    iris = act_iris(act_id)
    if _objects(mgr, iris["content"], RDF_TYPE):
        return iris
    title = act_title or act_id
    source = act_source_url(act_id)
    _put(mgr, iris["content"], RDF_TYPE, OWL_INDIVIDUAL)
    _put(mgr, iris["content"], RDF_TYPE, LEGAL_CONTENT)
    _put(mgr, iris["content"], RDFS_LABEL, _lit(f"content of {title}", language))
    _put(mgr, iris["content"], DCT + "identifier", _lit(act_id))
    if source:
        _put(mgr, iris["content"], DCT + "source", source)
    _put(mgr, iris["enactment"], RDF_TYPE, OWL_INDIVIDUAL)
    _put(mgr, iris["enactment"], RDF_TYPE, ENACTMENT)
    _put(mgr, iris["enactment"], RDFS_LABEL, _lit(f"enactment of {title}", language))
    _put(mgr, iris["enactment"], ESTABLISHES, iris["content"])
    if adoption_date:
        _put(mgr, iris["enactment"], DCT + "date", _lit(adoption_date))
    _put(mgr, iris["document"], RDF_TYPE, OWL_INDIVIDUAL)
    _put(mgr, iris["document"], RDF_TYPE, LEGAL_DOCUMENT)
    _put(mgr, iris["document"], RDFS_LABEL, _lit(f"published text of {title}", language))
    _put(mgr, iris["document"], IS_CARRIER_OF, iris["content"])
    mgr.save()
    return iris


# ------------------------------------------------- scoped definitions

LEGAL_DEFINITION = _working("LegalDefinition")
_SCOPE = re.compile(r"\bfor the purposes? of this (regulation|directive|decision|act|code|"
                    r"law|chapter|part|title|section|article|agreement|convention)\b[^:,;.]*",
                    re.I)


def detect_scope(text: str) -> str:
    m = _SCOPE.search(text or "")
    return m.group(0).strip() if m else ""


def _norm_label(s: str) -> str:
    s = re.sub(r"[‘’'\"]", "", s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _definiens_key(s: str) -> str:
    return _norm_label(s)


def _defined_entity(proposal, claim):
    """The entity a definition claim defines: the proposal entity whose label
    matches the claim's subject, else the first class it touches."""
    subj = _norm_label(claim.get("subject") or "")
    classes = [e for e in (getattr(proposal, "entities", []) or [])
               if getattr(e, "kind", "") == "class"]
    for e in classes:
        if subj and _norm_label(e.label) == subj:
            return e
    return classes[0] if classes else None


def _entity_iri(ent) -> str | None:
    from .ontology_manager import WORKING_IRI, _resolve_iri
    ref = ent.existing_iri or ent.iri_suggestion
    if not ref:
        return None
    try:
        return ref if ref.startswith("http") else _resolve_iri(ref, WORKING_IRI)
    except Exception:
        return None


def _is_operative(locator: str | None) -> bool:
    loc = (locator or "").strip()
    return bool(loc) and loc != "unknown" and not loc.startswith("rec_")


def record_definition(mgr, proposal, claim: dict, language: str = "en") -> dict | None:
    """Spec section 5 for one committed definition claim. Returns a summary
    ``{class, definition_individual, action, ...}`` or None when nothing was
    recorded (not a definition, nothing defined)."""
    if (claim.get("clause_type") or "") != "definition":
        return None
    ent = _defined_entity(proposal, claim)
    cls_iri = _entity_iri(ent) if ent is not None else None
    if not cls_iri or mgr.world[cls_iri] is None:
        return None
    act_id = claim.get("act_id") or ""
    act_title = claim.get("act_title") or act_id
    locator = claim.get("locator") or "unknown"
    definiens = claim.get("source_quote") or ""
    source_ref = " ".join(x for x in (act_source_url(act_id) or act_id, locator) if x)
    label = ent.label or claim.get("subject") or cls_iri.rsplit("#", 1)[-1]

    # The definition individual, part of the act's content (spec 5.1).
    if not _objects(mgr, LEGAL_DEFINITION, RDF_TYPE):
        _put(mgr, LEGAL_DEFINITION, RDF_TYPE, OWL_CLASS)
        _put(mgr, LEGAL_DEFINITION, RDFS_SUBCLASS, LEGAL_CONTENT)
        _put(mgr, LEGAL_DEFINITION, RDFS_LABEL, _lit("legal definition", "en"))
        _put(mgr, LEGAL_DEFINITION, IAO_DEFINITION,
             _lit("A legal content that states what a term means for the purposes "
                  "of the instrument that makes it.", "en"))
    def_iri = _working(f"Def_{_slug(act_id or 'unscoped')}_{_slug(locator)}_{_slug(label)}")
    _put(mgr, def_iri, RDF_TYPE, OWL_INDIVIDUAL)
    _put(mgr, def_iri, RDF_TYPE, LEGAL_DEFINITION)
    _put(mgr, def_iri, RDFS_LABEL, _lit(f"definition of '{label}' ({act_title}, {locator})", language))
    _put(mgr, def_iri, _working_meta("definiens"), _lit(definiens, language))
    _put(mgr, def_iri, _working_meta("definesTerm"), cls_iri)
    _put(mgr, def_iri, _working_meta("sourceLocator"), _lit(locator))
    scope = claim.get("scope") or detect_scope(definiens)
    if scope:
        _put(mgr, def_iri, _working_meta("scope"), _lit(scope, language))
    if act_id:
        content = (ensure_act(mgr, act_id, act_title, language) or {}).get("content")
        if content:
            _put(mgr, def_iri, CONTINUANT_PART_OF, content)

    # The defined class: verbatim definiens and source, or a scoped sense.
    existing_sources = [str(o) for o in _objects(mgr, cls_iri, _working_meta("definedIn"))]
    existing_defs = _objects(mgr, cls_iri, IAO_DEFINITION)
    result = {"class": cls_iri, "definition_individual": def_iri, "act_id": act_id,
              "locator": locator}
    if not existing_sources:
        # No act has defined this class yet, so any IAO definition on it is
        # the proposer's genus-differentia rendering: keep it, marked
        # derived (spec 5.1), and let the verbatim definiens take the slot.
        # Wildcard removes: owlready2 does not match its own stored
        # language-tagged literals on an exact remove.
        _remove(mgr, cls_iri, IAO_DEFINITION, None)
        for d in existing_defs:
            _put(mgr, cls_iri, _working_meta("derivedDefinition"), d)
        _remove(mgr, cls_iri, _working_meta("definitionStatus"), None)
        existing_defs = []
    same = [d for d in existing_defs if _definiens_key(str(d)) == _definiens_key(definiens)]
    prior_locs = [str(o) for o in _objects(mgr, cls_iri, _working_meta("definitionLocator"))]
    upgrade = (act_id in existing_sources and not same
               and _is_operative(locator)
               and not any(_is_operative(l) for l in prior_locs))
    if upgrade:
        # The same act defined this class earlier from a recital or an
        # unlocated passage; its operative definition takes the slot and the
        # earlier text is kept, marked superseded.
        for d in existing_defs:
            _put(mgr, cls_iri, _working_meta("supersededDefinition"), d)
        for p_ in (IAO_DEFINITION, IAO_DEFINITION_SOURCE, _working_meta("definitionLocator")):
            _remove(mgr, cls_iri, p_, None)
        _put(mgr, cls_iri, IAO_DEFINITION, _lit(definiens, language))
        _put(mgr, cls_iri, IAO_DEFINITION_SOURCE, _lit(source_ref))
        _put(mgr, cls_iri, _working_meta("definitionLocator"), _lit(locator))
        result["action"] = "defined-from-operative-provision"
    elif not existing_sources:
        _put(mgr, cls_iri, IAO_DEFINITION, _lit(definiens, language))
        _put(mgr, cls_iri, IAO_DEFINITION_SOURCE, _lit(source_ref))
        _put(mgr, cls_iri, _working_meta("definedIn"), _lit(act_id or "unscoped"))
        _put(mgr, cls_iri, _working_meta("definitionStatus"), _lit("verbatim"))
        _put(mgr, cls_iri, _working_meta("definitionLocator"), _lit(locator))
        result["action"] = "defined"
    elif act_id in existing_sources or same:
        if act_id not in existing_sources:
            _put(mgr, cls_iri, IAO_DEFINITION_SOURCE, _lit(source_ref))
            _put(mgr, cls_iri, _working_meta("definedIn"), _lit(act_id or "unscoped"))
        result["action"] = "same-definition-added-source"
    else:
        # Spec 5.2: two acts, two definiens -> one scoped sense per act under
        # the shared class. The earlier definition stays where it is and is
        # also given its own sense, so each act's meaning is explicit.
        senses = []
        for src in existing_sources:
            prior = next((str(d) for d in existing_defs), "")
            senses.append(_scoped_sense(mgr, cls_iri, label, src, prior, None, language))
        senses.append(_scoped_sense(mgr, cls_iri, label, act_id or "unscoped",
                                    definiens, source_ref, language, act_title))
        _put(mgr, cls_iri, _working_meta("definedIn"), _lit(act_id or "unscoped"))
        _put(mgr, cls_iri, _working_meta("hasScopedSenses"), _lit("true"))
        result.update(action="scoped-senses", senses=senses,
                      differs_from=existing_sources)
    mgr.save()
    return result


def _scoped_sense(mgr, general_iri: str, label: str, act_id: str, definiens: str,
                  source_ref: str | None, language: str, act_title: str = "") -> str:
    base = general_iri.rsplit("#", 1)[-1]
    sense = _working(f"{base}_{_slug(act_id)}")
    if _objects(mgr, sense, RDF_TYPE):
        return sense
    _put(mgr, sense, RDF_TYPE, OWL_CLASS)
    _put(mgr, sense, RDFS_SUBCLASS, general_iri)
    _put(mgr, sense, RDFS_LABEL, _lit(f"{label} ({act_title or act_id})", language))
    if definiens:
        _put(mgr, sense, IAO_DEFINITION, _lit(definiens, language))
    _put(mgr, sense, IAO_DEFINITION_SOURCE,
         _lit(source_ref or (act_source_url(act_id) or act_id)))
    _put(mgr, sense, _working_meta("definedIn"), _lit(act_id))
    _put(mgr, sense, _working_meta("scopedSenseOf"), general_iri)
    return sense


def _working_meta(name: str) -> str:
    from . import relation_vocab
    return relation_vocab.bfoagent(name)
