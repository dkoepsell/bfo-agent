"""QS-E1 / QS-F1..F4: legal-hohfeld profile and canonical anchoring."""
from __future__ import annotations

import json

import pytest
from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef

from app import canonical, incoherence_ledger, profiles

WN = "http://davidkoepsell.com/bfo-agent/working#"
OBO = "http://purl.obolibrary.org/obo/"
MANIFEST = {"profiles": ["legal-hohfeld"]}

pytestmark = pytest.mark.skipif(not profiles.BFO_PATH.exists(),
                                reason="ontology/bfo.owl not downloaded")


def _cls(g, local, label, parent):
    u = URIRef(WN + local)
    g.add((u, RDF.type, OWL.Class))
    g.add((u, RDFS.label, Literal(label)))
    g.add((u, RDFS.subClassOf, URIRef(parent)))
    return u


def _graph(permission_under_obligation=True, right_as_disposition=False):
    g = Graph()
    _cls(g, "Obligation", "Obligation", OBO + "BFO_0000023")
    _cls(g, "Permission", "Permission",
         WN + "Obligation" if permission_under_obligation else OBO + "BFO_0000023")
    _cls(g, "Right", "Right",
         OBO + "BFO_0000016" if right_as_disposition else OBO + "BFO_0000023")
    return g


def test_qs_e1_profile_file_matches_builder():
    g = Graph(); g.parse(str(profiles.PROFILES_DIR / "legal-hohfeld.owl"))
    built = profiles.build_legal_hohfeld()
    assert len(g) == len(built)
    S = canonical.SOOL
    positions = [S[k] for k in canonical.HOHFELD_POSITIONS]
    assert len(positions) == 8
    for i, a in enumerate(positions):
        assert (a, RDFS.subClassOf, S.LegalPosition) in g
        for b in positions[i + 1:]:
            assert (a, OWL.disjointWith, b) in g
    assert (S.LegalPosition, RDFS.subClassOf, URIRef(OBO + "BFO_0000023")) in g  # D-2
    assert (S.Norm, RDFS.subClassOf, URIRef(OBO + "BFO_0000031")) in g           # D-3
    assert (S.LegalAct, OWL.disjointWith, S.LegalDocument) in g


def test_qs_e1_permission_under_obligation_is_a_violation():
    found = profiles.check_profiles(_graph(), MANIFEST)
    assert [f["subject"] for f in found] == [WN + "Permission"]
    assert "Permission violates legal-hohfeld" in found[0]["reason"]
    assert "privilege" in found[0]["reason"] and "duty" in found[0]["reason"]


def test_qs_e1_d2_position_placed_under_disposition_is_a_violation():
    found = profiles.check_profiles(
        _graph(permission_under_obligation=False, right_as_disposition=True),
        MANIFEST)
    assert [f["subject"] for f in found] == [WN + "Right"]


def test_qs_e1_no_profile_no_findings():
    assert profiles.check_profiles(_graph(), {}) == []


def test_qs_e1_subjects_limits_check():
    assert profiles.check_profiles(_graph(), MANIFEST,
                                   subjects=[WN + "Obligation"]) == []


def test_qs_e1_faithful_ledgers_and_never_rejects(tmp_path):
    working = tmp_path / "working.owl"
    g = _graph(); g.serialize(destination=str(working), format="xml")
    before = working.read_bytes()
    res = profiles.apply_profiles(working, MANIFEST, "faithful")
    assert res["reject"] is False
    assert len(res["ledger_ids"]) == 1
    entries = incoherence_ledger.read_all(working)
    assert entries[0]["tier"] == "profile"
    assert entries[0]["exclude_axioms"] == []
    assert working.read_bytes() == before  # FG-0: the claim is untouched


def test_qs_e1_curated_rejects_without_ledger(tmp_path):
    working = tmp_path / "working.owl"
    _graph().serialize(destination=str(working), format="xml")
    res = profiles.apply_profiles(working, MANIFEST, "curated")
    assert res["reject"] is True
    assert incoherence_ledger.read_all(working) == []


def test_qs_e1_missing_profile_is_an_error():
    with pytest.raises(FileNotFoundError):
        profiles.profile_axioms_for({"profiles": ["no-such-profile"]})


def test_qs_f1_canonical_core_file_matches_builder():
    g = Graph(); g.parse(str(canonical.SOOL_CORE_PATH))
    assert len(g) == len(canonical.build_sool_core())


def test_qs_f1_seed_paths_resolve_library_then_repo(tmp_path):
    (tmp_path / "local.owl").write_text("")
    paths = canonical.seed_paths(
        {"canonical_seed": ["local.owl", "ontology/canonical/sool-canonical.owl"]},
        tmp_path)
    assert paths[0] == tmp_path / "local.owl"
    assert paths[1] == canonical.SOOL_CORE_PATH


def test_qs_f2_label_index_and_aliases():
    seed = canonical.build_sool_core()
    S = str(canonical.SOOL)
    assert canonical.canonical_match("Obligation", seed) == S + "Duty"
    assert canonical.canonical_match("Remedy", seed) == S + "Remedy"
    assert canonical.canonical_match("Legal Person", seed) == S + "LegalPerson"
    assert canonical.canonical_match("Recognition Process", seed) is None


def test_qs_f2_rank_canonical_first_is_stable():
    cands = [{"iri": "a"}, {"iri": "c1"}, {"iri": "b"}, {"iri": "c2"}]
    ranked = canonical.rank_canonical_first(cands, {"c1", "c2"})
    assert [c["iri"] for c in ranked] == ["c1", "c2", "a", "b"]


def test_qs_f3_coverage_reports_gaps_without_writing(tmp_path):
    working = tmp_path / "working.owl"
    g = _graph(permission_under_obligation=False)
    _cls(g, "CivilRemedy", "Civil Remedy", WN + "Remedy")
    g.serialize(destination=str(working), format="xml")
    before = working.read_bytes()
    cov = canonical.library_coverage(
        working, {"canonical_seed": "ontology/canonical/sool-canonical.owl"})
    by_label = {r["label"]: r for r in cov["rows"]}
    assert by_label["duty"]["aligned_classes"] == [WN + "Obligation"]
    assert "triggering facts" in cov["zero_coverage"]
    assert working.read_bytes() == before


def test_qs_f3_no_seed_no_report(tmp_path):
    working = tmp_path / "working.owl"
    _graph().serialize(destination=str(working), format="xml")
    assert canonical.library_coverage(working, {}) is None


def test_qs_f4_design_decisions_recorded_as_data():
    g = canonical.build_sool_core()
    notes = " ".join(str(o) for o in g.objects(None, canonical.BFOAGENT.designDecision))
    assert "D-2" in notes and "D-3" in notes
