"""Recognition-layer vocabulary: the contradiction kernel crossed with the recognition chain.

Implements the vocabulary half of SPEC-recognition-layer.md (P1), which formalises
"The Recognition Layer" §§5-7:

    typology  =  kernel  x  chain

* The **kernel** is twelve domain-neutral contradiction primitives in four strata
  (A model-theoretic, B definitional, C meta-ontological, D pragmatic).
* The **chain** is the seven loci every recognition-constituted institution has:
  authority -> criteria -> assessor-in-role -> presenting facts -> recognition act
  -> effects -> remedy.
* A **domain profile** fixes, for one institution, which loci exist and therefore
  which primitives can fire at all (the "stratum profile" of §6.4).

This module is pure data plus helpers: no I/O, no reasoner, no ontology mutation.
Nothing here mints classes into an extracted ontology -- the recognition layer is
annotation, gate checks and report structure only (anchor, don't regenerate).

The legal-domain prototypes (:mod:`app.sool_extension`, :mod:`app.mlc`) are one row
of Table 1; their MLC node numbers and FR-5 contradiction codes are re-expressed
here as ``(kernel, locus)`` pairs so there is a single typology rather than two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from . import bfo_catalog

# BFO fragments used as locus anchors. bfo_catalog exposes the kernel classes we
# already anchor against elsewhere; GDC is not in that set, so name it locally the
# same way app.sool_extension does.
GDC = "BFO_0000031"  # generically dependent continuant
MATERIAL_ENTITY = "BFO_0000040"


# --------------------------------------------------------------------------
# The recognition chain (Recognition Layer 5.1)
# --------------------------------------------------------------------------

class Locus(str, Enum):
    """The seven loci of the recognition chain, in order."""

    AUTHORITY = "authority"
    CRITERIA = "criteria"
    ASSESSOR = "assessor"
    FACTS = "facts"
    ACT = "act"
    EFFECT = "effect"
    REMEDY = "remedy"


LOCUS_ORDER: tuple[Locus, ...] = (
    Locus.AUTHORITY,
    Locus.CRITERIA,
    Locus.ASSESSOR,
    Locus.FACTS,
    Locus.ACT,
    Locus.EFFECT,
    Locus.REMEDY,
)


@dataclass(frozen=True)
class LocusSpec:
    """One link of the chain, with the BFO categories a term at that link may take."""

    locus: Locus
    name: str
    gloss: str
    anchors: tuple[str, ...]
    anchor_gloss: str


CHAIN: tuple[LocusSpec, ...] = (
    LocusSpec(
        Locus.AUTHORITY, "Source of Authority",
        "the source entitled to fix the criteria",
        (bfo_catalog.SDC, MATERIAL_ENTITY, GDC),
        "norm-bearing dependent continuant, or the organisation bearing it",
    ),
    LocusSpec(
        Locus.CRITERIA, "Criteria",
        "the normative content the authority fixes",
        (bfo_catalog.SDC, GDC),
        "specifically dependent continuant (norm) or information content entity",
    ),
    LocusSpec(
        Locus.ASSESSOR, "Assessor in Role",
        "the party licensed by the authority to apply the criteria",
        (bfo_catalog.ROLE, MATERIAL_ENTITY),
        "role (realizable DC) inhering in a material entity",
    ),
    LocusSpec(
        Locus.FACTS, "Presenting Facts",
        "the state of affairs to which the criteria are applied",
        (GDC, bfo_catalog.QUALITY, bfo_catalog.PROCESS, MATERIAL_ENTITY),
        "worldly quality or process, or the information content recording it",
    ),
    LocusSpec(
        Locus.ACT, "Recognition Act",
        "the act in which criteria are applied to facts and a status is conferred",
        (bfo_catalog.PROCESS,),
        "occurrent / process",
    ),
    LocusSpec(
        Locus.EFFECT, "Effect",
        "what follows from the conferral, including the conferred status itself",
        (bfo_catalog.REALIZABLE, bfo_catalog.SDC),
        "realizable dependent continuant (role, disposition) or other SDC",
    ),
    LocusSpec(
        Locus.REMEDY, "Remedy",
        "the route by which a conferral can be revisited",
        (bfo_catalog.PROCESS, bfo_catalog.REALIZABLE),
        "process, or the disposition to undergo one",
    ),
)

CHAIN_BY_LOCUS: dict[Locus, LocusSpec] = {spec.locus: spec for spec in CHAIN}

# The 8-node Minimal Legal Chain of app.sool_extension is this chain instantiated
# to the "Legal order" row of Table 1. Node 6 (Target) and node 7 (Legal Effect)
# both sit at the effect locus: the target is the bearer of the effect.
MLC_NODE_TO_LOCUS: dict[int, Locus] = {
    1: Locus.AUTHORITY,
    2: Locus.CRITERIA,
    3: Locus.ASSESSOR,
    4: Locus.FACTS,
    5: Locus.ACT,
    6: Locus.EFFECT,
    7: Locus.EFFECT,
    8: Locus.REMEDY,
}


# --------------------------------------------------------------------------
# The contradiction kernel (Recognition Layer 6.1) and its instruments (6.3)
# --------------------------------------------------------------------------

class Instrument(str, Enum):
    """What it takes to see a primitive at all. The partition is a matter of
    principle, not of tooling maturity (Recognition Layer 6.3, Table 7)."""

    REASONER = "reasoner"                # description-logic reasoner
    STRUCTURAL = "structural"            # structural analysis of axioms
    WORLD_DATA = "world_data"            # evidence, audit, adjudication
    PROCESS_MODEL = "process_model"      # process-level modelling or simulation


@dataclass(frozen=True)
class KernelPrimitive:
    code: str
    name: str
    stratum: str
    definition: str
    signature: str
    instruments: tuple[Instrument, ...]
    partial: bool = False  # instrument sees it only in part / in special cases


def _k(*args, **kwargs) -> KernelPrimitive:
    return KernelPrimitive(*args, **kwargs)


KERNEL: dict[str, KernelPrimitive] = {p.code: p for p in (
    # Stratum A: failures of a theory relative to its models
    _k("K-A1", "Inconsistency", "A",
       "The theory admits no model; some sentence and its negation are jointly "
       "derivable. Variants: assertional, deontic, analytic.",
       "O |= _|_ globally; locally, jointly unsatisfiable assertions over one locus",
       (Instrument.REASONER,)),
    _k("K-A2", "Term Incoherence", "A",
       "The theory is satisfiable overall, but some term is not: a class "
       "equivalent to the empty class.",
       "O consistent, yet O |= C [= _|_ for some class C",
       (Instrument.REASONER,)),
    _k("K-A3", "Indeterminacy", "A",
       "Underconstraint: unintended models admitted, or membership undecided "
       "where the governing practice requires decision.",
       "divergence of intended from admitted models; underivable borderline membership",
       (Instrument.STRUCTURAL,)),
    # Stratum B: definitional failures
    _k("K-B1", "Circularity", "B",
       "Non-wellfounded definition or grounding: a term's conditions depend "
       "transitively on the term itself, or a grounding order is inverted.",
       "a cycle in the dependency graph of definitions, or inversion of a "
       "required grounding order",
       (Instrument.REASONER, Instrument.STRUCTURAL), partial=True),
    _k("K-B2", "Equivocation", "B",
       "One item playing incompatible semantic or functional roles: a term, "
       "condition or occupant doing double duty across conflicting contexts.",
       "a single element bound to two roles whose satisfaction conditions conflict",
       (Instrument.STRUCTURAL,)),
    _k("K-B3", "Residual Definition", "B",
       "A term defined solely by complement: membership fixed only negatively, "
       "as what remains once the positive categories are exhausted.",
       "C = ~D1 & ~D2 & ... with no positive differentia",
       (Instrument.STRUCTURAL,)),
    # Stratum C: meta-ontological failures
    _k("K-C1", "Category Violation", "C",
       "An entity forced under disjoint upper-ontological categories: the "
       "formalized category mistake.",
       "a : C and a : D, with C and D disjoint at the upper level",
       (Instrument.REASONER,)),
    _k("K-C2", "Level Confusion", "C",
       "Conflation of representational levels: a universal treated as a "
       "particular, a class as an instance, a proposition as a class.",
       "an element occupying positions at two representational levels whose "
       "disciplines conflict",
       (Instrument.STRUCTURAL,)),
    _k("K-C3", "Dependence Violation", "C",
       "A dependent entity posited without its bearer, or a grounding link "
       "severed, including the asymmetric mutual-dependence case.",
       "a specifically dependent continuant without a bearer; a relation over a "
       "relatum that cannot sustain it",
       (Instrument.REASONER, Instrument.STRUCTURAL), partial=True),
    # Stratum D: pragmatic failures -- available only where there are acts
    _k("K-D1", "Falsification", "D",
       "Mismatch between assertion and world: triggering conditions fabricated, "
       "suppressed or misrepresented. The artifact is internally coherent; its "
       "contact with reality is corrupted.",
       "an asserted triggering condition with no worldly truthmaker, or a "
       "truthmaker deliberately excluded",
       (Instrument.WORLD_DATA,)),
    _k("K-D2", "Performative Self-Defeat", "D",
       "An act or effect whose success conditions are undermined by its own "
       "performance: a norm whose compliance conditions preclude compliance.",
       "satisfaction of the act's content entails failure of the act's success conditions",
       (Instrument.PROCESS_MODEL,)),
    _k("K-D3", "Modal Clash (de jure / de facto)", "D",
       "A capacity present in structure but blocked in practice: possible "
       "according to the artifact, unrealizable in the institution implementing it.",
       "<>-in-structure conjoined with ~<>-in-practice for the same capacity",
       (Instrument.PROCESS_MODEL,)),
)}

STRATA: tuple[str, ...] = ("A", "B", "C", "D")

KERNEL_BY_STRATUM: dict[str, tuple[str, ...]] = {
    s: tuple(c for c, p in KERNEL.items() if p.stratum == s) for s in STRATA
}


# --------------------------------------------------------------------------
# The product construction (Recognition Layer 6.2)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TypedDefect:
    """A defect type: one kernel primitive at one locus (or across an edge)."""

    code: str
    name: str
    kernel: str
    locus: Locus
    locus_to: Locus | None = None
    reading: str = ""

    @property
    def stratum(self) -> str:
        return KERNEL[self.kernel].stratum


def _ct(code, name, kernel, locus, reading, locus_to=None) -> TypedDefect:
    return TypedDefect(code, name, kernel, locus, locus_to, reading)


# CT-1..CT-7: the classificatory types, derived rather than hand-maintained.
CT_TYPES: dict[str, TypedDefect] = {t.code: t for t in (
    _ct("CT-1", "Disjointness / Overlap Failure", "K-A1", Locus.CRITERIA,
        "intended disjointness left unasserted, so jointly satisfiable models "
        "survive; the formal signature of artifactual comorbidity",
        locus_to=Locus.ACT),
    _ct("CT-2", "Criterion Circularity", "K-B1", Locus.CRITERIA,
        "a category's defining conditions depend transitively on the category itself"),
    _ct("CT-3", "Threshold / Polythetic Incoherence", "K-A3", Locus.CRITERIA,
        "a disjunctive family without shared essence; membership underconstrained"),
    _ct("CT-4", "Criterion Double-Duty", "K-B2", Locus.CRITERIA,
        "one condition bound to conflicting inclusion and exclusion roles"),
    _ct("CT-5", "Residual Indeterminacy", "K-B3", Locus.CRITERIA,
        "the 'other specified / unspecified' pattern"),
    _ct("CT-6", "Recognition Failure", "K-C3", Locus.CRITERIA,
        "the recognition act cannot track the criteria; grounding severed",
        locus_to=Locus.ACT),
    _ct("CT-7", "Type Contradiction", "K-C1", Locus.EFFECT,
        "an entity forced under disjoint BFO categories"),
)}

# The 13 FR-5 legal contradiction types of app.mlc, re-expressed as (kernel, locus)
# pairs so the legal typology is a view of the kernel rather than a second one.
# Keyed by app.mlc.ContradictionType.name; the LC-n code is retained on each entry.
FR5_ALIASES: dict[str, TypedDefect] = {t.name: t for t in (
    _ct("LC-1", "Authority Inflation", "K-C3", Locus.CRITERIA,
        "a norm claims more authority than its source confers", locus_to=Locus.AUTHORITY),
    _ct("LC-2", "Recognition Failure", "K-C3", Locus.EFFECT,
        "the effect is not tracked by any recognising act", locus_to=Locus.ACT),
    _ct("LC-3", "Norm Misalignment", "K-A1", Locus.CRITERIA,
        "deontic conflict between norms drawn from one authority"),
    _ct("LC-4", "Role Vacancy", "K-D3", Locus.ASSESSOR,
        "a norm addresses a role no actor occupies: possible in structure, "
        "unrealizable in practice", locus_to=Locus.CRITERIA),
    _ct("LC-5", "Trigger Gap", "K-A3", Locus.FACTS,
        "triggering conditions underconstrain the act they are meant to license",
        locus_to=Locus.ACT),
    _ct("LC-6", "Act Omission Conflict", "K-A1", Locus.ACT,
        "one act both required and forbidden"),
    _ct("LC-7", "Effect Without Norm", "K-C3", Locus.EFFECT,
        "a legal effect with no norm to ground it", locus_to=Locus.CRITERIA),
    _ct("LC-8", "Remedy Without Effect", "K-C3", Locus.REMEDY,
        "a remedy for an effect the artifact never confers", locus_to=Locus.EFFECT),
    _ct("LC-9", "Circular Authority", "K-B1", Locus.AUTHORITY,
        "an authority grounded, transitively, in itself"),
    _ct("LC-10", "Conflicting Norms", "K-A1", Locus.CRITERIA,
        "jointly unsatisfiable norms"),
    _ct("LC-11", "Target Mismatch", "K-C1", Locus.EFFECT,
        "an effect borne by a target of the wrong upper category"),
    _ct("LC-12", "Dangling Trigger", "K-C3", Locus.FACTS,
        "triggering facts with no bearer or no act to feed"),
    _ct("LC-13", "Unsourced Norm", "K-C3", Locus.CRITERIA,
        "a norm with no source of authority", locus_to=Locus.AUTHORITY),
)}


# --------------------------------------------------------------------------
# Domain profiles (Table 1) and the stratum profile (6.4, Table 8)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainProfile:
    """One row of Table 1, plus the failure geometry of 5.3 / 6.4."""

    key: str
    name: str
    authority: str
    criteria: str
    assessor: str
    act: str
    effect: str
    remedy: str
    act_thickness: str          # "none" | "thin" | "thick"
    repair: str                 # "none" | "external" | "internal"
    system_class: str
    loci: tuple[Locus, ...] = LOCUS_ORDER

    @property
    def has_chain(self) -> bool:
        return self.act_thickness != "none"

    @property
    def active_strata(self) -> tuple[str, ...]:
        """Which strata can fire at all. A-C apply to any ontology whatsoever;
        D fires only where there are acts (6.4)."""
        if not self.has_chain:
            return ("A", "B", "C")
        return ("A", "B", "C", "D")

    @property
    def stratum_d_thin(self) -> bool:
        """Recognition-only institutions have a thin D: acts are external to the
        artifact, so only the structural D primitives can fire."""
        return self.has_chain and (self.act_thickness == "thin" or self.repair == "external")


def _d(key, name, authority, criteria, assessor, act, effect, remedy,
       act_thickness, repair, system_class, loci=LOCUS_ORDER) -> DomainProfile:
    return DomainProfile(key, name, authority, criteria, assessor, act, effect,
                         remedy, act_thickness, repair, system_class, loci)


SCIENTIFIC_REFERENCE = _d(
    "scientific_reference", "Scientific reference ontology",
    "-", "-", "-", "-", "-", "-",
    "none", "none", "scientific reference ontology", loci=(),
)

# Table 1's twelve rows. The three system classes named in Table 8 are the paper's:
# scientific reference ontology (no chain), recognition-only institution
# (act-thin, repair-external: DSM-5-TR, ICD-11, technical standards), and the
# act-thick/repair-thick class (legal order, licensure, refugee status). The
# thickness of the remaining rows is a default read off Table 1's remedy column
# and is meant to be overridden per ontology by the operator.
DOMAIN_PROFILES: dict[str, DomainProfile] = {p.key: p for p in (
    SCIENTIFIC_REFERENCE,
    _d("legal_order", "Legal order", "constitution, legislature",
       "statute, doctrine", "judge, official", "judgment",
       "liability, entitlement", "appeal, review",
       "thick", "internal", "full legal system"),
    _d("clinical_nosology", "Clinical nosology", "APA, WHO",
       "diagnostic criteria", "clinician", "diagnosis",
       "treatment, coverage, forensic standing", "revision cycle (external)",
       "thin", "external", "recognition-only institution"),
    _d("currency", "Currency", "state, central bank", "legal tender rules",
       "issuing authority", "issuance", "discharge of debt", "demonetization",
       "thick", "internal", "act-thick institution"),
    _d("academic_credential", "Academic credential", "university, accreditor",
       "degree requirements", "faculty, registrar", "conferral",
       "licensure eligibility, hiring", "revocation",
       "thick", "internal", "act-thick institution"),
    _d("citizenship", "Citizenship", "state", "nationality law",
       "consular or immigration officer", "naturalization",
       "entry, franchise, protection", "denaturalization, appeal",
       "thick", "internal", "act-thick institution"),
    _d("corporate_personhood", "Corporate personhood", "state registrar",
       "incorporation statute", "registrar", "registration",
       "limited liability, standing", "dissolution, piercing",
       "thick", "internal", "act-thick institution"),
    _d("patent_grant", "Patent grant", "patent office",
       "novelty, non-obviousness", "examiner", "grant", "exclusion right",
       "invalidation", "thick", "internal", "act-thick institution"),
    _d("professional_licensure", "Professional licensure", "licensing board",
       "competency standards", "board examiner", "licensure",
       "scope of practice", "suspension, revocation",
       "thick", "internal", "full legal system"),
    _d("refugee_status", "Refugee status", "state, UNHCR",
       "Convention definition", "determination officer", "determination",
       "non-refoulement, benefits", "appeal, cessation",
       "thick", "internal", "full legal system"),
    _d("technical_certification", "Technical certification", "standards body",
       "conformance specification", "certifier, auditor", "certification",
       "market access", "decertification",
       "thin", "external", "recognition-only institution"),
    _d("electoral_office", "Electoral office", "election authority",
       "election law", "canvassing board", "certification",
       "occupancy of office", "recount, contest",
       "thick", "internal", "act-thick institution"),
    _d("scholarly_record", "Scholarly record", "journal, editor",
       "review criteria", "referee, editor", "acceptance",
       "publication, credit", "retraction",
       "thick", "internal", "act-thick institution"),
)}

DEFAULT_DOMAIN = SCIENTIFIC_REFERENCE.key


def profile_for(domain: str | None) -> DomainProfile:
    """Resolve a domain key to its profile, defaulting to the scientific-reference
    control. Never guess an institutional profile: an unknown key falls back to
    'no chain', which switches Stratum D off rather than inventing acts."""
    if not domain:
        return SCIENTIFIC_REFERENCE
    return DOMAIN_PROFILES.get(str(domain).strip().lower(), SCIENTIFIC_REFERENCE)


def active_primitives(profile: DomainProfile) -> tuple[str, ...]:
    """Which kernel primitives can fire at all under this profile (6.4)."""
    strata = set(profile.active_strata)
    codes = [c for c, p in KERNEL.items() if p.stratum in strata]
    if profile.stratum_d_thin:
        # Acts and repair are external to the artifact: K-D1 needs world-facing
        # data the artifact does not contain, so it cannot fire on the artifact.
        codes = [c for c in codes if c != "K-D1"]
    return tuple(codes)


# --------------------------------------------------------------------------
# Anchoring and completeness helpers
# --------------------------------------------------------------------------

def locus_anchors(locus: Locus | str) -> tuple[str, ...]:
    spec = CHAIN_BY_LOCUS.get(Locus(locus))
    return spec.anchors if spec else ()


def anchor_ok(locus: Locus | str, bfo_fragment: str | None) -> bool:
    """True if a term anchored at ``bfo_fragment`` is admissible at ``locus``.

    Comparison is against the BFO kernel fragment the term is anchored to; callers
    that hold an IRI should normalise it first.
    """
    if not bfo_fragment:
        return False
    frag = str(bfo_fragment).rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    anchors = locus_anchors(locus)
    if frag in anchors:
        return True
    # Accept any anchor that BFO subsumes under an admissible one.
    for allowed in anchors:
        if bfo_catalog.is_descendant_of(frag, allowed):
            return True
    return False


# Status values for the stratum-completeness table (7.5).
INSTRUMENTED = "instrumented"
SILENT_BY_PRINCIPLE = "silent_by_principle"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CompletenessRow:
    code: str
    name: str
    stratum: str
    status: str
    instruments: tuple[str, ...]
    note: str = ""


def completeness(profile: DomainProfile,
                 instrumented: set[str] | frozenset[str] | tuple[str, ...]) -> list[CompletenessRow]:
    """The stratum-completeness table: for each of the twelve primitives, whether
    this run could see it.

    A zero count for a primitive that is not instrumented is not a measurement.
    Every surface reporting a contradiction-debt figure must render this table
    beside it (SPEC P7).
    """
    instrumented = set(instrumented or ())
    active = set(active_primitives(profile))
    rows: list[CompletenessRow] = []
    for code, prim in KERNEL.items():
        if code not in active:
            if prim.stratum != "D":
                note = "not active under this profile"
            elif not profile.has_chain:
                note = "no chain: Stratum D fires only where there are acts"
            else:
                note = ("thin D: this institution's acts and repair are "
                        "external to the artifact, so the primitive cannot "
                        "fire on it")
            status = NOT_APPLICABLE
        elif code in instrumented:
            status, note = INSTRUMENTED, ""
        else:
            status, note = SILENT_BY_PRINCIPLE, (
                "requires " + ", ".join(i.value for i in prim.instruments))
        rows.append(CompletenessRow(code, prim.name, prim.stratum, status,
                                    tuple(i.value for i in prim.instruments), note))
    return rows


# Contradiction debt (7.1). Weights are UNCALIBRATED: CD ships as a typed count,
# and any aggregate is a lower bound over the instrumented primitives only (13).
WEIGHTS_CALIBRATED = False
DEFAULT_WEIGHT = 1.0


def weight(kernel_code: str) -> float:
    """Severity weight w(c). Uniform while uncalibrated -- CD is a count."""
    return DEFAULT_WEIGHT if kernel_code in KERNEL else 0.0


def contradiction_debt(findings, profile: DomainProfile,
                       instrumented: set[str] | None = None,
                       totals: dict[str, int] | None = None) -> dict:
    """CD(O) = sum of w(c) over unresolved findings, with its completeness table.

    ``findings`` is any iterable of mappings carrying at least ``kernel_code``;
    ``locus`` and ``attribution`` are used when present. Findings attributed to
    our own translation are counted separately and never folded into the debt of
    the source artifact (10).

    ``totals`` overrides the per-type counts where the caller knows the full
    population and passed only a sample of it -- a sampled count must never be
    reported as the whole.
    """
    instrumented = set(instrumented or ())
    per_type: dict[str, int] = {}
    per_locus: dict[str, int] = {}
    translation = 0
    undetermined = 0
    total = 0.0
    for f in findings or ():
        code = (f.get("kernel_code") or "").strip()
        if code not in KERNEL:
            continue
        attribution = (f.get("attribution") or "undetermined").strip()
        if attribution == "translation":
            translation += 1
            continue
        if attribution == "undetermined":
            undetermined += 1
        per_type[code] = per_type.get(code, 0) + 1
        locus = f.get("locus")
        if locus:
            per_locus[str(locus)] = per_locus.get(str(locus), 0) + 1
        total += weight(code)
    sampled: dict[str, int] = {}
    for code, full in (totals or {}).items():
        if code not in KERNEL:
            continue
        seen = per_type.get(code, 0)
        if full > seen:
            sampled[code] = seen
            total += weight(code) * (full - seen)
            per_type[code] = full
            # The unshown remainder is unadjudicated by construction.
            undetermined += full - seen
    return {
        "cd": total,
        "calibrated": WEIGHTS_CALIBRATED,
        "lower_bound": True,
        "per_type": per_type,
        "per_locus": per_locus,
        "attributed_to_translation": translation,
        "attribution_undetermined": undetermined,
        # Types whose findings list holds only a sample of the counted total.
        "sampled_types": sampled,
        "domain": profile.key,
        "active_strata": list(profile.active_strata),
        "completeness": [row.__dict__ for row in completeness(profile, instrumented)],
    }


def typed_defect_for_legal(name: str) -> TypedDefect | None:
    """The (kernel, locus) typing of one FR-5 legal contradiction type."""
    return FR5_ALIASES.get(name)


def loci_for_mlc_link(link) -> tuple[Locus, Locus] | None:
    """Map an ``(from_node, to_node)`` MLC link to its pair of chain loci."""
    try:
        a, b = link
    except (TypeError, ValueError):
        return None
    if a not in MLC_NODE_TO_LOCUS or b not in MLC_NODE_TO_LOCUS:
        return None
    return MLC_NODE_TO_LOCUS[a], MLC_NODE_TO_LOCUS[b]


def self_check() -> list[str]:
    """Internal consistency of the vocabulary. Returns a list of problems."""
    problems: list[str] = []
    if len(KERNEL) != 12:
        problems.append(f"kernel has {len(KERNEL)} primitives, expected 12")
    if len(CHAIN) != 7:
        problems.append(f"chain has {len(CHAIN)} loci, expected 7")
    if len(DOMAIN_PROFILES) != 13:  # twelve social domains + the control
        problems.append(f"{len(DOMAIN_PROFILES)} domain profiles, expected 13")
    for table in (CT_TYPES, FR5_ALIASES):
        for key, t in table.items():
            if t.kernel not in KERNEL:
                problems.append(f"{key} references unknown kernel {t.kernel}")
            if t.locus not in CHAIN_BY_LOCUS:
                problems.append(f"{key} references unknown locus {t.locus}")
    if len(CT_TYPES) != 7:
        problems.append(f"{len(CT_TYPES)} classificatory types, expected 7")
    if len(FR5_ALIASES) != 13:
        problems.append(f"{len(FR5_ALIASES)} FR-5 aliases, expected 13")
    for node, locus in MLC_NODE_TO_LOCUS.items():
        if locus not in CHAIN_BY_LOCUS:
            problems.append(f"MLC node {node} maps to unknown locus {locus}")
    return problems
