"""The chain declaration: what it refuses, and why.

The declaration is the one place a human judgment enters the calculus. Most of
these tests are about the three refusals that keep it honest: an empty basis, an
incomplete chain, and a derived thickness that contradicts the declared one.
"""
from __future__ import annotations

import pytest

from app import recognition as rec
from app.aperture import chain
from app.aperture.chain import (
    ChainError,
    IncompleteChain,
    ThicknessContradiction,
)

FULL_LINKS = {
    "authority": "absent",
    "criteria": "performed",
    "assessor": "absent",
    "facts": "external",
    "act": "external",
    "effect": "performed",
    "remedy": "absent",
}
BASIS = "Scope and Technical Guide, sections 2 and 4; entry file annotations"


def block(**overrides):
    b = {"links": dict(FULL_LINKS), "basis": BASIS, "declared_by": "DRK",
         "declared_on": "2026-08-02"}
    b.update(overrides)
    return b


def validate(b=None, domain="clinical_nosology", **kw):
    kw.setdefault("engagement", "example-engagement")
    kw.setdefault("domain", domain)
    return chain.validate(b if b is not None else block(), **kw)


# --------------------------------------------------------------------------
# A well-formed declaration
# --------------------------------------------------------------------------

def test_a_complete_declaration_validates():
    d = validate()
    assert d.links == FULL_LINKS
    assert d.basis == BASIS
    assert d.engagement == "example-engagement"
    assert d.declared_on == "2026-08-02"


def test_all_seven_loci_are_required_by_the_vocabulary():
    assert set(chain.LOCI) == {locus.value for locus in rec.LOCUS_ORDER}
    assert len(chain.LOCI) == 7


def test_declared_on_defaults_to_today_when_absent():
    from datetime import date

    d = validate(block(declared_on=""))
    assert d.declared_on == date.today().isoformat()


def test_digest_is_stable_and_content_sensitive():
    a = validate()
    b = validate()
    assert a.digest == b.digest
    c = validate(block(basis=BASIS + " and the cover letter"))
    assert c.digest != a.digest


# --------------------------------------------------------------------------
# Refusal 1: the basis
# --------------------------------------------------------------------------

@pytest.mark.parametrize("basis", ["", "   ", "n/a", "see"])
def test_missing_or_token_basis_is_refused(basis):
    with pytest.raises(IncompleteChain) as e:
        validate(block(basis=basis))
    assert "basis is mandatory" in str(e.value)


def test_a_real_basis_is_accepted():
    assert validate(block(basis="Entry file annotations")).basis


# --------------------------------------------------------------------------
# Refusal 2: an incomplete chain
# --------------------------------------------------------------------------

def test_a_missing_link_is_refused():
    links = dict(FULL_LINKS)
    del links["assessor"]
    with pytest.raises(IncompleteChain) as e:
        validate(block(links=links))
    assert "assessor" in str(e.value)


def test_an_undeclared_link_does_not_default_to_absent():
    """The distinction the refusal protects: absent is a claim about the system,
    and defaulting to it would narrow the review's scope silently."""
    links = dict(FULL_LINKS)
    links["remedy"] = None
    with pytest.raises(IncompleteChain) as e:
        validate(block(links=links))
    assert "not 'absent'" in str(e.value)


def test_an_empty_links_map_is_refused():
    with pytest.raises(IncompleteChain):
        validate(block(links={}))


def test_an_unknown_locus_is_refused():
    links = dict(FULL_LINKS)
    links["adjudicator"] = "performed"
    with pytest.raises(ChainError) as e:
        validate(block(links=links))
    assert "unknown chain loci" in str(e.value)


def test_an_unknown_link_state_is_refused():
    links = dict(FULL_LINKS)
    links["act"] = "sometimes"
    with pytest.raises(ChainError) as e:
        validate(block(links=links))
    assert "exactly one of" in str(e.value)


def test_link_states_are_case_and_space_insensitive():
    links = dict(FULL_LINKS)
    links["criteria"] = "  Performed "
    assert validate(block(links=links)).links["criteria"] == "performed"


def test_a_bad_date_is_refused():
    with pytest.raises(ChainError) as e:
        validate(block(declared_on="2 August 2026"))
    assert "YYYY-MM-DD" in str(e.value)


# --------------------------------------------------------------------------
# Refusal 3: thickness is derived and cross-checked, never inferred
# --------------------------------------------------------------------------

def test_thickness_is_derived_from_the_links():
    d = validate()
    assert d.act_thickness == "thin"       # act: external
    assert d.repair == "none"              # remedy: absent
    assert d.has_chain is True
    assert d.stratum_d_thin is True


@pytest.mark.parametrize("link,expected", [
    ("performed", "thick"), ("external", "thin"), ("absent", "none"),
])
def test_act_thickness_mapping(link, expected):
    links = dict(FULL_LINKS, act=link)
    assert chain.derive_thickness(links)[0] == expected


@pytest.mark.parametrize("link,expected", [
    ("performed", "internal"), ("external", "external"), ("absent", "none"),
])
def test_repair_mapping(link, expected):
    links = dict(FULL_LINKS, remedy=link)
    assert chain.derive_thickness(links)[1] == expected


def test_contradiction_halts_and_reports_both():
    """clinical_nosology is declared act-thin. Declaring the act performed makes
    the derived thickness thick, and neither value is preferred."""
    links = dict(FULL_LINKS, act="performed")
    with pytest.raises(ThicknessContradiction) as e:
        validate(block(links=links), declared_act_thickness="thin")
    assert e.value.field == "act_thickness"
    assert e.value.derived == "thick"
    assert e.value.declared == "thin"
    assert "neither is preferred" in str(e.value)


def test_repair_contradiction_halts():
    links = dict(FULL_LINKS, remedy="performed")
    with pytest.raises(ThicknessContradiction) as e:
        validate(block(links=links), declared_repair="external")
    assert e.value.field == "repair"
    assert e.value.derived == "internal"


def test_agreement_does_not_halt():
    validate(block(), declared_act_thickness="thin", declared_repair="none")


# --------------------------------------------------------------------------
# Defaults are a suggestion, not a declaration
# --------------------------------------------------------------------------

def test_only_act_and_remedy_are_suggested():
    """Nothing in a domain profile records where the other five links sit
    relative to the artifact, so nothing is invented for them."""
    suggested = chain.suggest_links(rec.profile_for("clinical_nosology"))
    assert suggested["act"] == "external"
    assert suggested["remedy"] == "external"
    for locus in ("authority", "criteria", "assessor", "facts", "effect"):
        assert suggested[locus] is None


def test_a_suggestion_is_not_a_valid_declaration():
    suggested = chain.suggest_links(rec.profile_for("legal_order"))
    with pytest.raises(IncompleteChain):
        validate(block(links={k: v for k, v in suggested.items() if v}))


@pytest.mark.parametrize("key", sorted(rec.DOMAIN_PROFILES))
def test_suggestions_round_trip_against_every_domain_profile(key):
    """The suggestion and the derivation are inverses, so a suggested act and
    remedy can never trigger the contradiction halt on their own. This is what
    the Table 1 columns could not give: they contradict the stored thickness for
    the two recognition-only rows."""
    profile = rec.DOMAIN_PROFILES[key]
    suggested = chain.suggest_links(profile)
    links = dict(FULL_LINKS, act=suggested["act"], remedy=suggested["remedy"])
    d = validate(block(links=links),
                 domain=key,
                 declared_act_thickness=profile.act_thickness,
                 declared_repair=profile.repair)
    assert d.act_thickness == profile.act_thickness
    assert d.repair == profile.repair


def test_table_one_columns_would_have_contradicted_the_stored_thickness():
    """Why the naive backfill was dropped. The act column of a recognition-only
    institution is non-empty, so 'non-empty means performed' would derive thick
    where the profile says thin."""
    for key in ("clinical_nosology", "technical_certification"):
        profile = rec.DOMAIN_PROFILES[key]
        assert profile.act not in ("", "-")
        assert profile.act_thickness == "thin"
        assert chain.ACT_THICKNESS_FROM_LINK["performed"] != profile.act_thickness


# --------------------------------------------------------------------------
# The declaration drives the existing helpers
# --------------------------------------------------------------------------

def test_to_profile_matches_the_declared_thickness():
    profile = validate().to_profile()
    assert profile.act_thickness == "thin"
    assert profile.repair == "none"
    assert profile.stratum_d_thin is True


def test_a_chainless_declaration_switches_stratum_d_off():
    links = {locus: "absent" for locus in chain.LOCI}
    d = validate(block(links=links), domain="scientific_reference")
    assert d.has_chain is False
    assert rec.active_primitives(d.to_profile()) == tuple(
        c for c, p in rec.KERNEL.items() if p.stratum != "D")


# --------------------------------------------------------------------------
# The advisory cross-check warns and never overrides
# --------------------------------------------------------------------------

class FakeBinding:
    def __init__(self, counts, named=100):
        self.counts = counts
        self.named_classes = named


def test_performed_but_unbound_raises_an_advisory():
    d = validate()
    binding = FakeBinding({locus: 5 for locus in chain.LOCI} | {"criteria": 0})
    advisories = chain.cross_check(d, binding)
    kinds = {(a.kind, a.locus) for a in advisories}
    assert ("performed_but_unbound", "criteria") in kinds
    assert all(a.to_dict()["advisory"] is True for a in advisories)


def test_the_advisory_does_not_change_the_declaration():
    d = validate()
    binding = FakeBinding({locus: 0 for locus in chain.LOCI})
    chain.cross_check(d, binding)
    assert d.links["criteria"] == "performed"
    assert d.act_thickness == "thin"


def test_an_external_link_with_no_binding_is_not_flagged():
    """Only a performed claim is checkable against the vocabulary. An external
    link is by definition performed somewhere the artifact does not represent."""
    d = validate()
    binding = FakeBinding({locus: 0 for locus in chain.LOCI} | {"criteria": 3, "effect": 3})
    advisories = chain.cross_check(d, binding)
    assert [a.locus for a in advisories] == []


def test_a_stale_declaration_is_flagged():
    d = validate(block(artifact_sha256="a" * 64))
    binding = FakeBinding({locus: 9 for locus in chain.LOCI})
    advisories = chain.cross_check(d, binding, current_artifact_sha256="b" * 64)
    assert [a.kind for a in advisories] == ["declaration_predates_artifact"]


def test_a_current_declaration_is_not_flagged_as_stale():
    d = validate(block(artifact_sha256="a" * 64))
    binding = FakeBinding({locus: 9 for locus in chain.LOCI})
    assert chain.cross_check(d, binding, current_artifact_sha256="a" * 64) == []


# --------------------------------------------------------------------------
# status() never raises, so a half-finished editor state is showable
# --------------------------------------------------------------------------

def nosology_with_no_remedy():
    """clinical_nosology, with repair overridden to none by the operator.

    The spec's own example chain declares remedy absent, which derives repair
    none, while the stored profile says external. The override is the supported
    route: the operator narrows the profile, then declares the chain to match.
    """
    from dataclasses import replace

    return replace(rec.profile_for("clinical_nosology"), repair="none")


def test_status_reports_a_usable_declaration():
    profile = nosology_with_no_remedy()
    s = chain.status(block(), engagement="e", domain=profile.key, profile=profile)
    assert s["usable"] is True
    assert s["chain"]["act_thickness"] == "thin"
    assert s["chain"]["repair"] == "none"


def test_the_stored_profile_catches_a_chain_that_drops_the_remedy():
    """Without the override, declaring remedy absent against a profile whose
    repair is external is a contradiction, and it halts rather than picking one."""
    profile = rec.profile_for("clinical_nosology")
    s = chain.status(block(), engagement="e", domain=profile.key, profile=profile)
    assert s["usable"] is False
    assert s["detail"] == {"field": "repair", "derived": "none",
                           "declared": "external"}


def test_status_reports_incompleteness_without_raising():
    profile = nosology_with_no_remedy()
    s = chain.status(block(links={"act": "external"}), engagement="e",
                     domain=profile.key, profile=profile)
    assert s["usable"] is False
    assert s["reason"] == "incomplete"
    assert s["suggested_links"]["act"] == "external"


def test_status_reports_a_contradiction_without_raising():
    profile = nosology_with_no_remedy()
    s = chain.status(block(links=dict(FULL_LINKS, act="performed")),
                     engagement="e", domain=profile.key, profile=profile)
    assert s["usable"] is False
    assert s["reason"] == "thickness_contradiction"
    assert s["detail"] == {"field": "act_thickness", "derived": "thick",
                           "declared": "thin"}


def test_status_on_an_empty_block_is_undeclared_not_broken():
    profile = rec.profile_for("scientific_reference")
    s = chain.status({}, engagement="e", domain=profile.key, profile=profile)
    assert s["declared"] is False
    assert s["usable"] is False
