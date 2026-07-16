"""Extract minimal, self-contained regression fixtures from the live ICD-11
artifact (real P2 + CONTRA defects) so CI reasons fast over BFO + a few classes.
Also builds a synthetic P1 fixture and a cascade fixture."""
from pathlib import Path
from rdflib import Graph, RDF, RDFS, OWL, URIRef, BNode, Literal, Namespace

ROOT = Path("/home/drkoepsell/projects/bfo-agent")
FIX = ROOT / "tests" / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)
OBO = "http://purl.obolibrary.org/obo/"
W = "http://davidkoepsell.com/bfo-agent/working#"

src = Graph(); src.parse(str(ROOT / "icd_cleanup/icd11bfo_v3.owl"))


def ln(u): return str(u).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def reachable(seeds):
    """All working-namespace class subjects + bnode subtrees + property subjects
    referenced (transitively) from the seed classes."""
    keep_nodes = set()
    frontier = list(seeds)
    while frontier:
        n = frontier.pop()
        if n in keep_nodes:
            continue
        keep_nodes.add(n)
        for p, o in src.predicate_objects(n):
            if isinstance(o, BNode):
                frontier.append(o)
            elif isinstance(o, URIRef) and str(o).startswith(W):
                frontier.append(o)
            # pull in the property used (for the corrupted BFO_0000196 override)
            if p == OWL.onProperty and isinstance(o, URIRef):
                frontier.append(o)
    return keep_nodes


def emit(seeds, path, ontology_iri):
    seeds = [URIRef(W + s) if not s.startswith("http") else URIRef(s) for s in seeds]
    keep = reachable(seeds)
    # also keep the property IRIs used anywhere in kept restrictions
    g = Graph()
    onto = URIRef(ontology_iri)
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, OWL.imports, URIRef("http://purl.obolibrary.org/obo/bfo.owl")))
    for n in keep:
        for p, o in src.predicate_objects(n):
            # drop dangling references to working classes we did not keep
            if isinstance(o, URIRef) and str(o).startswith(W) and o not in keep:
                continue
            g.add((n, p, o))
    # The realizable-sibling disjointness (Role/Disposition/Function pairwise
    # disjoint) is what makes the P2 clash bite. bfo.owl omits it; the ICD
    # artifact asserts it. Carry those axioms so the fixture reproduces the bug.
    realiz = {URIRef(OBO + f) for f in ("BFO_0000023", "BFO_0000016", "BFO_0000034")}
    for s, o in src.subject_objects(OWL.disjointWith):
        if s in realiz and o in realiz:
            g.add((s, OWL.disjointWith, o))
    g.serialize(destination=str(path), format="xml")
    print("wrote", path.name, "with", len(g), "triples")


# Real ICD-11 P2 case extracted verbatim: Creatine bears an EnergyShuttleRole
# (a Role) via the corrupted has-disposition property (BFO_0000196, range
# Disposition). Role<->Disposition clash -> Creatine unsatisfiable. R2 swaps the
# property to has-role, whose range (Role) matches the filler. (VitaminC's
# CofactorRole filler carries a *second*, independent defect -- it is itself
# unsatisfiable via a mis-minted inheres-in -- so VitaminC is a multi-defect
# cascade the detector flags but the P2 repair alone cannot heal; kept out of the
# clean P2 acceptance fixture.)
emit(["Creatine", "EnergyShuttleRole"],
     FIX / "rm_p2.owl", "http://davidkoepsell.com/bfo-agent/fixtures/p2")


def hand_built(path, ontology_iri, builder):
    g = Graph()
    onto = URIRef(ontology_iri)
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, OWL.imports, URIRef("http://purl.obolibrary.org/obo/bfo.owl")))
    # realizable-sibling disjointness (BFO-correct; bfo.owl omits it)
    role, disp, func = (URIRef(OBO + f) for f in ("BFO_0000023", "BFO_0000016", "BFO_0000034"))
    g.add((role, OWL.disjointWith, disp)); g.add((role, OWL.disjointWith, func))
    g.add((disp, OWL.disjointWith, func))
    builder(g)
    g.serialize(destination=str(path), format="xml")
    print("wrote", path.name, "with", len(g), "triples")


def _cls(g, iri, *supers):
    u = URIRef(iri); g.add((u, RDF.type, OWL.Class))
    for s in supers:
        g.add((u, RDFS.subClassOf, URIRef(s if s.startswith("http") else OBO + s)))
    return u


def _restr(g, cls, prop, filler):
    b = BNode()
    g.add((b, RDF.type, OWL.Restriction))
    g.add((b, OWL.onProperty, URIRef(prop if prop.startswith("http") else OBO + prop)))
    g.add((b, OWL.someValuesFrom, URIRef(filler)))
    g.add((cls, RDFS.subClassOf, b))


# P1: an SDC (disposition) class asserted to *bear* an independent continuant.
# bearer-of's domain is independent continuant, so the SDC subject is forced
# independent -> clashes with SDC (SDC disjoint independent continuant) -> the
# class is unsatisfiable. R1 retypes the restriction to inheres-in: the
# disposition inheres in its bearer rather than bearing it. (The live DSM P1
# case was already repaired; this is a minimal faithful reconstruction.)
def _p1(g):
    # bearer-of domain + Disposition/independent-continuant disjointness make the
    # misuse bite. This repo's bfo.owl is a flat stub (no BFO hierarchy/domains),
    # so the fixture states the BFO-correct axioms the clash needs, exactly as the
    # ICD working artifact does for the realizable-sibling disjointness.
    g.add((URIRef(OBO + "BFO_0000196"), RDF.type, OWL.ObjectProperty))
    g.add((URIRef(OBO + "BFO_0000196"), RDFS.domain, URIRef(OBO + "BFO_0000004")))
    g.add((URIRef(OBO + "BFO_0000016"), OWL.disjointWith, URIRef(OBO + "BFO_0000004")))
    metab = _cls(g, W + "MetabolicDisposition", "BFO_0000016")      # a Disposition (SDC)
    _cls(g, W + "Enzyme", "BFO_0000040")                            # a material entity (IC)
    # MetabolicDisposition bearer-of some Enzyme  <-- misuse
    _restr(g, metab, "BFO_0000196", W + "Enzyme")


hand_built(FIX / "rm_p1.owl", "http://davidkoepsell.com/bfo-agent/fixtures/p1", _p1)


# CONTRA: a realizable both realized-in some Process and complementOf(realized-in
# some Process) -- a self-contradictory placement. R4 drops the complement,
# keeping the source `realized in` restriction.
def _contra(g):
    g.add((URIRef(OBO + "BFO_0000054"), RDF.type, OWL.ObjectProperty))  # realized in
    proc = _cls(g, W + "MenstrualProcess", "BFO_0000015")               # a process
    disp = _cls(g, W + "CyclicDisposition", "BFO_0000016")              # a disposition
    _restr(g, disp, "BFO_0000054", W + "MenstrualProcess")              # realized in P
    comp = BNode(); inner = BNode()
    g.add((comp, RDF.type, OWL.Class))
    g.add((comp, OWL.complementOf, inner))
    g.add((inner, RDF.type, OWL.Restriction))
    g.add((inner, OWL.onProperty, URIRef(OBO + "BFO_0000054")))
    g.add((inner, OWL.someValuesFrom, URIRef(W + "MenstrualProcess")))
    g.add((disp, RDFS.subClassOf, comp))                               # NOT(realized in P)


hand_built(FIX / "rm_contra.owl", "http://davidkoepsell.com/bfo-agent/fixtures/contra", _contra)


# Cascade: a root P2 class (independent continuant bears a role via the corrupted
# has-disposition property) and a dependent defined by reference to the root.
# Repairing the root must heal the dependent without touching it directly.
def _cascade(g):
    # corrupt property: BFO_0000196 range Disposition (mirrors the ICD artifact)
    g.add((URIRef(OBO + "BFO_0000196"), RDF.type, OWL.ObjectProperty))
    g.add((URIRef(OBO + "BFO_0000196"), RDFS.range, URIRef(OBO + "BFO_0000016")))
    g.add((URIRef(OBO + "BFO_0000178"), RDF.type, OWL.ObjectProperty))
    role = _cls(g, W + "CatalyticRole", "BFO_0000023")
    root = _cls(g, W + "EnzymeCofactor", "BFO_0000040")             # material entity
    _restr(g, root, "BFO_0000196", W + "CatalyticRole")            # P2 misuse
    dep = _cls(g, W + "CofactorComplex", "BFO_0000040")
    _restr(g, dep, "BFO_0000178", W + "EnzymeCofactor")            # has continuant part


hand_built(FIX / "rm_cascade.owl", "http://davidkoepsell.com/bfo-agent/fixtures/cascade", _cascade)
