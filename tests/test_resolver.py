"""QS-A1 / QS-A2: the resolver and the malformed-IRI guard.

SPEC-bfo-agent-quality.md section 1 lists five strings the old resolver
silently concatenated onto the working namespace. Each must now resolve to a
real IRI or be refused on the write path, and every non-strict (read path)
fallback must be flagged by ``iri_is_malformed`` so it can never persist.
"""
import pytest

from app import owl_checks
from app.ontology_manager import WORKING_IRI, _resolve_iri

OBO = "http://purl.obolibrary.org/obo/"
W = WORKING_IRI + "#"


def strict(s):
    return _resolve_iri(s, WORKING_IRI, strict=True)


def test_qs_a1_bare_ro_id_resolves_to_canonical_bfo():
    # D-1: RO_0000052 (inheres in) is aliased to BFO 2020's BFO_0000197.
    assert strict("RO_0000052") == OBO + "BFO_0000197"


def test_qs_a1_bare_iao_id_resolves_to_obo():
    assert strict("IAO_0000115") == OBO + "IAO_0000115"


def test_qs_a1_blank_node_label_is_refused():
    with pytest.raises(ValueError):
        strict("_:r1")


def test_qs_a1_working_prefixed_curie_is_refused():
    with pytest.raises(ValueError):
        strict("working:rdfs:subClassOf")


def test_qs_a1_ro_prefix_resolves_and_aliases():
    assert strict("ro:RO_0000052") == OBO + "BFO_0000197"


@pytest.mark.parametrize("raw,expected", [
    ("BFO_0000040", OBO + "BFO_0000040"),
    ("bfo:BFO_0000040", OBO + "BFO_0000040"),
    ("obo:RO_0000087", OBO + "RO_0000087"),  # kept RO: has role
    ("iao:IAO_0000115", OBO + "IAO_0000115"),
    ("OBI_0000011", OBO + "OBI_0000011"),
    ("rdfs:subClassOf", "http://www.w3.org/2000/01/rdf-schema#subClassOf"),
    ("working:LegalRole", W + "LegalRole"),
    ("working:Legal Role", W + "LegalRole"),
    ("LegalRole", W + "LegalRole"),
    (OBO + "RO_0000053", OBO + "BFO_0000196"),
])
def test_qs_a1_known_forms_resolve(raw, expected):
    assert strict(raw) == expected


def test_qs_a1_unknown_prefix_is_refused():
    with pytest.raises(ValueError):
        strict("foo:Bar")


def test_qs_a5_file_iri_is_rebased_on_write():
    assert strict("file:///home/x/ontology/working.owl#Court") == W + "Court"


@pytest.mark.parametrize("raw", [
    "_:r1", "working:rdfs:subClassOf", "foo:Bar",
])
def test_qs_a2_read_path_fallbacks_are_flagged_malformed(raw):
    # Read paths keep the old non-raising behaviour; the guard catches it.
    iri = _resolve_iri(raw, WORKING_IRI)
    assert owl_checks.iri_is_malformed(iri)


@pytest.mark.parametrize("frag", [
    "rdfs:subClassOf",                         # CURIE pasted into a fragment
    "file:///home/x/working.owl#X",             # nested file:// IRI
    "_:r1",                                     # blank-node label
    "BFO_0000040", "RO_0000052", "IAO_0000115",  # locally minted upper ids
])
def test_qs_a2_malformed_local_fragments(frag):
    assert owl_checks.iri_is_malformed(W + frag)


def test_qs_a2_legacy_file_base_nested_iri_is_malformed():
    assert owl_checks.iri_is_malformed(
        "file:///home/x/working.owl#file:///home/x/working.owl#X")


@pytest.mark.parametrize("iri", [
    W + "LegalRole",
    OBO + "BFO_0000040",
    "http://www.w3.org/2002/07/owl#Thing",
    "file:///home/x/working.owl#LegalRole",  # legacy base, clean fragment
])
def test_qs_a2_clean_iris_pass(iri):
    assert not owl_checks.iri_is_malformed(iri)


def test_qs_a2_sool_owl_checks_shares_the_implementation():
    import sool_owl_checks
    assert sool_owl_checks.iri_is_malformed is owl_checks.iri_is_malformed


@pytest.mark.parametrize("text,is_sentence", [
    ("working:LegalRole", False),
    ("Legal Role", False),
    ("BFO_0000197 some working:Person", False),
    ("not working:Person", False),
    ("Law is the union of primary and secondary rules.", True),
    ("The court must recognize every valid claim", True),
])
def test_qs_a6_sentence_slot_detection(text, is_sentence):
    assert owl_checks.is_sentence_slot(text) is is_sentence
