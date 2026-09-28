"""QS-C1..C3: legacy migration of reasoner-invisible axioms
(scripts/migrate_invisible_axioms.py, SPEC-bfo-agent-quality.md section 5)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_invisible_axioms as mig  # noqa: E402
import ontology_audit  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "qs_migration_fixture.owl"
W = "http://davidkoepsell.com/bfo-agent/working#"
OBO = "http://purl.obolibrary.org/obo/"
QS_G3 = ["malformed_iris>0", "mangled_standard_predicates>0", "punned_triples>0",
         "empty_restrictions>0", "undeclared_properties_used>0",
         "file_scheme_namespaces>0", "visibility_ratio<0.99"]


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    d = tmp_path_factory.mktemp("mig")
    rep = mig.migrate(FIXTURE, d / "out.owl", d / "report.json", d / "ledger.json")
    g = rdflib.Graph()
    g.parse(str(d / "out.owl"))
    return {"dir": d, "report": rep, "graph": g,
            "ledger": json.loads((d / "ledger.json").read_text())}


def _restrictions(g, subject):
    out = set()
    for r in g.objects(URIRef(W + subject), RDFS.subClassOf):
        if (r, RDF.type, OWL.Restriction) in g:
            for q in (OWL.someValuesFrom, OWL.hasValue, OWL.allValuesFrom):
                f = g.value(r, q)
                if f is not None:
                    out.add((str(g.value(r, OWL.onProperty)), q.split("#")[-1], str(f)))
    return out


def test_qs_c1_fixture_has_every_defect():
    a = ontology_audit.audit(str(FIXTURE))
    assert a["file_scheme_namespaces"]
    assert a["mangled_standard_predicates"] == 2
    assert set(a["malformed_breakdown"]) == {
        "whitespace_or_illegal_char", "curie_in_fragment",
        "upper_ontology_id_in_local_ns"}
    assert a["punned_triples"] > 0


def test_qs_c1_per_step_counts(migrated):
    steps = migrated["report"]["steps"]
    assert steps["1_namespace_unification"]["total"] > 0
    assert steps["2_mangled_predicates"]["rdfs:subClassOf"] == 1
    assert steps["2_mangled_predicates"]["subClassOf"] == 1  # owl:subClassOf
    assert steps["3_upper_ids_and_aliases"]["local_upper_id"] == 1
    assert steps["3_upper_ids_and_aliases"]["RO_0000057->BFO_0000057"] == 1
    assert steps["3_upper_ids_and_aliases"]["BFO_0000050->BFO_0000176"] == 1
    assert steps["4_restriction_text"] == {"restored": 1, "total": 1}
    assert steps["5_sentence_iris"] == {"to_comment": 1, "total": 1}
    assert steps["6_punned_triples"] == {
        "class_class_to_some": 1, "class_individual_to_hasValue": 1,
        "refused_I_C": 1, "total": 3}
    assert steps["7_bnode_label_iris"] == {"dropped": 1, "recovered": 1, "total": 2}


def test_qs_c1_namespace_unified(migrated):
    g = migrated["graph"]
    assert not [t for t in g.all_nodes() if str(t).startswith("file:")]
    assert (URIRef(W.rstrip("#")), RDF.type, OWL.Ontology) in g
    # nested working#file:///...#Judge collapses onto working#Judge
    assert (URIRef(W + "Court"), RDFS.seeAlso, URIRef(W + "Judge")) in g
    assert (URIRef(W + "Court"), RDFS.subClassOf, URIRef(W + "Institution")) in g


def test_qs_c1_upper_ids_and_aliases(migrated):
    g = migrated["graph"]
    assert (URIRef(W + "Institution"), RDFS.subClassOf, URIRef(OBO + "BFO_0000040")) in g
    assert (URIRef(W + "JudicialRole"), RDFS.subClassOf, URIRef(OBO + "BFO_0000023")) in g
    assert not [t for t in g.all_nodes() if "RO_0000052" in str(t) or "RO_0000057" in str(t)]


def test_qs_c1_restriction_text_restored(migrated):
    assert (OBO + "BFO_0000197", "someValuesFrom", W + "Judge") in \
        _restrictions(migrated["graph"], "JudicialRole")


def test_qs_c1_sentence_iri_becomes_comment(migrated):
    g = migrated["graph"]
    lits = [str(o) for o in g.objects(URIRef(W + "Judge"), RDFS.comment)]
    assert lits == ["A judge is an officer who decides cases"]
    assert any(p == URIRef(mig.rv.BFOAGENT_NS + "migratedFrom") for p in g.predicates())


def test_qs_c1_punned_triples_per_qs_a3_table(migrated):
    g, ledger = migrated["graph"], migrated["ledger"]
    assert (OBO + "BFO_0000057", "someValuesFrom", W + "Judge") in \
        _restrictions(g, "Adjudication")
    assert (OBO + "BFO_0000178", "hasValue", W + "JusticeAlpha") in _restrictions(g, "Court")
    # class->class restorations carry the quantifierDefaulted annotation
    qd = URIRef(mig.rv.BFOAGENT_NS + "quantifierDefaulted")
    annotated = {str(g.value(a, OWL.annotatedSource)) for a in g.subjects(qd, None)}
    assert W + "Adjudication" in annotated
    # individual->individual kept as a property assertion
    assert (URIRef(W + "JusticeAlpha"), URIRef(OBO + "BFO_0000176"),
            URIRef(W + "SupremeCourt")) in g
    # individual->class refused: ledgered, not stored raw
    assert not list(g.triples((URIRef(W + "JusticeAlpha"), URIRef(OBO + "BFO_0000056"), None)))
    assert any(e["step"] == "6_punned_triples" and e["object"].endswith("#Adjudication")
               for e in ledger)


def test_qs_c1_bnode_labels(migrated):
    g, ledger = migrated["graph"], migrated["ledger"]
    assert (OBO + "BFO_0000196", "someValuesFrom", W + "JudicialRole") in \
        _restrictions(g, "Judge")
    assert any(e["step"] == "7_bnode_label_iris" and e["object"].endswith("#_:r1")
               for e in ledger)


def test_qs_c1_output_passes_qs_g3_gates(migrated):
    a = ontology_audit.audit(str(migrated["dir"] / "out.owl"))
    assert ontology_audit.check_gates(a, QS_G3) == []
    assert a["visibility_ratio"] == 1.0


def test_qs_c1_input_is_read_only(tmp_path):
    before = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    mig.migrate(FIXTURE, tmp_path / "o.owl", do_verify=False)
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == before


def test_qs_c1_cli_refuses_in_place(tmp_path):
    src = tmp_path / "w.owl"
    src.write_bytes(FIXTURE.read_bytes())
    p = subprocess.run([sys.executable, str(ROOT / "scripts/migrate_invisible_axioms.py"),
                        str(src), str(src)], capture_output=True, text=True)
    assert p.returncode != 0 and "read-only" in p.stderr


def test_qs_c2_idempotent_rerun_is_byte_identical(tmp_path):
    """Two runs in separate processes (different hash seeds) and a run over
    the output all produce the same bytes."""
    script = str(ROOT / "scripts/migrate_invisible_axioms.py")
    outs = []
    for seed, src, name in (("1", FIXTURE, "a"), ("2", FIXTURE, "b"),
                            ("3", tmp_path / "a.owl", "c")):
        subprocess.run([sys.executable, script, str(src), str(tmp_path / f"{name}.owl")],
                       check=True, capture_output=True,
                       env={**os.environ, "PYTHONHASHSEED": seed})
        outs.append((tmp_path / f"{name}.owl").read_bytes())
    assert outs[0] == outs[1] == outs[2]


def test_qs_c3_verify_and_faithful_gating(tmp_path, monkeypatch):
    """--verify records the certificate, and a restoration on an unsat class
    is kept out of the coherent file and ledgered, never silently dropped."""
    # the Adjudication restoration's restriction (a property declaration
    # elsewhere in the file uses rdf:about, so this matches only the axiom)
    restored = b'<owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/BFO_0000057"/>'

    def fake_verify(owl_bytes, bfo_path, fol=True):
        # Adjudication is unsat only while its restored restriction is
        # present, i.e. the restoration (not the base text) causes the clash.
        unsat = [W + "Adjudication"] if restored in owl_bytes else []
        return {"hermit": {"consistent": True, "ok": not unsat, "unsat_classes": unsat}}

    monkeypatch.setattr(mig, "verify", fake_verify)
    rep = mig.migrate(FIXTURE, tmp_path / "o.owl", tmp_path / "r.json",
                      tmp_path / "l.json", do_verify=True)
    assert rep["gated_out"] == 1
    assert rep["unsat_source"] == 0
    assert rep["verify"]["hermit"]["unsat_classes"] == []
    assert rep["verify_before_gating"]["hermit"]["unsat_classes"] == [W + "Adjudication"]
    g = rdflib.Graph()
    g.parse(str(tmp_path / "o.owl"))
    assert _restrictions(g, "Adjudication") == set()
    ledger = json.loads((tmp_path / "l.json").read_text())
    assert any("unsatisfiable" in e["reason"] and e["subject"] == W + "Adjudication"
               for e in ledger)
    a = ontology_audit.audit(str(tmp_path / "o.owl"))
    assert ontology_audit.check_gates(a, QS_G3) == []


def test_qs_c3_isolate_clashes_finds_the_single_offender(monkeypatch):
    """The chunk/bisect search excludes exactly the restorations whose
    presence makes a consistent base inconsistent, and keeps the rest."""
    m = mig.Migration(FIXTURE)
    m.run()
    assert len(m.migration_axioms) >= 3
    bad_subject = W + "Court"  # the hasValue restoration

    def fake_consistent(owl_bytes, bfo_path):
        return b"<owl:hasValue" not in owl_bytes

    monkeypatch.setattr(mig, "hermit_consistent", fake_consistent)
    bad, stats = mig.isolate_clashes(m, Path("unused"))
    assert stats["base_consistent"] is True
    assert [u["axiom"]["subject"] for u in bad if u["kind"] == "restoration"] == [bad_subject]
    assert len(bad) == 1


def test_qs_c3_pre_existing_inconsistency_is_not_gated(monkeypatch):
    m = mig.Migration(FIXTURE)
    m.run()
    monkeypatch.setattr(mig, "hermit_consistent", lambda b, p: False)
    bad, stats = mig.isolate_clashes(m, Path("unused"))
    assert bad == [] and stats["base_consistent"] is False


def test_qs_c3_gated_by_default_fixture_is_consistent(migrated):
    """The default (gated) run certifies the written file with HermiT."""
    h = migrated["report"]["verify"]["hermit"]
    assert h["consistent"] is True and h["unsat_classes"] == []


def test_qs_c3_inconsistent_change_is_ledgered_not_written(tmp_path, monkeypatch):
    """A migration change that makes the ontology inconsistent is found,
    kept out of the written file and ledgered (QS-C3 / FG-0)."""
    clash = b'<owl:hasValue rdf:resource="http://davidkoepsell.com/bfo-agent/working#JusticeAlpha"/>'

    def consistent(b):
        return clash not in b

    monkeypatch.setattr(mig, "hermit_consistent", lambda b, p: consistent(b))
    monkeypatch.setattr(mig, "verify", lambda b, p, fol=True: {
        "hermit": {"consistent": consistent(b), "ok": consistent(b), "unsat_classes": []}})
    rep = mig.migrate(FIXTURE, tmp_path / "o.owl", tmp_path / "r.json", tmp_path / "l.json")
    assert rep["gating_search"]["base_consistent"] is True
    assert rep["gated_out"] == 1
    assert rep["deployable"] is True
    assert clash not in (tmp_path / "o.owl").read_bytes()
    assert _restrictions(_graph(tmp_path / "o.owl"), "Court") == set()
    ledger = json.loads((tmp_path / "l.json").read_text())
    assert any("inconsistent" in e["reason"] and e["subject"] == W + "Court"
               for e in ledger)


def test_qs_c3_rewritten_triple_can_be_gated():
    """Not only restorations: a rewritten IRI (step 3) that imports a clash
    is a gating unit too."""
    target = (URIRef(W + "Institution"), RDFS.subClassOf, URIRef(OBO + "BFO_0000040"))
    m = mig.Migration(FIXTURE)
    m.run()
    units, base = mig._gating_units(m)
    assert target not in base
    assert any(u["kind"] == "axiom" and target in u["triples"] for u in units)


def test_qs_c3_source_inconsistent_nothing_removed_not_deployable(tmp_path, monkeypatch):
    """FG-0: if the source's own axioms are inconsistent once visible, no
    source axiom is removed to force consistency; the output is written,
    marked source_inconsistent, not deployable, with culprits as evidence."""
    monkeypatch.setattr(mig, "hermit_consistent", lambda b, p: False)
    monkeypatch.setattr(mig, "verify", lambda b, p, fol=True: {
        "hermit": {"consistent": False, "ok": False, "unsat_classes": []}})
    rep = mig.migrate(FIXTURE, tmp_path / "o.owl", tmp_path / "r.json", tmp_path / "l.json")
    assert rep["source_inconsistent"] is True
    assert rep["deployable"] is False and rep["consistent"] is False
    assert rep["gated_out"] == 0
    assert rep["missing_source_axioms"] == 0
    assert (tmp_path / "o.owl").exists()
    ref = mig.migrate(FIXTURE, tmp_path / "u.owl", do_verify=False)
    assert rep["output_triples"] == ref["output_triples"]  # nothing removed
    ledger = json.loads((tmp_path / "l.json").read_text())
    assert rep["source_culprits"] >= 1
    assert any(e["step"] == "source_inconsistent" for e in ledger)


def test_qs_c3_text_axioms_are_never_gating_candidates():
    m = mig.Migration(FIXTURE)
    m.run()
    units, _ = mig._gating_units(m)
    text = [u for u in units if u["kind"] == "text"]
    assert text
    with pytest.raises(AssertionError):
        mig._exclude_units(m, text[:1], "x", {})


def test_qs_c3_gated_rewrite_reverts_to_source_form():
    """A rewrite of an axiom the source reasoner could already see is
    reverted, not deleted, when gated."""
    m = mig.Migration(FIXTURE)
    m.run()
    forms = mig._source_forms(m)
    units, _ = mig._gating_units(m)
    rewritten = [u for u in units if u["kind"] == "axiom"
                 and any(t in forms for t in u["triples"])]
    assert rewritten
    mig._exclude_units(m, rewritten[:1], "test", forms)
    assert mig.missing_source_axioms(m) == []


def test_qs_c3_unsat_roots_found_inside_subclass_cycles():
    """Classes in a subclass cycle are mutual ancestors; the root finder must
    still pick them (GeometryofTheGood had 149 such classes and gating
    stalled with no roots)."""
    m = mig.Migration(FIXTURE)
    m.run()
    a, b, c = (URIRef(W + x) for x in ("CycA", "CycB", "CycChild"))
    m.out |= {(a, RDFS.subClassOf, b), (b, RDFS.subClassOf, a), (c, RDFS.subClassOf, a)}
    units = [{"kind": "axiom", "triples": {(a, RDFS.subClassOf, b)}},
             {"kind": "axiom", "triples": {(c, RDFS.subClassOf, a)}},
             {"kind": "text", "triples": {(b, RDFS.subClassOf, a)}}]
    hit = mig._primary_unsat_units(m, units, {str(a), str(b), str(c)})
    assert hit == [units[0]]  # the cycle member's change; not the child, not text


def test_qs_c3_ancestor_change_blamed_when_root_has_only_source_axioms(monkeypatch):
    """GeometryofTheGood's Anticipation: the source puts it under process,
    and a migration rewrite makes its (satisfiable) parent a continuant. The
    parent's change is found and gated; the root's source axioms are not."""
    m = mig.Migration(FIXTURE)
    m.run()
    root, parent = URIRef(W + "Anticipation"), URIRef(W + "TemporalDimension")
    bad_edge = (parent, RDFS.subClassOf, URIRef(OBO + "BFO_0000002"))
    ok_edge = (parent, RDFS.label, rdflib.Literal("x"))
    m.out |= {bad_edge, (root, RDFS.subClassOf, parent)}
    units = [{"kind": "axiom", "triples": {bad_edge}},
             {"kind": "axiom", "triples": {(parent, RDFS.comment, rdflib.Literal("y"))}},
             {"kind": "text", "triples": {(root, RDFS.subClassOf, parent)}}]
    edge = b'rdf:about="http://davidkoepsell.com/bfo-agent/working#TemporalDimension">'
    cont = b'<rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000002"/>'

    def fake(b, p):
        probe = b"_qsProbe" in b
        return not (probe and cont in b.split(edge, 1)[-1].split(b"</rdf:Description>")[0])

    monkeypatch.setattr(mig, "hermit_consistent", fake)
    stats = {}
    hit = mig._ancestor_culprits(m, units, str(root), {str(parent)}, Path("unused"), stats)
    assert hit == [units[0]]
    assert ok_edge not in m.out


def test_qs_c3_gated_part_of_split_reverts_to_source_form():
    m = mig.Migration(FIXTURE)
    m.run()
    forms = mig._source_forms(m)
    split = (URIRef(W + "JusticeAlpha"), URIRef(OBO + "BFO_0000176"), URIRef(W + "SupremeCourt"))
    assert split in m.out and split in forms
    mig._exclude_units(m, [{"kind": "axiom", "triples": {split}}], "test", forms)
    assert mig.missing_source_axioms(m) == []


def _graph(path):
    g = rdflib.Graph()
    g.parse(str(path))
    return g
