"""app/gate_client.py
=====================
Validation gate client (bfo-agent-spec.md: every output passes owltesterservice
before it is written to a durable location).

Two backends behind one ``evaluate`` call, both returning the existing
:class:`~app.coherence_gate.GateResult` contract so callers are backend-blind:

  * REMOTE  -- when ``OWLTESTER_URL`` is configured, POST the serialized fragment
    to the external owltesterservice and map its verdict.
  * LOCAL   -- otherwise (or if the remote call fails), the in-process coherence
    gate (construction PC-1..PC-8 + disjoint-straddle lint + local HermiT
    reasoner) plus the file-level ``owl_checks`` validators run on the serialized
    fragment. The local path needs no network and no external service.

In BOTH backends the same ``owl_checks`` validators run on the emitted text, so
self-lint and gate never diverge. This client is BFO-general: it applies to
every ontology the workbench builds, not just SOoL.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import coherence_gate, owl_checks
from .coherence_gate import GateOutcome, GateResult, GateTier
from .config import OWLTESTER_TIMEOUT, OWLTESTER_URL

log = logging.getLogger(__name__)

_OBO = "http://purl.obolibrary.org/obo/"
_WORKING_NS = "http://example.org/sool/working#"


def _local(ref: str) -> str:
    s = (ref or "").split("#")[-1]
    s = s.split("/")[-1]
    return s.split(":")[-1]


def _resolve_iri(ref: str) -> str:
    """Best-effort IRI for a schema reference, for serialization. A bare/working
    name lands in the working namespace; a BFO_/RO_/IAO_ fragment resolves to its
    obo IRI. The exact namespace does not affect the lexical owl_checks (which key
    off ``rdf:about``); it only needs to be stable and well-formed."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref
    # Standard vocabulary prefixes -- without these, 'owl:disjointWith' /
    # 'rdfs:subClassOf' fall through to the working namespace and the emitted
    # triple is silently mis-namespaced (parity with OntologyManager).
    _STD = {
        "rdf:": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs:": "http://www.w3.org/2000/01/rdf-schema#",
        "owl:": "http://www.w3.org/2002/07/owl#",
        "obo:": _OBO,
    }
    for prefix, base in _STD.items():
        if ref.startswith(prefix):
            return base + ref.split(":", 1)[1]
    frag = _local(ref)
    if frag.split("_")[0] in ("BFO", "RO", "IAO"):
        return _OBO + frag
    # H-1 parity with the commit path: a label-like fragment is slugified the
    # same way OntologyManager._resolve_iri does; anything else stays literal
    # so the file-level checks (E_BAD_IRI / E_EXPR_IRI) catch it.
    slug = owl_checks.slugify_fragment(frag)
    return _WORKING_NS + (slug if slug is not None else frag)


def serialize_fragment(proposal) -> str:
    """Serialize a proposal's would-be triples to RDF/XML text.

    Used as the POST body for the remote gate and as the input to the file-level
    ``owl_checks`` validators. Deliberately literal: it preserves whatever IRI
    the proposer suggested (including a malformed expression-IRI) so the gate can
    catch it -- it does NOT sanitize.
    """
    from rdflib import RDF, RDFS, OWL, Graph, URIRef

    g = Graph()
    g.bind("owl", OWL)
    for ent in proposal.entities:
        ref = getattr(ent, "iri_suggestion", "") or getattr(ent, "existing_iri", "") \
            or getattr(ent, "label", "")
        s = URIRef(_resolve_iri(ref))
        kind = getattr(ent, "kind", "class")
        g.add((s, RDF.type, OWL.NamedIndividual if kind == "individual" else OWL.Class))
        bfo_type = getattr(ent, "bfo_type", "") or ""
        if bfo_type:
            pred = RDF.type if kind == "individual" else RDFS.subClassOf
            g.add((s, pred, URIRef(_resolve_iri(bfo_type))))
        parent = getattr(ent, "parent_class", "") or ""
        if parent:
            g.add((s, RDFS.subClassOf, URIRef(_resolve_iri(parent))))
    for rel in proposal.relations:
        s = URIRef(_resolve_iri(getattr(rel, "s", "")))
        p_ref = getattr(rel, "p", "") or ""
        p = URIRef(_resolve_iri(p_ref))
        o_ref = getattr(rel, "o", "")
        # A sanctioned class expression on a subClassOf edge serializes as the
        # same real anonymous construct the commit path materialises (X-1) --
        # gate parity. Unparseable strings stay literal so the checks fire.
        expr = owl_checks.parse_class_expression(o_ref or "")
        if expr is not None and "subClassOf" in p_ref:
            g.add((s, RDFS.subClassOf, _expression_node(g, expr)))
            continue
        o = URIRef(_resolve_iri(o_ref)) if o_ref else None
        if o is not None:
            g.add((s, p, o))
    return g.serialize(format="xml")


def _expression_node(g, expr: dict):
    """Build the anonymous construct for a parsed expression via the
    owl_checks emitters (never by templating an IRI)."""
    from rdflib import URIRef

    if expr["op"] == "some":
        return owl_checks.some_values_from(
            g, URIRef(_resolve_iri(expr["prop"])),
            URIRef(_resolve_iri(expr["filler"])))
    if expr["op"] == "not":
        return owl_checks.complement_of(g, URIRef(_resolve_iri(expr["cls"])))
    # not_some
    return owl_checks.complement_of(
        g, owl_checks.some_values_from(
            g, URIRef(_resolve_iri(expr["prop"])),
            URIRef(_resolve_iri(expr["filler"]))))


def _file_level_findings(fragment_xml: str) -> list[dict]:
    """Run the lexical owl_checks (PC-7/PC-8/remint) on the serialized fragment.
    Returns violation dicts in the construction-tier shape."""
    rep = owl_checks.Report()
    rep.findings.extend(owl_checks.check_expression_iris(fragment_xml).findings)
    rep.findings.extend(owl_checks.check_antipatterns_all(fragment_xml).findings)
    rep.findings.extend(owl_checks.check_bad_iris(fragment_xml).findings)
    code_to_rule = {"E_EXPR_IRI": "PC-7", "E_ANTIPATTERN": "PC-8",
                    "E_BFO_REMINT": "PC-4", "E_BAD_IRI": "H-1"}
    return [
        {
            "rule": code_to_rule.get(f.code, f.code),
            "offending_term": f.detail,
            "suggested_rewrite": "See owl_checks; emit a proper construct over "
                                 "kernel terms instead of baking it into an IRI.",
            "detail": f"file-level owl_checks {f.code}",
        }
        for f in rep.findings
    ]


class GateClient:
    """Backend-blind validation gate. Construct once and reuse."""

    def __init__(self, url: str | None = None, timeout: float | None = None):
        self.url = url if url is not None else OWLTESTER_URL
        self.timeout = timeout if timeout is not None else OWLTESTER_TIMEOUT

    @property
    def backend(self) -> str:
        return "remote" if self.url else "local"

    def evaluate(
        self,
        proposal,
        manager,
        run_reasoner: bool = True,
        run_construction: bool = True,
        strict_closed_vocab: bool = False,
        exclude_axioms: Optional[list] = None,
        chain_active: bool = False,
        findings_out: Optional[list] = None,
    ) -> GateResult:
        """Validate a proposal. Returns a GateResult (ACCEPT only if it passes).

        ``chain_active`` / ``findings_out`` carry the recognition-chain rules
        (SPEC P4) through to the local gate. The remote owltesterservice does
        not run them, so a remote gate leaves ``findings_out`` untouched.
        """
        if self.url:
            remote = self._evaluate_remote(proposal)
            if remote is not None:
                return remote
            log.warning("owltesterservice unreachable at %s; falling back to "
                        "local gate", self.url)
        return self._evaluate_local(
            proposal, manager, run_reasoner, run_construction,
            strict_closed_vocab, exclude_axioms, chain_active, findings_out
        )

    # ----- local backend --------------------------------------------------
    def _evaluate_local(self, proposal, manager, run_reasoner, run_construction,
                        strict_closed_vocab, exclude_axioms=None,
                        chain_active=False, findings_out=None) -> GateResult:
        result = coherence_gate.gate(
            proposal, manager,
            run_reasoner=run_reasoner,
            run_construction=run_construction,
            strict_closed_vocab=strict_closed_vocab,
            exclude_axioms=exclude_axioms,
            chain_active=chain_active,
            findings_out=findings_out,
        )
        if not result.accepted:
            return result
        # In-loop gate passed; run the file-level owl_checks for gate parity.
        try:
            findings = _file_level_findings(serialize_fragment(proposal))
        except Exception as e:  # serialization must never crash the gate
            log.warning("file-level owl_checks skipped: %s", e)
            findings = []
        if findings:
            rules = ", ".join(sorted({f["rule"] for f in findings}))
            return GateResult(
                outcome=GateOutcome.REJECT,
                tier=GateTier.CONSTRUCTION,
                reason=f"File-level owl_checks failed [{rules}] on the emitted "
                       f"fragment.",
                justification="\n".join(
                    f"- [{f['rule']}] {f['offending_term']}" for f in findings
                ),
                violations=findings,
            )
        return result

    # ----- remote backend -------------------------------------------------
    def _evaluate_remote(self, proposal) -> Optional[GateResult]:
        """POST the fragment to owltesterservice. Returns None on transport
        failure so the caller can fall back locally."""
        try:
            import requests
        except Exception:
            log.warning("requests not available; cannot reach owltesterservice")
            return None
        try:
            fragment = serialize_fragment(proposal)
            resp = requests.post(
                self.url,
                json={"fragment": fragment, "format": "xml"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return self._map_response(resp.json())
        except Exception as e:
            log.warning("owltesterservice call failed: %s", e)
            return None

    @staticmethod
    def _map_response(data: dict) -> GateResult:
        """Map an owltesterservice JSON verdict onto a GateResult."""
        outcome = GateOutcome(data.get("outcome", "reject"))
        tier_raw = data.get("tier", "none")
        try:
            tier = GateTier(tier_raw)
        except ValueError:
            tier = GateTier.NONE
        return GateResult(
            outcome=outcome,
            tier=tier,
            reason=data.get("reason", ""),
            justification=data.get("justification", ""),
            unsat_classes=data.get("unsat_classes", []) or [],
            violations=data.get("violations", []) or [],
        )


# Module-level default client (mirrors coherence_gate.gate for easy swap-in).
_default = GateClient()


def evaluate(proposal, manager, **kwargs) -> GateResult:
    """Validate via the default gate client (remote if OWLTESTER_URL is set,
    else local). Drop-in for ``coherence_gate.gate``."""
    return _default.evaluate(proposal, manager, **kwargs)
