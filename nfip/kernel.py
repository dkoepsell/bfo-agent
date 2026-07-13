"""Phase A: the Coverage Kernel (BFO-2020 grounded).

Implements coverage-kernel-spec.md: the reusable formal model that turns policy
wording into a reasoning-capable structure. Domain-content free. Every domain
ontology (e.g. the NFIP Dwelling Form) imports these classes and populates the
marker classes below.

The kernel is the proprietary asset. It never leaves the server; only findings do.

Key design:
  * Model the fact pattern, not the document. A Loss is run through the wording.
  * Coverage is bivalent:  CoveredLoss and UncoveredLoss are DISJOINT.
  * Coverage is a DEFINED class (sufficient conditions), so membership is
    entailed, so contradictions can actually fire.

Two proof shapes fall out (see nfip/proofs.py):
  Shape 1 (TBox): a coverage class is unsatisfiable  -> coverage that cannot pay.
  Shape 2 (ABox): one loss is CoveredLoss and UncoveredLoss -> inconsistent.
"""
from __future__ import annotations

import owlready2 as o2

# BFO 2020 obo IRIs (house style; see app/bfo_catalog.py). We declare the small
# backbone we anchor to rather than importing all of BFO, but reuse the canonical
# obo IRIs verbatim (no re-minted BFO IRIs, per anti-pattern PC in the spec).
OBO = "http://purl.obolibrary.org/obo/"
KERNEL_IRI = "https://sool.tamu.edu/coverage-kernel"

_BFO_BACKBONE = {
    # fragment: (parent_fragment_or_None, label)
    "BFO_0000001": (None, "entity"),
    "BFO_0000002": ("BFO_0000001", "continuant"),
    "BFO_0000003": ("BFO_0000001", "occurrent"),
    "BFO_0000015": ("BFO_0000003", "process"),
    "BFO_0000020": ("BFO_0000002", "specifically dependent continuant"),
    "BFO_0000031": ("BFO_0000002", "generically dependent continuant"),
    "BFO_0000017": ("BFO_0000020", "realizable entity"),
    "BFO_0000023": ("BFO_0000017", "role"),
}


def _bfo(world: o2.World):
    """Materialise the BFO-2020 backbone with canonical obo IRIs.

    Classes are created in an ontology whose base IRI is the obo prefix, so each
    class IRI comes out as http://purl.obolibrary.org/obo/BFO_xxxxxxx exactly
    (no re-minted BFO IRIs)."""
    bfo_onto = world.get_ontology(OBO)  # base_iri ends in '/', frag -> obo IRI
    made: dict[str, type] = {}
    with bfo_onto:
        for frag, (parent, label) in _BFO_BACKBONE.items():
            base = made[parent] if parent else o2.Thing
            cls = o2.types.new_class(frag, (base,))
            cls.label = [label]
            made[frag] = cls
    return made


def build_coverage_kernel(world: o2.World) -> o2.Ontology:
    """Build the coverage kernel in `world`; return the ontology.

    Marker classes (Grantedloss / ExcludedLoss / RestoredLoss) are the seam a
    domain ontology fills in by subclassing. Coverage is defined over them.
    """
    onto = world.get_ontology(KERNEL_IRI)
    bfo = _bfo(world)
    process = bfo["BFO_0000015"]
    sdc = bfo["BFO_0000020"]
    gdc = bfo["BFO_0000031"]
    role = bfo["BFO_0000023"]

    with onto:
        # --- Continuants: policy, clauses, defined terms -------------------
        class Policy(gdc):
            """A document act: executing it creates the indemnity obligation."""

        class Clause(gdc):
            """Information content entity, part of a Policy. Five subtypes."""

        class Grant(Clause):
            pass

        class Exclusion(Clause):
            pass

        class Carveback(Clause):
            """Defined relative to an Exclusion (see `modifies`)."""

        class Condition(Clause):
            pass

        class Definition(Clause):
            pass

        class DefinedTerm(gdc):
            pass

        # --- Roles and the deontic obligation ------------------------------
        class Insurer(role):
            pass

        class Insured(role):
            pass

        class IndemnityObligation(sdc):
            """Deontic dependent continuant. Inheres in the INSURER, never in
            the policy or the loss (anti-pattern: get the bearer right)."""

        class PaymentProcess(process):
            pass

        # --- The fact pattern: losses --------------------------------------
        class Loss(process):
            """The occurrent run through the wording."""

        # Marker classes the domain ontology populates by subclassing.
        class GrantedLoss(Loss):
            """Losses some Grant reaches."""

        class ExcludedLoss(Loss):
            """Losses some Exclusion removes."""

        class RestoredLoss(Loss):
            """Losses some Carveback restores."""

        # Derived coverage classes (DEFINED -> membership is entailed).
        class EffectivelyExcludedLoss(Loss):
            pass

        class CoveredLoss(Loss):
            pass

        class UncoveredLoss(Loss):
            pass

        # --- Object properties (kernel relations) --------------------------
        class inheresIn(o2.ObjectProperty):
            domain = [IndemnityObligation]
            range = [Insurer]

        class realizedBy(o2.ObjectProperty):
            domain = [IndemnityObligation]
            range = [PaymentProcess]

        class modifies(o2.ObjectProperty):
            """A Carveback always modifies a named Exclusion. Required."""
            domain = [Carveback]
            range = [Exclusion]

        class exhibits(o2.ObjectProperty):
            """A Loss exhibits factual features (peril, mechanism, location)."""
            domain = [Loss]

    # --- Class axioms (built after classes exist) --------------------------
    with onto:
        # Carveback interaction: a loss is effectively excluded iff an exclusion
        # removes it and no carveback restores it.
        EffectivelyExcludedLoss.equivalent_to = [
            Loss & ExcludedLoss & o2.Not(RestoredLoss)
        ]
        # A loss is covered iff a grant reaches it and it is not effectively
        # excluded. UncoveredLoss is a loss the wording affirmatively removes.
        CoveredLoss.equivalent_to = [
            Loss & GrantedLoss & o2.Not(EffectivelyExcludedLoss)
        ]
        UncoveredLoss.equivalent_to = [Loss & EffectivelyExcludedLoss]

        # THE PIVOT: coverage is bivalent. Natural to the domain, not imposed.
        o2.AllDisjoint([CoveredLoss, UncoveredLoss])

    return onto


def sanity_gate(verbose: bool = False) -> bool:
    """Reasoner sanity gate (spec 9.7): a trivial A disjoint B with x:A,x:B MUST
    be flagged inconsistent before any verdict is trusted. Returns True if the
    reasoner is trustworthy."""
    w = o2.World()
    onto = w.get_ontology("https://sool.tamu.edu/sanity")
    with onto:
        class A(o2.Thing):
            pass

        class B(o2.Thing):
            pass

        o2.AllDisjoint([A, B])
        x = A("x")
        x.is_a.append(B)
    try:
        o2.sync_reasoner(w, debug=0)
    except o2.OwlReadyInconsistentOntologyError:
        if verbose:
            print("sanity gate PASS: reasoner detected the trivial contradiction")
        return True
    if verbose:
        print("sanity gate FAIL: reasoner did NOT detect a trivial contradiction")
    return False
