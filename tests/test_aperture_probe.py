"""The precondition probe, and the guarantees it is supposed to give.

The probes are mechanical, so most of what follows is built from small synthetic
artifacts where the right answer is obvious by construction. Two properties get
the most attention, because they are the ones a wrong answer would quietly
corrupt every downstream verdict with:

* a precondition must never be satisfied by an axiom the merged upper ontology
  contributed;
* profile_dl must never report a pass it did not observe.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app import config
from app.aperture import binding, probe

BFO_PATH = str(config.BFO_PATH)

HEADER = """<?xml version="1.0"?>
<rdf:RDF xmlns="http://example.org/t#" xml:base="http://example.org/t"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:obo="http://purl.obolibrary.org/obo/">
  <owl:Ontology rdf:about="http://example.org/t"/>
"""
FOOTER = "</rdf:RDF>\n"


def write_owl(tmp_path: Path, body: str, name: str = "t.owl") -> str:
    p = tmp_path / name
    p.write_text(HEADER + textwrap.dedent(body) + FOOTER)
    return str(p)


# A vocabulary that declares classes and nothing else. This is the shape the
# strict formal_theory reading is about.
BARE = """
  <owl:Class rdf:about="http://example.org/t#Alpha"/>
  <owl:Class rdf:about="http://example.org/t#Beta"/>
"""

# The same vocabulary with domain and range only.
DOMAIN_RANGE_ONLY = """
  <owl:Class rdf:about="http://example.org/t#Alpha"/>
  <owl:Class rdf:about="http://example.org/t#Beta"/>
  <owl:ObjectProperty rdf:about="http://example.org/t#rel">
    <rdfs:domain rdf:resource="http://example.org/t#Alpha"/>
    <rdfs:range rdf:resource="http://example.org/t#Beta"/>
  </owl:ObjectProperty>
"""


@pytest.fixture
def bare(tmp_path):
    return write_owl(tmp_path, BARE)


def run_bare(path, **kw):
    kw.setdefault("bfo_path", BFO_PATH)
    kw.setdefault("robot_path", "definitely-not-a-real-binary")
    return probe.run(path, **kw)


# --------------------------------------------------------------------------
# Shape of the report
# --------------------------------------------------------------------------

def test_all_eight_preconditions_are_reported(bare):
    report = run_bare(bare)
    assert set(report.results) == set(probe.PRECONDITIONS)
    assert len(probe.PRECONDITIONS) == 8


def test_no_probe_returns_a_bare_boolean(bare):
    """Every probe carries the evidence that produced it."""
    report = run_bare(bare)
    for pid, result in report.results.items():
        assert isinstance(result.evidence, dict), pid
        assert result.evidence, pid


def test_report_records_the_artifact_digest(bare):
    report = run_bare(bare)
    assert len(report.artifact_sha256) == 64
    assert report.probe_version == probe.PROBE_VERSION


def test_unsatisfied_returns_the_first_in_order(bare):
    report = run_bare(bare)
    first = report.unsatisfied(["individuals", "formal_theory", "definitions"])
    assert first == "individuals"


def test_unknown_counts_as_unsatisfied(bare):
    """profile_dl is unknown without ROBOT, and the resolver must not read that
    as satisfied."""
    report = run_bare(bare)
    assert report.satisfied("profile_dl") is None
    assert report.unsatisfied(["profile_dl"]) == "profile_dl"


# --------------------------------------------------------------------------
# formal_theory, and ADJUDICATE item 2
# --------------------------------------------------------------------------

def test_bare_vocabulary_is_not_a_formal_theory(bare):
    result = run_bare(bare).results["formal_theory"]
    assert result.satisfied is False
    assert result.evidence["construct_total"] == 0


def test_domain_and_range_alone_fail_the_strict_reading(tmp_path):
    path = write_owl(tmp_path, DOMAIN_RANGE_ONLY)
    result = run_bare(path, strict_formal_theory=True).results["formal_theory"]
    assert result.satisfied is False
    assert result.evidence["domain_range_total"] == 2
    assert result.evidence["reading"] == "strict"
    assert "domain and range" in result.note


def test_domain_and_range_alone_pass_the_permissive_reading(tmp_path):
    path = write_owl(tmp_path, DOMAIN_RANGE_ONLY)
    result = run_bare(path, strict_formal_theory=False).results["formal_theory"]
    assert result.satisfied is True
    assert result.evidence["reading"] == "permissive"


def test_the_chosen_reading_travels_in_the_report(tmp_path):
    path = write_owl(tmp_path, DOMAIN_RANGE_ONLY)
    assert run_bare(path, strict_formal_theory=False).formal_theory_strict is False
    assert run_bare(path, strict_formal_theory=True).formal_theory_strict is True


def test_a_restriction_makes_it_a_theory(tmp_path):
    path = write_owl(tmp_path, """
      <owl:ObjectProperty rdf:about="http://example.org/t#rel"/>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf>
          <owl:Restriction>
            <owl:onProperty rdf:resource="http://example.org/t#rel"/>
            <owl:someValuesFrom rdf:resource="http://example.org/t#Beta"/>
          </owl:Restriction>
        </rdfs:subClassOf>
      </owl:Class>
    """)
    result = run_bare(path).results["formal_theory"]
    assert result.satisfied is True
    assert result.evidence["constructs"]["someValuesFrom"] == 1


def test_a_property_characteristic_makes_it_a_theory(tmp_path):
    path = write_owl(tmp_path, """
      <owl:ObjectProperty rdf:about="http://example.org/t#partOf">
        <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#TransitiveProperty"/>
      </owl:ObjectProperty>
    """)
    result = run_bare(path).results["formal_theory"]
    assert result.satisfied is True
    assert result.evidence["constructs"]["TransitiveProperty"] == 1


# --------------------------------------------------------------------------
# The merged closure must not satisfy a precondition on the artifact's behalf
# --------------------------------------------------------------------------

def test_bfo_does_not_satisfy_formal_theory_for_a_bare_artifact(bare):
    """BFO is full of restrictions and disjointness axioms. Merging it must not
    make a bare vocabulary look like a theory."""
    with_bfo = run_bare(bare, bfo_path=BFO_PATH).results["formal_theory"]
    without = run_bare(bare, bfo_path=None).results["formal_theory"]
    assert with_bfo.satisfied is False
    assert without.satisfied is False
    assert with_bfo.evidence["construct_total"] == without.evidence["construct_total"] == 0


def test_bfo_does_not_satisfy_definitions_for_a_bare_artifact(bare):
    assert run_bare(bare, bfo_path=BFO_PATH).results["definitions"].satisfied is False


def test_bfo_does_not_satisfy_complement_available_for_a_bare_artifact(bare):
    result = run_bare(bare, bfo_path=BFO_PATH).results["complement_available"]
    assert result.satisfied is False
    assert result.evidence["complement_of"] == 0


def test_bfo_does_not_satisfy_acts_for_a_bare_artifact(bare):
    """BFO declares process itself. An artifact that anchors nothing to it has
    no acts, and merging BFO must not change that."""
    assert run_bare(bare, bfo_path=BFO_PATH).results["acts"].satisfied is False


# --------------------------------------------------------------------------
# definitions
# --------------------------------------------------------------------------

def test_equivalent_class_satisfies_definitions(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <owl:equivalentClass rdf:resource="http://example.org/t#Beta"/>
      </owl:Class>
    """)
    result = run_bare(path).results["definitions"]
    assert result.satisfied is True
    assert result.evidence["equivalent_class"] == 1


MIXED_IRI_FORMS = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="http://example.org/t"/>
  <owl:Class rdf:about="file:///lib/working.owl#Alpha"/>
  <owl:Class rdf:about="file:///lib/working.owl#Beta"/>
  <owl:Class rdf:about="http://example.org/t#Alpha">
    <owl:equivalentClass rdf:resource="http://example.org/t#Beta"/>
  </owl:Class>
</rdf:RDF>
"""


def test_definitions_are_not_lost_when_iri_forms_disagree(tmp_path):
    """Regression. SOoL_v1 declares its classes under file:// IRIs while its
    equivalentClass axioms use http:// ones. Intersecting the two sets reported
    two real definitions as zero, which would have put Stratum B out of scope on
    a false basis."""
    path = tmp_path / "mixed.owl"
    path.write_text(MIXED_IRI_FORMS)
    result = run_bare(str(path)).results["definitions"]
    assert result.satisfied is True
    assert result.evidence["equivalent_class"] == 1
    assert result.evidence["defined_but_undeclared"] == 0


def test_a_definition_on_an_undeclared_class_still_counts(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <rdf:Description rdf:about="http://example.org/t#Ghost">
        <owl:equivalentClass rdf:resource="http://example.org/t#Beta"/>
      </rdf:Description>
    """)
    result = run_bare(path).results["definitions"]
    assert result.satisfied is True
    assert result.evidence["defined_but_undeclared"] == 1


def test_prose_definitions_without_a_logical_counterpart_are_counted(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <obo:IAO_0000115>A thing of some kind.</obo:IAO_0000115>
      </owl:Class>
    """)
    result = run_bare(path).results["definitions"]
    assert result.satisfied is False
    assert result.evidence["prose_without_logical_counterpart"] == 1
    assert "prose definition" in result.note


# --------------------------------------------------------------------------
# complement_available
# --------------------------------------------------------------------------

def test_complement_of_satisfies_complement_available(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <owl:Class rdf:about="http://example.org/t#NotBeta">
        <owl:equivalentClass>
          <owl:Class>
            <owl:complementOf rdf:resource="http://example.org/t#Beta"/>
          </owl:Class>
        </owl:equivalentClass>
      </owl:Class>
    """)
    assert run_bare(path).results["complement_available"].satisfied is True


# --------------------------------------------------------------------------
# upper_ontology
# --------------------------------------------------------------------------

def test_upper_ontology_names_which_one_and_how_much_is_anchored(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000040"/>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
    """)
    result = run_bare(path).results["upper_ontology"]
    assert result.satisfied is True
    assert result.evidence["referenced"] == ["BFO"]
    assert result.evidence["named_classes"] == 2
    assert result.evidence["classes_directly_anchored"] == 1
    assert result.evidence["anchored_pct"] == 50.0


def test_upper_ontology_unsatisfied_when_nothing_is_referenced(bare):
    result = run_bare(bare).results["upper_ontology"]
    assert result.satisfied is False
    assert result.evidence["referenced"] == []


# --------------------------------------------------------------------------
# bearer_relations
# --------------------------------------------------------------------------

def test_declared_but_unused_bearer_property_does_not_satisfy(tmp_path):
    """Declaring 'inheres in' is not asserting a dependence. The distinction is
    the whole point of the precondition."""
    path = write_owl(tmp_path, """
      <owl:ObjectProperty rdf:about="http://purl.obolibrary.org/obo/RO_0000052"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
    """)
    result = run_bare(path).results["bearer_relations"]
    assert result.satisfied is False
    assert result.evidence["declared_but_unused"] == ["RO_0000052"]


def test_asserted_bearer_relation_satisfies(tmp_path):
    path = write_owl(tmp_path, """
      <owl:ObjectProperty rdf:about="http://purl.obolibrary.org/obo/RO_0000052"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <owl:NamedIndividual rdf:about="http://example.org/t#a">
        <rdf:type rdf:resource="http://example.org/t#Alpha"/>
        <obo:RO_0000052 rdf:resource="http://example.org/t#b"/>
      </owl:NamedIndividual>
      <owl:NamedIndividual rdf:about="http://example.org/t#b"/>
    """)
    result = run_bare(path).results["bearer_relations"]
    assert result.satisfied is True
    assert result.evidence["asserted"]["RO_0000052"] == 1


# --------------------------------------------------------------------------
# acts and individuals
# --------------------------------------------------------------------------

def test_a_process_subclass_satisfies_acts(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Diagnosis">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000015"/>
      </owl:Class>
    """)
    result = run_bare(path).results["acts"]
    assert result.satisfied is True
    assert result.evidence["occurrent_classes"] == 1


def test_a_continuant_subclass_does_not_satisfy_acts(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Patient">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000040"/>
      </owl:Class>
    """)
    assert run_bare(path).results["acts"].satisfied is False


def test_individuals_counted(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <owl:NamedIndividual rdf:about="http://example.org/t#a">
        <rdf:type rdf:resource="http://example.org/t#Alpha"/>
      </owl:NamedIndividual>
    """)
    result = run_bare(path).results["individuals"]
    assert result.satisfied is True
    assert result.evidence["named_individuals"] == 1


# --------------------------------------------------------------------------
# profile_dl is a hard gate and never assumes a pass
# --------------------------------------------------------------------------

def test_missing_robot_reports_unknown_not_satisfied(bare):
    result = run_bare(bare, robot_path="definitely-not-a-real-binary").results["profile_dl"]
    assert result.satisfied is None
    assert result.unknown is True
    assert result.evidence["checker"] is None
    assert "could not be verified" in result.note


def test_robot_timeout_reports_unknown(bare, monkeypatch):
    import subprocess

    monkeypatch.setattr(probe.shutil, "which", lambda p: "/usr/bin/robot")

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="robot", timeout=1)

    monkeypatch.setattr(probe.subprocess, "run", boom)
    result = probe.run(bare, bfo_path=BFO_PATH, robot_timeout=1).results["profile_dl"]
    assert result.satisfied is None
    assert "unverified" in result.note


def test_robot_violations_are_reported_by_kind(bare, monkeypatch):
    class Proc:
        returncode = 1
        stderr = ""

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("--output") + 1])
        out.write_text(
            "ERROR: Use of undeclared class: http://example.org/t#Alpha\n"
            "ERROR: Use of undeclared class: http://example.org/t#Beta\n"
            "ERROR: Punning is not allowed here\n"
        )
        return Proc()

    monkeypatch.setattr(probe.shutil, "which", lambda p: "/usr/bin/robot")
    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.run(bare, bfo_path=BFO_PATH).results["profile_dl"]
    assert result.satisfied is False
    assert result.evidence["violations_total"] == 3
    assert result.evidence["violations_by_kind"]["Use of undeclared class"] == 2
    assert "outside OWL 2 DL" in result.note


def test_clean_robot_run_satisfies(bare, monkeypatch):
    class Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        Path(cmd[cmd.index("--output") + 1]).write_text("")
        return Proc()

    monkeypatch.setattr(probe.shutil, "which", lambda p: "/usr/bin/robot")
    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.run(bare, bfo_path=BFO_PATH).results["profile_dl"]
    assert result.satisfied is True
    assert result.evidence["violations_total"] == 0


IMPORTING = """<?xml version="1.0"?>
<rdf:RDF xmlns="http://example.org/t#" xml:base="http://example.org/t"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="http://example.org/t">
    <owl:imports rdf:resource="http://purl.obolibrary.org/obo/bfo.owl"/>
  </owl:Ontology>
  <owl:Class rdf:about="http://example.org/t#Alpha"/>
</rdf:RDF>
"""


def test_robot_never_sees_an_imports_triple(tmp_path, monkeypatch):
    """The network stays off. ROBOT validates the closure we already resolved
    locally, with owl:imports stripped so there is nothing to dereference."""
    import rdflib
    from rdflib import OWL

    path = tmp_path / "importing.owl"
    path.write_text(IMPORTING)

    # The artifact really does carry an imports triple, so the assertion below
    # is about the stripping rather than about an absence.
    source = rdflib.Graph()
    source.parse(str(path))
    assert len(list(source.triples((None, OWL.imports, None)))) == 1

    seen = {}

    class Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        g = rdflib.Graph()
        g.parse(cmd[cmd.index("--input") + 1])
        seen["imports"] = len(list(g.triples((None, OWL.imports, None))))
        seen["classes"] = len(list(g.subjects(rdflib.RDF.type, OWL.Class)))
        Path(cmd[cmd.index("--output") + 1]).write_text("")
        return Proc()

    monkeypatch.setattr(probe.shutil, "which", lambda p: "/usr/bin/robot")
    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.run(str(path), bfo_path=BFO_PATH)

    assert seen["imports"] == 0
    # BFO came from the local merge, not from dereferencing the import.
    assert seen["classes"] > 1


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------

def test_cache_is_reused_when_the_artifact_is_unchanged(bare, tmp_path):
    cache = tmp_path / "probe.json"
    first = probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                             robot_path="definitely-not-a-real-binary")
    assert cache.exists()
    marker = json.loads(cache.read_text())
    marker["preconditions"]["individuals"]["evidence"]["named_individuals"] = 999
    cache.write_text(json.dumps(marker))

    second = probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                              robot_path="definitely-not-a-real-binary")
    assert second.results["individuals"].evidence["named_individuals"] == 999
    assert first.artifact_sha256 == second.artifact_sha256


def test_cache_is_discarded_when_the_artifact_changes(bare, tmp_path):
    cache = tmp_path / "probe.json"
    probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                     robot_path="definitely-not-a-real-binary")
    Path(bare).write_text(HEADER + textwrap.dedent("""
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <owl:NamedIndividual rdf:about="http://example.org/t#a">
        <rdf:type rdf:resource="http://example.org/t#Alpha"/>
      </owl:NamedIndividual>
    """) + FOOTER)
    again = probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                             robot_path="definitely-not-a-real-binary")
    assert again.results["individuals"].satisfied is True


def test_cache_is_discarded_when_the_probe_version_changes(bare, tmp_path, monkeypatch):
    cache = tmp_path / "probe.json"
    probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                     robot_path="definitely-not-a-real-binary")
    monkeypatch.setattr(probe, "PROBE_VERSION", "9.9.9")
    report = probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                              robot_path="definitely-not-a-real-binary")
    assert report.probe_version == "9.9.9"


def test_corrupt_cache_is_recomputed(bare, tmp_path):
    cache = tmp_path / "probe.json"
    cache.write_text("{not json")
    report = probe.run_cached(bare, cache, bfo_path=BFO_PATH,
                              robot_path="definitely-not-a-real-binary")
    assert report.results["formal_theory"].satisfied is False


# --------------------------------------------------------------------------
# The binding adapter
# --------------------------------------------------------------------------

def test_binding_reports_every_locus(bare):
    from app import recognition as rec

    result = binding.bind(probe.load_closure(bare, BFO_PATH))
    assert set(result.counts) == {locus.value for locus in rec.LOCUS_ORDER}


def test_an_unanchored_vocabulary_binds_nowhere(bare):
    result = binding.bind(probe.load_closure(bare, BFO_PATH))
    assert sum(result.counts.values()) == 0
    assert result.unanchored_classes == 2
    for locus in result.counts:
        assert result.binds(locus) is False


def test_a_process_class_binds_at_the_act_locus(tmp_path):
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Diagnosis">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000015"/>
      </owl:Class>
    """)
    result = binding.bind(probe.load_closure(path, BFO_PATH))
    assert result.binds("act") is True
    assert "http://example.org/t#Diagnosis" in result.per_locus["act"]


def test_binding_is_permissive_across_admissible_loci(tmp_path):
    """A process is admissible at act, facts and remedy. The adapter reports all
    three rather than picking one, which makes the Phase 2 warning conservative."""
    path = write_owl(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Diagnosis">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000015"/>
      </owl:Class>
    """)
    result = binding.bind(probe.load_closure(path, BFO_PATH))
    bound = {locus for locus, n in result.counts.items() if n}
    assert {"act", "facts", "remedy"} <= bound
    assert "criteria" not in bound


def test_binding_digest_changes_with_the_artifact(tmp_path):
    a = write_owl(tmp_path, BARE, name="a.owl")
    b = write_owl(tmp_path, DOMAIN_RANGE_ONLY, name="b.owl")
    da = binding.bind(probe.load_closure(a, BFO_PATH)).input_digest
    db = binding.bind(probe.load_closure(b, BFO_PATH)).input_digest
    assert da != db
    assert da.startswith("sha256:")


def test_binding_digest_changes_when_the_chain_anchors_change(bare, monkeypatch):
    from app import recognition as rec

    closure = probe.load_closure(bare, BFO_PATH)
    before = binding.bind(closure).input_digest
    trimmed = tuple(
        rec.LocusSpec(s.locus, s.name, s.gloss, ("BFO_9999999",), s.anchor_gloss)
        for s in rec.CHAIN
    )
    monkeypatch.setattr(rec, "CHAIN", trimmed)
    assert binding.bind(closure).input_digest != before
