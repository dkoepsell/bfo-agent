"""Tests for Phase 5: stable IRIs (FR-6), kext (§8), MLC well-formedness/typology
(FR-4/FR-5, SOoL-only)."""
from app import kext, mlc
from app import stable_iri as si
from app.schema import Entity, Proposal, Relation


# --- FR-6: deterministic individual IRIs ---------------------------------
def test_stable_name_is_deterministic_and_content_addressed():
    a = si.stable_local_name("Acme Corp", "BFO_0000040", "case-1")
    b = si.stable_local_name("Acme Corp", "BFO_0000040", "case-1")
    c = si.stable_local_name("Acme Corp", "BFO_0000040", "case-2")
    assert a == b           # same content -> same name (MC-3)
    assert a != c           # source-scoped


def test_remap_keeps_relations_in_sync():
    ent = Entity(label="Acme", iri_suggestion="working:Acme", bfo_type="BFO_0000040",
                 bfo_label="material entity", kind="individual", rationale="x")
    rel = Relation(s="working:Acme", p="working:bearerOf", o="working:RoleX",
                   rationale="x")
    prop = Proposal(session_id="s", utterance="case-1", entities=[ent],
                    relations=[rel])
    out = si.remap_proposal(prop, source=prop.utterance)
    assert out.entities[0].iri_suggestion.startswith("working:Acme_")
    assert out.relations[0].s == out.entities[0].iri_suggestion  # synced
    assert out.relations[0].o == "working:RoleX"                  # untouched
    # idempotent
    assert si.remap_proposal(prop, prop.utterance).entities[0].iri_suggestion \
        == out.entities[0].iri_suggestion


# --- §8: kernel-extension requests ---------------------------------------
def test_kext_parse_and_dedup():
    prop = Proposal(
        session_id="s", utterance="some case",
        entities=[], relations=[],
        open_questions=["KEXT: LegalPersonhood -- BFO has no notion of it",
                        "KEXT: LegalPersonhood -- duplicate",
                        "not a kext line"],
    )
    reqs = kext.collect(prop, "kernel-v1")
    assert len(reqs) == 1                       # deduped by id
    assert reqs[0].term == "LegalPersonhood"
    assert reqs[0].kernel_version == "kernel-v1"
    assert reqs[0].justification


def test_kext_persist(tmp_path):
    req = kext.parse("KEXT: Foo -- bar", "src", "kv")
    path = kext.persist(req, tmp_path)
    assert path.name == req.filename
    assert path.read_text(encoding="utf-8")  # wrote JSON


# --- FR-4/FR-5: MLC is SOoL-only -----------------------------------------
def test_mlc_well_formedness_only_for_sool():
    types = ["BFO_0000020", "BFO_0000015"]  # a norm/SDC and a process
    assert mlc.assess_well_formedness(types, "SpinozaCorpus") is None
    wf = mlc.assess_well_formedness(types, "SOoL_v1")
    assert wf is not None
    assert wf.present  # at least one MLC node populated


def test_mlc_contradiction_individual_only_for_sool():
    assert mlc.contradiction_individual("Authority Inflation", "src",
                                        "LeibnitzPhilCorpus") is None
    rec = mlc.contradiction_individual("Authority Inflation", "src", "SOoL_v1")
    assert rec is not None
    assert rec["kind"] == "individual"          # FR-5: never a class
    assert rec["mlc_link"] == [1, 2]
    # unknown defect type -> no record (no invented absence class)
    assert mlc.contradiction_individual("Nonsense", "src", "SOoL_v1") is None
