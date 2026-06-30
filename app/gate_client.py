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
    frag = _local(ref)
    if frag.split("_")[0] in ("BFO", "RO", "IAO"):
        return _OBO + frag
    return _WORKING_NS + frag


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
        p = URIRef(_resolve_iri(getattr(rel, "p", "")))
        o_ref = getattr(rel, "o", "")
        o = URIRef(_resolve_iri(o_ref)) if o_ref else None
        if o is not None:
            g.add((s, p, o))
    return g.serialize(format="xml")


def _file_level_findings(fragment_xml: str) -> list[dict]:
    """Run the lexical owl_checks (PC-7/PC-8/remint) on the serialized fragment.
    Returns violation dicts in the construction-tier shape."""
    rep = owl_checks.Report()
    rep.findings.extend(owl_checks.check_expression_iris(fragment_xml).findings)
    rep.findings.extend(owl_checks.check_antipatterns_all(fragment_xml).findings)
    code_to_rule = {"E_EXPR_IRI": "PC-7", "E_ANTIPATTERN": "PC-8",
                    "E_BFO_REMINT": "PC-4"}
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
    ) -> GateResult:
        """Validate a proposal. Returns a GateResult (ACCEPT only if it passes)."""
        if self.url:
            remote = self._evaluate_remote(proposal)
            if remote is not None:
                return remote
            log.warning("owltesterservice unreachable at %s; falling back to "
                        "local gate", self.url)
        return self._evaluate_local(
            proposal, manager, run_reasoner, run_construction, strict_closed_vocab
        )

    # ----- local backend --------------------------------------------------
    def _evaluate_local(self, proposal, manager, run_reasoner, run_construction,
                        strict_closed_vocab) -> GateResult:
        result = coherence_gate.gate(
            proposal, manager,
            run_reasoner=run_reasoner,
            run_construction=run_construction,
            strict_closed_vocab=strict_closed_vocab,
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
