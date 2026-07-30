#!/usr/bin/env python3
"""Phase 4: hand-authored chain-locus scaffold -> ``ontology/cfr404-chain.owl``.

This is the index set the kernel is instantiated over. It is authored by hand and
*before* extraction on purpose: if an extractor invented the loci, locus attribution
would be circular and every Table 13 density would be a fact about the extractor.

Provenance is not decorative here. Every term names a section and an anchor phrase,
and the build fails loudly if the anchor is not present verbatim in that section's
corpus text. A term that cannot be anchored does not get authored.

The central modelling commitment is the separation of authority (L1) from assessor
(L3). The baseline collapses both into ``DeterminingAgencyRole``; keeping them
merged would make K-B2, authority inflation, undetectable by construction.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS, XSD

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import BFO, CFR, REL, ROOT  # noqa: E402

OUT = ROOT / "ontology" / "cfr404-chain.owl"
CHUNKS = ROOT / "corpus" / "chunks"
CHAIN_IRI = URIRef("http://davidkoepsell.com/cfr404/chain")
REPORT = ROOT / "ontology" / "phase4-chain-report.json"

# ---------------------------------------------------------------------------
# Object properties the chain needs. Domains and ranges are asserted so that
# K-C3 (relation misuse) has something to check against.
# ---------------------------------------------------------------------------
OBJ_PROPS = [
    ("derivesAuthorityFrom", "derives authority from", BFO["role"], BFO["role"],
     "Links an L3 assessor role to the L1 authority role that grants it. This is the "
     "edge K-B2 is computed over: an assessor role whose grant does not reach the "
     "function it is assigned is authority inflation."),
    ("grantsRole", "grants role", BFO["process"], BFO["role"],
     "A recognition act that brings an assessor role into existence. Granting an "
     "assessor role is itself an act, not a brute fact about an office."),
    ("appliesCriteria", "applies criteria", BFO["process"], BFO["gdc"],
     "L5 act to the L2 criteria it is required to apply."),
    ("producesEffect", "produces effect", BFO["process"], BFO["realizable"],
     "L5 act to the L6 status or entitlement it brings about."),
    ("assessedBy", "assessed by", BFO["process"], BFO["role"],
     "L5 act to the L3 role responsible for performing it."),
    ("repairPathFor", "repair path for", BFO["process"], BFO["process"],
     "L7 remedy to the L5 act it can revisit."),
    ("hasPrecondition", "has precondition", BFO["process"], BFO["gdc"],
     "A condition that must hold before a repair path is available."),
    ("excludesPath", "excludes path", BFO["process"], BFO["process"],
     "One repair path barring another. Jointly unsatisfiable exclusion cycles are "
     "the K-D3 signal."),
]

# ---------------------------------------------------------------------------
# (name, label, locus, [bfo parents], [named parents], section, anchor, comment)
# ---------------------------------------------------------------------------
T = []


def term(name, label, locus, bfo, parents, section, anchor, comment, restrictions=()):
    T.append(dict(name=name, label=label, locus=locus, bfo=list(bfo), parents=list(parents),
                  section=section, anchor=anchor, comment=comment,
                  restrictions=list(restrictions)))


# ---- abstract spines -------------------------------------------------------
term("AuthorityRole", "authority role", "L1", [BFO["role"]], [], "404.1503",
     "Commissioner",
     "A role borne by an institution that holds a grant of decisional power. Kept "
     "strictly apart from AssessorRole: the Commissioner holds authority; an "
     "administrative law judge holds an assessment role granted under it.")
term("AssessorRole", "assessor role", "L3", [BFO["role"]], [], "404.1546",
     "residual functional capacity",
     "A role borne by a person who is made responsible for an assessment. Disjoint "
     "from AuthorityRole by assertion, which is what makes authority inflation "
     "expressible rather than definitionally impossible.",
     [(REL["inheres_in"], "Person")])
term("RecognitionAct", "recognition act", "L5", [BFO["process"]], [], "404.900",
     "administrative review process",
     "A process in which an institution declares a status that did not obtain before "
     "the declaration.")
term("RemedyProcess", "remedy process", "L7", [BFO["process"]], [], "404.900",
     "dissatisfied",
     "A process by which a completed recognition act can be revisited.")
term("InstitutionalCriterion", "institutional criterion", "L2", [BFO["gdc"]], [],
     "404.1520", "five-step sequential evaluation process",
     "A generically dependent continuant that fixes what an act must find.")
term("PresentedFact", "presented fact", "L4", [BFO["gdc"]], [], "404.1512", "evidence",
     "A generically dependent continuant offered to an assessor as input to an act.")
term("InstitutionalEffect", "institutional effect", "L6", [BFO["realizable"]], [],
     "404.1505", "disability",
     "A realizable entity that exists only because a recognition act occurred.")

# ---- L1 source of authority ------------------------------------------------
term("CommissionerOfSocialSecurity", "Commissioner of Social Security", "L1",
     [BFO["object"]], [], "404.1503", "Commissioner",
     "The federal officer in whom the Act vests the determination power.",
     [(REL["bearer_of"], "CommissionerAuthorityRole")])
term("CommissionerAuthorityRole", "Commissioner authority role", "L1", [], ["AuthorityRole"],
     "404.1503", "Commissioner",
     "The grant of decisional power held by the Commissioner.")
term("StateAgencyAuthorityRole", "State agency authority role", "L1", [], ["AuthorityRole"],
     "404.1503", "State",
     "The grant under which a State agency makes disability determinations for the "
     "Commissioner. It is delegated, not original.",
     [(CFR["derivesAuthorityFrom"], "CommissionerAuthorityRole")])
term("AppealsCouncilAuthorityRole", "Appeals Council authority role", "L1", [],
     ["AuthorityRole"], "404.967", "Appeals Council",
     "The grant under which the Appeals Council reviews hearing decisions.",
     [(CFR["derivesAuthorityFrom"], "CommissionerAuthorityRole")])

# ---- L2 criteria -----------------------------------------------------------
term("ListingOfImpairments", "Listing of Impairments", "L2", [], ["InstitutionalCriterion"],
     "404.1525", "Listing of Impairments",
     "The catalogue of impairments severe enough to preclude gainful activity.")
term("SequentialEvaluationCriteria", "sequential evaluation criteria", "L2", [],
     ["InstitutionalCriterion"], "404.1520", "five-step sequential evaluation process",
     "The ordered steps an adjudicator must follow.")
term("ResidualFunctionalCapacityCriteria", "residual functional capacity criteria", "L2",
     [], ["InstitutionalCriterion"], "404.1545", "residual functional capacity",
     "What a claimant can still do despite limitations.")
term("MedicalImprovementReviewStandard", "medical improvement review standard", "L2", [],
     ["InstitutionalCriterion"], "404.1594", "medical improvement",
     "The standard governing whether an existing disability finding may be ended. "
     "The spec names this the highest-probability K-D3 site in the corpus.")
term("SupportabilityFactor", "supportability factor", "L2", [], ["InstitutionalCriterion"],
     "404.1520c", "upportability",
     "How well a medical opinion is supported by its own objective evidence.")
term("ConsistencyFactor", "consistency factor", "L2", [], ["InstitutionalCriterion"],
     "404.1520c", "onsistency",
     "How consistent a medical opinion is with the rest of the record.")
term("MedicalVocationalGuideline", "medical-vocational guideline", "L2", [],
     ["InstitutionalCriterion"], "App2-all", "sustained work capability",
     "The grid rules directing a conclusion from age, education and work experience.")

# ---- L3 assessor roles, person-borne, separate from L1 ---------------------
_L3 = [
    ("StateAgencyDisabilityExaminerRole", "State agency disability examiner role",
     "404.1615", "disability examiner", "StateAgencyAuthorityRole",
     "Makes the disability determination in the State agency, alone in the cases "
     "404.1615(c)(3) allows."),
    ("StateAgencyMedicalConsultantRole", "State agency medical consultant role",
     "404.1616", "medical consultant", "StateAgencyAuthorityRole",
     "A physician who joins the State agency determination."),
    ("StateAgencyPsychologicalConsultantRole", "State agency psychological consultant role",
     "404.1616", "psychological consultant", "StateAgencyAuthorityRole",
     "A psychologist who joins the State agency determination."),
    ("DisabilityHearingOfficerRole", "disability hearing officer role",
     "404.1546", "disability hearing officer", "CommissionerAuthorityRole",
     "Assesses residual functional capacity at the disability hearing stage."),
    ("AssociateCommissionerForDisabilityDeterminationsRole",
     "Associate Commissioner for Disability Determinations role",
     "404.1546", "Associate Commissioner", "CommissionerAuthorityRole",
     "Or a delegate; assesses residual functional capacity when the disability "
     "hearing officer's determination is changed."),
    ("AdministrativeLawJudgeRole", "administrative law judge role",
     "404.1546", "administrative law judge", "CommissionerAuthorityRole",
     "Assesses residual functional capacity at the hearing level."),
    ("AdministrativeAppealsJudgeRole", "administrative appeals judge role",
     "404.1546", "administrative appeals judge", "AppealsCouncilAuthorityRole",
     "Assesses residual functional capacity at the Appeals Council level."),
    ("AppealsCouncilMemberRole", "Appeals Council member role",
     "404.967", "Appeals Council", "AppealsCouncilAuthorityRole",
     "Reviews hearing decisions on request or own motion."),
]
for nm, lbl, sec, anc, auth, cmt in _L3:
    term(nm, lbl, "L3", [], ["AssessorRole"], sec, anc, cmt,
         [(REL["inheres_in"], "Person"), (CFR["derivesAuthorityFrom"], auth)])

# ---- L4 presenting facts ---------------------------------------------------
term("EvidenceOfRecord", "evidence of record", "L4", [], ["PresentedFact"], "404.1512",
     "evidence", "Everything received into the claim file.")
term("MedicalOpinion", "medical opinion", "L4", [], ["PresentedFact"], "404.1513",
     "medical opinion",
     "A medical source's statement about what a claimant can still do.")
term("PriorAdministrativeMedicalFinding", "prior administrative medical finding", "L4", [],
     ["PresentedFact"], "404.1513", "prior administrative medical finding",
     "A finding by a prior State agency consultant at an earlier level.")
term("NonmedicalEvidence", "nonmedical evidence", "L4", [], ["PresentedFact"], "404.1513",
     "nonmedical source", "Evidence from sources who are not medical sources.")

# ---- L5 recognition acts ---------------------------------------------------
term("InitialDetermination", "initial determination", "L5", [], ["RecognitionAct"],
     "404.902", "initial determination",
     "The first determination on a claim; the act every repair path aims at.",
     [(CFR["appliesCriteria"], "SequentialEvaluationCriteria"),
      (CFR["producesEffect"], "DisabilityStatusEffect")])
term("ReconsideredDetermination", "reconsidered determination", "L5", [], ["RecognitionAct"],
     "404.907", "reconsideration", "The determination made on reconsideration.")
term("DisabilityHearingDecision", "disability hearing officer's reconsidered determination",
     "L5", [], ["RecognitionAct"], "404.918", "disability hearing officer",
     "The determination issued after a disability hearing.",
     [(CFR["assessedBy"], "DisabilityHearingOfficerRole")])
term("AdministrativeLawJudgeDecision", "administrative law judge decision", "L5", [],
     ["RecognitionAct"], "404.953", "decision",
     "The decision issued after a hearing.",
     [(CFR["assessedBy"], "AdministrativeLawJudgeRole")])
term("AppealsCouncilDecision", "Appeals Council decision", "L5", [], ["RecognitionAct"],
     "404.979", "decision", "The decision issued on Appeals Council review.",
     [(CFR["assessedBy"], "AppealsCouncilMemberRole")])
term("AssessorRoleDesignation", "assessor role designation", "L5", [], ["RecognitionAct"],
     "404.1615", "disability determinations",
     "The act by which an assessor role is conferred. Its effect is the existence of "
     "an L3 role, which is why the role is not treated as a brute institutional fact.",
     [(CFR["grantsRole"], "AssessorRole")])
term("ResidualFunctionalCapacityAssessment", "residual functional capacity assessment",
     "L5", [], ["RecognitionAct"], "404.1546", "residual functional capacity",
     "404.1546 assigns this single assessment to occupants at every level of the "
     "system. It is the densest assessor locus in the corpus and the site prediction 4 "
     "names for K-B2.",
     [(CFR["appliesCriteria"], "ResidualFunctionalCapacityCriteria")]
     + [(CFR["assessedBy"], nm) for nm, *_ in _L3[:7]])

# ---- L6 effects ------------------------------------------------------------
term("DisabilityStatusEffect", "disability status", "L6", [], ["InstitutionalEffect"],
     "404.1505", "disability",
     "The status of being disabled for purposes of the Act. It obtains because an act "
     "declared it, not because of the impairment alone.")
term("EntitlementToBenefits", "entitlement to benefits", "L6", [], ["InstitutionalEffect"],
     "404.900", "benefits", "The claim on payment that a favourable act creates.")
term("MedicareEligibility", "Medicare eligibility", "L6", [], ["InstitutionalEffect"],
     "404.900", "Medicare", "Eligibility under title XVIII following from entitlement.")
term("CessationOfDisability", "cessation of disability", "L6", [], ["InstitutionalEffect"],
     "404.1594", "no longer", "The withdrawal of disability status by a later act.")

# ---- L7 remedies and their preconditions -----------------------------------
term("Reconsideration", "reconsideration", "L7", [], ["RemedyProcess"], "404.907",
     "reconsideration", "The first level of administrative review.",
     [(CFR["repairPathFor"], "InitialDetermination")])
term("AdministrativeLawJudgeHearing", "hearing before an administrative law judge", "L7",
     [], ["RemedyProcess"], "404.929", "administrative law judge",
     "The second level of administrative review.",
     [(CFR["repairPathFor"], "ReconsideredDetermination")])
term("AppealsCouncilReview", "Appeals Council review", "L7", [], ["RemedyProcess"],
     "404.967", "Appeals Council", "The third level of administrative review.",
     [(CFR["repairPathFor"], "AdministrativeLawJudgeDecision")])
term("JudicialReview", "judicial review", "L7", [], ["RemedyProcess"], "404.981",
     "Federal district court", "Review in federal district court after the Council acts.",
     [(CFR["repairPathFor"], "AppealsCouncilDecision")])
term("Reopening", "reopening", "L7", [], ["RemedyProcess"], "404.987", "reopen",
     "Revisiting a final determination outside the ordinary appeal sequence.",
     [(CFR["repairPathFor"], "InitialDetermination"),
      (CFR["hasPrecondition"], "ReopeningTimeCondition")])
term("Revision", "revision", "L7", [], ["RemedyProcess"], "404.987", "revised",
     "The change made to a determination that has been reopened.",
     [(CFR["repairPathFor"], "InitialDetermination")])
term("ResJudicataDismissal", "dismissal on res judicata", "L7", [], ["RemedyProcess"],
     "404.957", "res judicata",
     "Dismissal of a request for hearing because the same claim has already been "
     "decided. It bars the very revisiting that reopening allows.",
     [(CFR["repairPathFor"], "InitialDetermination"),
      (CFR["excludesPath"], "AdministrativeLawJudgeHearing")])
term("ContinuingDisabilityReview", "continuing disability review", "L7", [],
     ["RemedyProcess"], "404.1589", "review",
     "Periodic review of whether an existing disability continues.",
     [(CFR["repairPathFor"], "InitialDetermination"),
      (CFR["appliesCriteria"], "MedicalImprovementReviewStandard")])
term("ReopeningTimeCondition", "reopening time condition", "L2", [],
     ["InstitutionalCriterion"], "404.988", "12 months",
     "The time limits within which a determination may be reopened.")
term("GoodCauseForReopening", "good cause for reopening", "L2", [],
     ["InstitutionalCriterion"], "404.989", "good cause",
     "What must be shown to reopen after the shortest window has closed.")
term("ResJudicataBar", "res judicata bar", "L2", [], ["InstitutionalCriterion"],
     "404.957", "res judicata",
     "The condition under which a claim may not be adjudicated again.")


# ---------------------------------------------------------------------------
def quote(chunk_text: str, anchor: str, max_words: int = 38) -> str:
    m = re.search(re.escape(anchor), chunk_text, re.IGNORECASE)
    start = max(0, m.start() - 150)
    window = chunk_text[start:m.end() + 150].replace("\n", " ")
    return " ".join(window.split()[:max_words])


def main() -> int:
    chunks = {p.stem: json.loads(p.read_text()) for p in CHUNKS.glob("*.json")}

    missing = []
    for t in T:
        ch = chunks.get(t["section"])
        if ch is None:
            missing.append((t["name"], t["section"], "section not in corpus"))
        elif not re.search(re.escape(t["anchor"]), ch["text"], re.IGNORECASE):
            missing.append((t["name"], t["section"], f"anchor {t['anchor']!r} not found"))
    if missing:
        for n, s, why in missing:
            print(f"ANCHOR FAIL  {n}  ({s}): {why}", file=sys.stderr)
        print(f"\n{len(missing)} unanchored term(s); refusing to author them.", file=sys.stderr)
        return 1

    g = Graph()
    g.bind("cfr", CFR)
    g.bind("obo", "http://purl.obolibrary.org/obo/")
    g.add((CHAIN_IRI, RDF.type, OWL.Ontology))
    g.add((CHAIN_IRI, OWL.versionIRI,
           URIRef("http://davidkoepsell.com/cfr404/chain/2026-07-30")))
    g.add((CHAIN_IRI, OWL.imports, URIRef("http://davidkoepsell.com/cfr404/kernel")))
    g.add((CHAIN_IRI, OWL.imports, URIRef("http://purl.obolibrary.org/obo/bfo.owl")))
    g.add((CHAIN_IRI, RDFS.label, Literal("cfr404 recognition-chain scaffold (L1-L7)")))
    g.add((CHAIN_IRI, DCTERMS.license,
           URIRef("https://creativecommons.org/licenses/by/4.0/")))
    g.add((CHAIN_IRI, RDFS.comment, Literal(
        "Hand-authored before extraction. Every term is anchored to a verbatim phrase "
        "in a named section of the acquired corpus; the build refuses to emit a term "
        "whose anchor cannot be found.")))

    for name, label, dom, rng, comment in OBJ_PROPS:
        u = CFR[name]
        g.add((u, RDF.type, OWL.ObjectProperty))
        g.add((u, RDFS.label, Literal(label)))
        g.add((u, RDFS.domain, dom))
        g.add((u, RDFS.range, rng))
        g.add((u, RDFS.comment, Literal(comment)))

    for t in T:
        u = CFR[t["name"]]
        ch = chunks[t["section"]]
        g.add((u, RDF.type, OWL.Class))
        g.add((u, RDFS.label, Literal(t["label"])))
        g.add((u, RDFS.comment, Literal(t["comment"])))
        for p in t["bfo"]:
            g.add((u, RDFS.subClassOf, p))
        for p in t["parents"]:
            g.add((u, RDFS.subClassOf, CFR[p]))
        for rel, filler in t["restrictions"]:
            b = BNode()
            g.add((b, RDF.type, OWL.Restriction))
            g.add((b, OWL.onProperty, rel))
            g.add((b, OWL.someValuesFrom, CFR[filler] if isinstance(filler, str) else filler))
            g.add((u, RDFS.subClassOf, b))
        g.add((u, CFR.chainLocus, Literal(t["locus"])))
        g.add((u, CFR.sourceSection, Literal(t["section"])))
        g.add((u, CFR.chunkId, Literal(t["section"])))
        g.add((u, CFR.sourceQuote, Literal(quote(ch["text"], t["anchor"]))))
        g.add((u, CFR.extractionConfidence, Literal("high")))
        g.add((u, CFR.approved, Literal(True, datatype=XSD.boolean)))
        g.add((u, CFR.reviewNote, Literal(
            f"hand-authored chain scaffold; anchored on {t['anchor']!r} in {t['section']}")))
        g.add((u, SKOS.editorialNote, Literal("Phase 4 scaffold term")))

    # The axiom that makes authority inflation expressible.
    g.add((CFR["AuthorityRole"], OWL.disjointWith, CFR["AssessorRole"]))
    g.add((CFR["AuthorityRole"], RDFS.comment, Literal(
        "Asserted disjoint from AssessorRole. The baseline's DeterminingAgencyRole "
        "merged the two; under that model an occupant could never be assigned a "
        "function exceeding its grant, because grant and function were the same "
        "entity. K-B2 would then be undetectable by construction rather than absent "
        "from the regulation.")))

    # Retire the merged baseline role rather than silently leaving it in place.
    dep = CFR["DeterminingAgencyRole"]
    g.add((dep, OWL.deprecated, Literal(True, datatype=XSD.boolean)))
    g.add((dep, RDFS.comment, Literal(
        "Deprecated by the Phase 4 scaffold: it collapsed the L1 grant of authority "
        "and the L3 assessment role into one term. Replaced by cfr:AuthorityRole "
        "(L1) and cfr:AssessorRole (L3), which are asserted disjoint.")))
    g.add((dep, SKOS.editorialNote, Literal("replaced by cfr:AuthorityRole + cfr:AssessorRole")))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(OUT), format="pretty-xml")

    by_locus: dict[str, list[str]] = {}
    for t in T:
        by_locus.setdefault(t["locus"], []).append(t["name"])
    l3 = [t for t in T if t["locus"] == "L3" and t["name"] != "AssessorRole"]
    report = {
        "phase": 4,
        "terms": len(T),
        "object_properties": len(OBJ_PROPS),
        "by_locus": {k: sorted(v) for k, v in sorted(by_locus.items())},
        "locus_counts": {k: len(v) for k, v in sorted(by_locus.items())},
        "person_borne_L3_roles": sorted(t["name"] for t in l3),
        "person_borne_L3_role_count": len(l3),
        "authority_separated_from_assessor": True,
        "anchor_failures": 0,
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"wrote {OUT.relative_to(ROOT)}: {len(T)} terms, {len(g)} triples")
    print("locus counts:", report["locus_counts"])
    print(f"person-borne L3 roles: {len(l3)} (spec floor is 4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
