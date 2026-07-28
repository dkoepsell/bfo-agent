"""Agent-side construction linter (bfo-agent-spec.md §6, PC-1..PC-8).

The proposer's failure mode is well documented: feeding a corpus produced
thousands of flat classes (``AbsenceOfLegalExistence``,
``NormDependencyRelation``, ...) and zero object properties. This linter runs
on the proposer's own draft *before* the coherence gate's straddle/reasoner
tiers and rejects the prohibited constructions that drive that proliferation,
returning ``{rule, offending_term, suggested_rewrite}`` for each so the
bounded-retry loop can self-correct.

Reconciliation with the workbench (important): bfo-agent-spec.md describes a
case-fragment mode where *zero* new classes are allowed (PC-4, strict closed
vocabulary). The interactive workbench, by contrast, exists to build a domain
ontology *on top of* BFO, which legitimately mints some classes (``Norm``,
``Contract``). So:

  * PC-1, PC-2, PC-3, PC-5, PC-6 are ALWAYS-ON hard rejects -- they only ever
    catch genuinely malformed constructions (privations, relation-baked names,
    untyped entities, invented predicates, continuant/occurrent conflation).
  * PC-7 (class expression baked into an IRI) and PC-8 (privation/compound term
    in ANY IRI fragment, including expression operands) are ALWAYS-ON. They run
    the lexical A7/A6 checks from ``owl_checks`` -- the SAME validators the gate
    runs on the serialized fragment -- over the proposal's IRIs, so self-lint ==
    gate. PC-8 is the IRI-fragment twin of PC-1's name-level privation reject.
  * PC-4 (off-vocabulary class IRI / zero-new-class) is OPT-IN via
    ``strict_closed_vocab=True`` -- it implements the spec's case-fragment mode.

The class-count budget (FR-7) is a separate, softer cap handled in the
orchestrator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import bfo_catalog
from . import owl_checks


# ---------------------------------------------------------------------------
# PC-1: privation primitives. Leading-token match so "NonArbitrary" /
# "Unlawful" / "AbsenceOfX" are caught but "Universal" / "Nonsense" are not
# (the prefix forms require a following CamelCase boundary).
# ---------------------------------------------------------------------------
_PRIVATION_WORD = re.compile(
    r"^(AbsenceOf|Absence|Lack|Loss|Missing|Failure|Broken|Invalid|"
    r"Invalidity|Degraded|Collapsed|Residual|Partial)"
)
_PRIVATION_PREFIX = re.compile(r"^(No|Non|Un)(?=[A-Z])")

# PC-2 / PC-5: relational keywords that should be a property assertion, not a
# string baked into a class name.
_RELATION_CONNECTIVES = ("With", "For")  # "Of"/"And" are too common in legit terms
_RELATION_NOUNS = ("Relation", "Dependency", "Alignment", "Grounding", "Support")

# Tokens that are legitimate BFO/domain genus words and should not count toward
# the "compound" budget on their own.
_CAMEL_TOKEN = re.compile(r"[A-Z][a-z0-9]*")


@dataclass
class Violation:
    rule: str                 # e.g. "PC-1"
    offending_term: str       # the local name or IRI at fault
    suggested_rewrite: str    # how the agent should fix it
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "offending_term": self.offending_term,
            "suggested_rewrite": self.suggested_rewrite,
            "detail": self.detail,
        }


@dataclass
class Finding:
    """A typed defect *recorded*, not rejected.

    Findings carry a kernel code and a chain locus (SPEC P4). They describe the
    source, not our construction, so under faithful fidelity they must never
    cause a rewrite: the extracted ontology keeps the source's errors and the
    defect is evidence.
    """

    rule: str                 # e.g. "PC-10"
    kernel_code: str          # e.g. "K-B3"
    locus: str                # recognition-chain locus
    term: str
    detail: str = ""
    # Never default to "source": what we see is our proposal, and the defect may
    # belong to our translation of the source rather than to the institution
    # (Recognition Layer §10). Attribution is an analyst's call.
    attribution: str = "undetermined"

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "kernel_code": self.kernel_code,
            "locus": self.locus,
            "term": self.term,
            "detail": self.detail,
            "attribution": self.attribution,
        }


@dataclass
class LintReport:
    violations: list[Violation] = field(default_factory=list)
    findings: list["Finding"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        """One-line-per-violation rendering for a corrective prompt note."""
        return "\n".join(
            f"- [{v.rule}] '{v.offending_term}': {v.suggested_rewrite}"
            for v in self.violations
        )

    def to_dicts(self) -> list[dict]:
        return [v.to_dict() for v in self.violations]

    def finding_dicts(self) -> list[dict]:
        return [f.to_dict() for f in self.findings]


def _local(ref: str) -> str:
    s = (ref or "").split("#")[-1]
    s = s.split("/")[-1]
    return s.split(":")[-1]


def _camel_tokens(name: str) -> list[str]:
    return _CAMEL_TOKEN.findall(name)


def _is_new_class(ent) -> bool:
    return (
        getattr(ent, "kind", None) == "class"
        and getattr(ent, "is_new", False)
        and not getattr(ent, "existing_iri", None)
    )


# ---------------------------------------------------------------------------
# Individual checks.
# ---------------------------------------------------------------------------
def _check_privation(name: str) -> Optional[Violation]:
    """PC-1: reject privation/absence primitives."""
    m = _PRIVATION_WORD.match(name) or _PRIVATION_PREFIX.match(name)
    if not m:
        return None
    return Violation(
        rule="PC-1",
        offending_term=name,
        suggested_rewrite=(
            "Do not model absence/failure as a class. Express it as a "
            "contradiction-type individual, or as an owl:complementOf / "
            "cardinality-0 restriction over a BFO property (e.g. "
            "'not (bearer_of some X)')."
        ),
        detail=f"name begins with privation marker '{m.group(1)}'",
    )


def _check_compound(name: str) -> Optional[Violation]:
    """PC-2: reject relation-baked / over-compounded class names."""
    for conn in _RELATION_CONNECTIVES:
        if conn in _camel_tokens(name):
            return Violation(
                rule="PC-2",
                offending_term=name,
                suggested_rewrite=(
                    f"'{conn}' bakes a relation into a class name. Emit the "
                    f"meaning as a class expression instead, e.g. "
                    f"'A and (someRelation some B)'."
                ),
                detail=f"contains relational connective '{conn}'",
            )
    for noun in _RELATION_NOUNS:
        if name.endswith(noun) and name != noun:
            return Violation(
                rule="PC-2",
                offending_term=name,
                suggested_rewrite=(
                    f"'{noun}' names a relation, not a kind. Assert the "
                    f"relation as an object-property triple over BFO terms "
                    f"instead of minting this class."
                ),
                detail=f"ends with relational noun '{noun}'",
            )
    tokens = _camel_tokens(name)
    if len(tokens) >= 4:
        return Violation(
            rule="PC-2",
            offending_term=name,
            suggested_rewrite=(
                "This name fuses several concepts. Decompose it into a class "
                "expression / property assertions over simpler kernel terms."
            ),
            detail=f"{len(tokens)} CamelCase tokens",
        )
    return None


def _entity_anchor(ent) -> str:
    return bfo_catalog.normalize_fragment(getattr(ent, "bfo_type", "") or "")


def _check_untyped(ent) -> Optional[Violation]:
    """PC-3: every entity must resolve to a BFO category.

    A reused entity (existing_iri set) is assumed to carry its typing already.
    """
    if getattr(ent, "existing_iri", None):
        return None
    anchor = _entity_anchor(ent)
    if anchor in bfo_catalog.KERNEL_CLASSES:
        return None
    parent = bfo_catalog.normalize_fragment(getattr(ent, "parent_class", "") or "")
    if parent in bfo_catalog.KERNEL_CLASSES:
        return None
    return Violation(
        rule="PC-3",
        offending_term=_local(getattr(ent, "iri_suggestion", "") or ent.label),
        suggested_rewrite=(
            "Give this entity a bfo_type that resolves to a BFO 2020 category "
            f"(one of K_C). Got bfo_type='{getattr(ent, 'bfo_type', None)}'."
        ),
        detail="no path to a BFO category",
    )


def _check_conflation(ent) -> Optional[Violation]:
    """PC-6: an entity typed as both a continuant and an occurrent."""
    anchors = {
        _entity_anchor(ent),
        bfo_catalog.normalize_fragment(getattr(ent, "parent_class", "") or ""),
    }
    anchors.discard("")
    is_cont = any(
        bfo_catalog.is_descendant_of(a, bfo_catalog.CONTINUANT) for a in anchors
    )
    is_occ = any(
        bfo_catalog.is_descendant_of(a, bfo_catalog.OCCURRENT) for a in anchors
    )
    if is_cont and is_occ:
        return Violation(
            rule="PC-6",
            offending_term=_local(getattr(ent, "iri_suggestion", "") or ent.label),
            suggested_rewrite=(
                "An entity cannot be both a continuant and an occurrent. Pick "
                "one: a thing that persists (continuant) or a happening "
                "(occurrent)."
            ),
            detail="continuant/occurrent conflation",
        )
    return None


def _check_predicate(rel) -> Optional[Violation]:
    """PC-5: a relation predicate must be a BFO object property or meta-predicate.

    An invented predicate is a string-baked relation; route it to a real BFO
    relation instead.
    """
    p = getattr(rel, "p", "") or ""
    if bfo_catalog.is_meta_predicate(p) or bfo_catalog.is_kernel_property(p):
        return None
    return Violation(
        rule="PC-5",
        offending_term=p,
        suggested_rewrite=(
            "Use a BFO 2020 object property (K_P) -- e.g. inheres in "
            "(BFO_0000197), realized in (BFO_0000054), participates in "
            "(BFO_0000056), part of (BFO_0000050) -- or rdfs:subClassOf / "
            "rdf:type. If no BFO relation fits, file a kernel-extension request."
        ),
        detail="predicate is not in K_P and is not a meta-predicate",
    )


# An upper-ontology id (BFO/RO/IAO) used as a subClassOf/type target is only
# valid if it is a known BFO CLASS. If it is a relation (BFO_0000050 "part of",
# BFO_0000054 "realized in", any RO_*) it is NOT in K_C -- and a class subclassed
# to a property crashes owlready2 with a metaclass conflict that bricks the whole
# ontology. K_P is a curated subset (it omits part_of etc.), so we cannot rely on
# is_kernel_property alone: anything that looks like an upper-ontology id but is
# not a kernel class is treated as a non-class target.
_UPPER_ONTOLOGY_ID = re.compile(r"^(?:BFO|RO|IAO)_\d+$")


def _target_is_not_a_class(ref: str) -> bool:
    frag = bfo_catalog.normalize_fragment(ref or "")
    if not frag:
        return False
    if bfo_catalog.is_kernel_property(frag):
        return True
    if _UPPER_ONTOLOGY_ID.match(frag) and frag not in bfo_catalog.KERNEL_CLASSES:
        return True
    return False


def _check_class_parent(ent) -> Optional[Violation]:
    """PC-5: a subClassOf/type target must be a BFO class, not a relation.

    ``X subClassOf <a BFO property>`` (e.g. ``PropertyRight subClassOf
    BFO_0000054``, or ``RelationalField subClassOf BFO_0000050`` "part of")
    crashes owlready2 with a metaclass conflict that bricks the whole ontology.
    PC-3 misses it when the entity's own ``bfo_type`` is a valid class but its
    ``parent_class`` is a property.
    """
    parent = getattr(ent, "parent_class", "") or ""
    if _target_is_not_a_class(parent):
        return Violation(
            rule="PC-5",
            offending_term=_local(getattr(ent, "iri_suggestion", "") or ent.label),
            suggested_rewrite=(
                f"parent_class '{_local(parent)}' is a BFO relation, not a class. "
                f"A class cannot be a subclass of a property. Anchor to a BFO "
                f"category and express the relation as a property assertion."
            ),
            detail="subClassOf target is not a BFO class",
        )
    return None


def _check_relation_class_target(rel) -> Optional[Violation]:
    """PC-5: a subClassOf / rdf:type triple's object must be a class, not a
    relation. Same metaclass-conflict hazard as a bad parent_class."""
    p = getattr(rel, "p", "") or ""
    if "subClassOf" not in p and "type" not in p:
        return None
    o = getattr(rel, "o", "") or ""
    # A sanctioned class expression ("not X", "P some F") is a valid
    # subClassOf target; lint its class operand instead of the raw string.
    expr = owl_checks.parse_class_expression(o)
    if expr is not None:
        o = expr.get("cls") or expr.get("filler") or ""
    if _target_is_not_a_class(o):
        return Violation(
            rule="PC-5",
            offending_term=_local(getattr(rel, "s", "")),
            suggested_rewrite=(
                f"'{_local(o)}' is a BFO relation; a class cannot be "
                f"subClassOf/typed to a property. Use a BFO category, or assert "
                f"the relation as a property triple."
            ),
            detail="subClassOf/type target is not a BFO class",
        )
    return None


def _check_off_vocabulary(ent) -> Optional[Violation]:
    """PC-4 (strict mode only): no class IRI outside K_C; zero new classes."""
    if _is_new_class(ent):
        return Violation(
            rule="PC-4",
            offending_term=_local(getattr(ent, "iri_suggestion", "") or ent.label),
            suggested_rewrite=(
                "Strict closed-vocabulary mode forbids new classes. Express "
                "this as an individual typed to a K_C class, or as a class "
                "expression over kernel terms. If a genuinely new primitive is "
                "required, emit a kernel-extension request and proceed without "
                "it."
            ),
            detail="new named class in strict closed-vocabulary mode",
        )
    return None


# ---------------------------------------------------------------------------
# PC-9..PC-13: recognition-chain checks (SPEC-recognition-layer.md P4).
#
# These run only for ontologies with a declared institutional profile, and they
# split two ways on the artifact-versus-source discipline (Recognition Layer 10):
#
#   * PC-9, PC-11, PC-12 are defects of OUR construction -> Violations, rejected
#     and rewritten, attributed to the translation.
#   * PC-10, PC-13 are defects of the SOURCE -> Findings, recorded and preserved.
#     Under faithful fidelity the extraction keeps them.
# ---------------------------------------------------------------------------
_RESIDUAL_PATTERNS = (
    "other specified", "otherspecified",
    "unspecified", "not otherwise specified", "nototherwisespecified",
    "nos", "unclassified", "unclassifiable", "residual",
    "other disorders of", "otherdisordersof",
)

_EXCLUSION_MARKERS = ("exclusion", "excludes", "notincluded", "not included")
_INCLUSION_MARKERS = ("inclusion", "includes", "criterion", "criteria")


def _entity_locus(ent) -> Optional[str]:
    loc = getattr(ent, "recognition_locus", None)
    if not loc or loc == "none":
        return None
    return str(loc)


def _check_locus_anchor(ent) -> Optional[Violation]:
    """PC-9 (K-C1 at a chain locus): the locus and the BFO anchor disagree.

    Rejected, not recorded: a status modelled as a quality, or an act modelled
    as a realizable, is a mistake in our translation, not in the institution.
    """
    from . import recognition as rec

    locus = _entity_locus(ent)
    if not locus:
        return None
    bfo_type = getattr(ent, "bfo_type", None) or getattr(ent, "bfo_class", None)
    if not bfo_type:
        return None
    if rec.anchor_ok(locus, bfo_type):
        return None
    spec = rec.CHAIN_BY_LOCUS.get(rec.Locus(locus))
    allowed = ", ".join(spec.anchors) if spec else ""
    return Violation(
        rule="PC-9",
        offending_term=_local(getattr(ent, "iri_suggestion", "") or ent.label),
        suggested_rewrite=(
            f"A term at the '{locus}' locus must be anchored under "
            f"{allowed} ({spec.anchor_gloss if spec else ''}). Re-anchor the "
            f"term, or set recognition_locus to the link it actually occupies "
            f"(or 'none' if it is not part of the chain)."
        ),
        detail=f"locus '{locus}' anchored to {_local(str(bfo_type))}",
    )


def _check_chain_class(ent) -> Optional[Violation]:
    """PC-12 (K-C2 level confusion): the chain itself minted as a class.

    The loci are annotations on the source's own terms. A class literally named
    for a link of the chain conflates the framework with the domain.
    """
    if not _is_new_class(ent):
        return None
    name = _local(getattr(ent, "iri_suggestion", "") or ent.label)
    tokens = [t.lower() for t in _camel_tokens(name)] or [name.lower()]
    joined = "".join(tokens)
    bare = {
        "authority", "criteria", "criterion", "assessor", "recognitionact",
        "recognitionchain", "remedy", "presentingfacts", "sourceofauthority",
    }
    if joined in bare:
        return Violation(
            rule="PC-12",
            offending_term=name,
            suggested_rewrite=(
                "Do not mint the recognition chain as classes. The locus is an "
                "annotation (recognition_locus) on the terms the source names; "
                "emit the source's own term and tag its locus instead."
            ),
            detail=f"class name '{name}' names a chain link, not a domain term",
        )
    return None


def _check_residual_definition(ent) -> Optional[Finding]:
    """PC-10 / CT-5 (K-B3): the 'other specified / unspecified' pattern.

    Recorded, never rejected: residual categories are a real and load-bearing
    feature of the source classification. Flagging one is evidence about the
    institution; rewriting it would falsify the extraction.
    """
    name = _local(getattr(ent, "iri_suggestion", "") or ent.label)
    hay = " ".join(filter(None, [
        str(getattr(ent, "label", "") or ""), name,
    ])).lower()
    squashed = hay.replace(" ", "").replace("_", "").replace("-", "")
    hit = next(
        (p for p in _RESIDUAL_PATTERNS
         if p in hay or p.replace(" ", "") in squashed),
        None,
    )
    if hit is None:
        return None
    locus = _entity_locus(ent) or "criteria"
    return Finding(
        rule="PC-10",
        kernel_code="K-B3",
        locus=locus,
        term=name,
        detail=(
            f"residual marker '{hit}': membership fixed only negatively, as "
            f"what remains once the positive categories are exhausted"
        ),
    )


def _check_criterion_double_duty(proposal) -> list[Finding]:
    """PC-11 / CT-4 (K-B2): one criterion bound to conflicting inclusion and
    exclusion roles for the same category.

    Detected over the proposal's relations: the same (subject, object) pair
    asserted through both an inclusion-flavoured and an exclusion-flavoured
    predicate. Recorded as a source finding -- the double duty is in the text.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for rel in getattr(proposal, "relations", ()) or ():
        subj = _local(str(getattr(rel, "s", "") or ""))
        obj = _local(str(getattr(rel, "o", "") or ""))
        pred = _local(str(getattr(rel, "p", "") or "")).lower()
        if not subj or not obj:
            continue
        role = None
        if any(m in pred for m in _EXCLUSION_MARKERS):
            role = "exclusion"
        elif any(m in pred for m in _INCLUSION_MARKERS):
            role = "inclusion"
        if role is None:
            continue
        seen.setdefault((subj, obj), set()).add(role)
    out: list[Finding] = []
    for (subj, obj), roles in seen.items():
        if len(roles) > 1:
            out.append(Finding(
                rule="PC-11",
                kernel_code="K-B2",
                locus="criteria",
                term=f"{subj} -> {obj}",
                detail=("one condition bound to both inclusion and exclusion "
                        "roles for the same category"),
            ))
    return out


def _check_unasserted_disjointness(proposal) -> list[Finding]:
    """PC-13 / CT-1 (K-A1 latent): sibling categories the source treats as
    exclusive, with no disjointness asserted between them.

    Flagged, never repaired: asserting the disjointness ourselves would be our
    claim, not the source's. The formal signature of artifactual comorbidity.
    """
    parents: dict[str, list[str]] = {}
    for rel in getattr(proposal, "relations", ()) or ():
        pred = _local(str(getattr(rel, "p", "") or "")).lower()
        if pred not in ("subclassof", "rdfs:subclassof", "is_a", "isa"):
            continue
        subj = _local(str(getattr(rel, "s", "") or ""))
        obj = _local(str(getattr(rel, "o", "") or ""))
        if subj and obj:
            parents.setdefault(obj, []).append(subj)

    disjoint_pairs: set[frozenset[str]] = set()
    for rel in getattr(proposal, "relations", ()) or ():
        pred = _local(str(getattr(rel, "p", "") or "")).lower()
        if "disjoint" not in pred:
            continue
        subj = _local(str(getattr(rel, "s", "") or ""))
        obj = _local(str(getattr(rel, "o", "") or ""))
        if subj and obj:
            disjoint_pairs.add(frozenset((subj, obj)))

    out: list[Finding] = []
    for parent, kids in parents.items():
        kids = sorted(set(kids))
        if len(kids) < 2:
            continue
        undeclared = [
            (a, b)
            for i, a in enumerate(kids) for b in kids[i + 1:]
            if frozenset((a, b)) not in disjoint_pairs
        ]
        if len(undeclared) == len(kids) * (len(kids) - 1) // 2:
            out.append(Finding(
                rule="PC-13",
                kernel_code="K-A1",
                locus="criteria",
                term=parent,
                detail=(
                    f"{len(kids)} siblings under '{parent}' with no "
                    f"disjointness asserted: jointly satisfiable models survive"
                ),
            ))
    return out


# ---------------------------------------------------------------------------
# PC-7 / PC-8: lexical IRI checks, delegated to owl_checks so the agent's
# self-lint uses the exact validators the gate runs on the emitted fragment.
# ---------------------------------------------------------------------------
_PC7_PC8_REWRITE = {
    "PC-7": (
        "A class expression is baked into this IRI. Do not template "
        "'#[ A and not (p some B) ]' as an IRI -- build a proper anonymous "
        "OWL construct (owl:intersectionOf / owl:complementOf / "
        "owl:Restriction) via owl_checks emitters and reference it by blank "
        "node."
    ),
    "PC-8": (
        "A privation/compound marker appears inside an IRI fragment. Reuse the "
        "positive kernel term and negate with owl:complementOf / a "
        "cardinality-0 restriction instead of minting an absence term (even as "
        "an expression operand, e.g. 'working:NonQuantity')."
    ),
}


def _proposal_iris(proposal) -> list[str]:
    """Every minted/asserted IRI string in the proposal, for lexical checks."""
    iris: list[str] = []
    for ent in proposal.entities:
        iris.append(getattr(ent, "iri_suggestion", "") or "")
        iris.append(getattr(ent, "existing_iri", "") or "")
    for rel in proposal.relations:
        iris.append(getattr(rel, "s", "") or "")
        iris.append(getattr(rel, "p", "") or "")
        iris.append(getattr(rel, "o", "") or "")
    return [i for i in iris if i]


def _check_iris(proposal) -> list[Violation]:
    """PC-7 (E_EXPR_IRI) and PC-8 (E_ANTIPATTERN) over the proposal's IRIs."""
    report = owl_checks.check_iri_strings(_proposal_iris(proposal))
    violations: list[Violation] = []
    for f in report.findings:
        rule = "PC-7" if f.code == "E_EXPR_IRI" else "PC-8"
        violations.append(
            Violation(
                rule=rule,
                offending_term=f.detail,
                suggested_rewrite=_PC7_PC8_REWRITE[rule],
                detail=f"owl_checks {f.code}",
            )
        )
    return violations


# ---------------------------------------------------------------------------
# Top-level entry point.
# ---------------------------------------------------------------------------
def lint(proposal, strict_closed_vocab: bool = False,
         chain_active: bool = False) -> LintReport:
    """Run PC-1..PC-13 over a proposal draft. Returns a LintReport.

    PC-1/PC-2/PC-3/PC-5/PC-6/PC-7/PC-8 always run. PC-4 runs only when
    ``strict_closed_vocab`` is set (the spec's case-fragment mode). PC-9..PC-13
    run only when ``chain_active`` -- i.e. the ontology declares an
    institutional recognition profile -- and split between violations (defects
    of our construction, rejected) and findings (defects of the source,
    recorded and preserved).
    """
    report = LintReport()

    # PC-7 / PC-8: lexical IRI checks (same validators the gate runs).
    report.violations.extend(_check_iris(proposal))

    for ent in proposal.entities:
        name = _local(getattr(ent, "iri_suggestion", "") or ent.label)

        # Privation / compound naming only apply to newly minted classes.
        if _is_new_class(ent):
            for check in (_check_privation, _check_compound):
                v = check(name)
                if v is not None:
                    report.violations.append(v)
            if strict_closed_vocab:
                v = _check_off_vocabulary(ent)
                if v is not None:
                    report.violations.append(v)

        # Typing checks apply to every emitted entity.
        for check in (_check_untyped, _check_conflation, _check_class_parent):
            v = check(ent)
            if v is not None:
                report.violations.append(v)

        # PC-9..PC-12: chain checks, only for institutional profiles.
        if chain_active:
            for check in (_check_locus_anchor, _check_chain_class):
                v = check(ent)
                if v is not None:
                    report.violations.append(v)
            f = _check_residual_definition(ent)
            if f is not None:
                report.findings.append(f)

    for rel in proposal.relations:
        # subClassOf / type predicates are fine; only non-meta predicates that
        # are not BFO object properties are string-baked relations.
        v = _check_predicate(rel)
        if v is not None:
            report.violations.append(v)
        # A subClassOf/type target must be a class, not a relation.
        v = _check_relation_class_target(rel)
        if v is not None:
            report.violations.append(v)

    # PC-11 / PC-13: proposal-level source findings, recorded not rejected.
    if chain_active:
        report.findings.extend(_check_criterion_double_duty(proposal))
        report.findings.extend(_check_unasserted_disjointness(proposal))

    return report
