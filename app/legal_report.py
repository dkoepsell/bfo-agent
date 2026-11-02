"""Draft quality report and artifact finishing for legal drafts
(SPEC-legal-drafting.md section 7; layers L7 and L8).

:func:`write_report` writes ``draft-report.md`` and ``draft-report.json``
beside the ontology from three sources: the job's claims (with their legal
metadata), ``legal_findings.jsonl``, and the ontology graph itself.

:func:`finish` builds a release copy under ``release/`` beside the ontology:
the ontology header (title, description, sources, license, version,
creator, provenance), language-tagged labels and definitions, the project
namespace in place of the working namespace, and BFO imported through an
XML catalog to a local copy instead of the network. The working file is never
rewritten, so the live feed is unaffected.

Both are evidence and packaging only: nothing here changes a claim, an axiom
or a verdict.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .legal_source import is_legal_genre

BFOAGENT_VERSION = "bfo-agent (SPEC-legal-drafting)"
BFO_IRI = "http://purl.obolibrary.org/obo/bfo.owl"
OBO = "http://purl.obolibrary.org/obo/"
IAO_DEF = OBO + "IAO_0000115"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"

# MLC: the eight chain nodes of OwlTester's law lens.
MLC_NODES = ("source", "norm", "addressee", "position", "conduct",
             "condition", "breach", "remedy")
_NORM_CLAUSES = {"norm", "conferral", "remedy", "time_limit"}
_CONDITION = re.compile(r"\b(where|if|in the case of|in any case where|provided that|"
                        r"unless|after having become aware)\b", re.I)
_BREACH = re.compile(r"\b(infringe\w*|breach\w*|fail\w* to|non-?complian\w*|violat\w*|"
                     r"in breach of|contrary to)\b", re.I)
_REMEDY = re.compile(r"\b(lodge a complaint|judicial remedy|compensation|liable|"
                     r"administrative fines?|penalt\w+|sanction\w*|damages)\b", re.I)
_CONDUCT = re.compile(r"\b(shall(?: not)?|may(?: not)?|must|is entitled to|has the right to|"
                      r"shall have the power to)\s+(\w+)", re.I)


def legal_claims(job: dict) -> list[dict]:
    return [c for c in job.get("claims", []) if is_legal_genre(c.get("genre"))]


def _read_findings(onto_dir: Path) -> list[dict]:
    path = onto_dir / "legal_findings.jsonl"
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ------------------------------------------------------------ the graph

def _graph(working_path: Path):
    import rdflib
    g = rdflib.Graph()
    g.parse(working_path)
    return g


def _graph_facts(g, working_ns: str) -> dict:
    from rdflib import BNode, URIRef
    from rdflib.namespace import OWL, RDF, RDFS
    from .relation_vocab import BFOAGENT_NS as bfoagent

    def P(name):
        return URIRef(bfoagent + name)

    classes = [s for s in g.subjects(RDF.type, OWL.Class)
               if isinstance(s, URIRef) and str(s).startswith(working_ns)]
    isolated, absent, scaffolded = [], [], []
    by_quant: Counter = Counter()
    for c in classes:
        sups = list(g.objects(c, RDFS.subClassOf))
        named_non_bfo = [s for s in sups if isinstance(s, URIRef) and not str(s).startswith(OBO)]
        restrictions = [s for s in sups if isinstance(s, BNode)]
        incoming = list(g.subjects(None, c))
        outgoing_rel = [o for p, o in g.predicate_objects(c)
                        if isinstance(o, URIRef) and p not in (RDF.type, RDFS.subClassOf)
                        and not str(p).startswith(bfoagent)]
        if not named_non_bfo and not restrictions and not outgoing_rel and \
                not [s for s in incoming if (s, RDFS.subClassOf, c) in g or isinstance(s, BNode)]:
            isolated.append(str(c))
        if (c, IAO_DEF_REF(), None) not in g:
            absent.append(str(c))
        for r in restrictions:
            if (r, OWL.someValuesFrom, None) in g:
                by_quant["some"] += 1
            elif (r, OWL.allValuesFrom, None) in g:
                by_quant["only"] += 1
            elif (r, OWL.hasValue, None) in g:
                by_quant["value"] += 1
    for s in set(g.subjects(P("scaffolded"), None)):
        scaffolded.append(str(s))
    spans = list(g.subject_objects(P("sourceSpan")))
    return {"classes": [str(c) for c in classes], "isolated": isolated,
            "absent_in_source": absent, "restrictions_by_kind": dict(by_quant),
            "scaffolded": scaffolded, "spans": [(str(s), str(o)) for s, o in spans],
            "labels": {str(s): str(o) for s, o in g.subject_objects(RDFS.label)},
            "scoped": [(str(s), str(o)) for s, o in g.subject_objects(P("scopedSenseOf"))],
            "definitions": {str(s): str(o) for s, o in g.subject_objects(IAO_DEF_REF())}}


def IAO_DEF_REF():
    from rdflib import URIRef
    return URIRef(IAO_DEF)


# ------------------------------------------------------------- the MLC

def mlc_coverage(claims: list[dict]) -> list[dict]:
    """Per norm, which of the eight chain nodes the TEXT supplied (with its
    evidence) and which are missing. Reported, never filled in."""
    by_act_article: dict[tuple, list[dict]] = {}
    for c in claims:
        art = (c.get("locator") or "unknown").split("/")[0]
        by_act_article.setdefault((c.get("act_id") or "", art), []).append(c)
    act_has_remedy = {c.get("act_id") for c in claims if c.get("clause_type") == "remedy"
                      or _REMEDY.search(c.get("source_quote") or "")}
    rows = []
    for c in claims:
        if (c.get("clause_type") or "") not in _NORM_CLAUSES:
            continue
        quote = c.get("source_quote") or ""
        siblings = by_act_article.get(((c.get("act_id") or ""),
                                       (c.get("locator") or "unknown").split("/")[0]), [])
        nodes: dict[str, str | None] = {}
        nodes["source"] = (f"{c.get('act_id')} {c.get('locator')}" if c.get("act_id") else None)
        nodes["norm"] = quote[:120] or None
        nodes["addressee"] = c.get("subject") or None
        nodes["position"] = c.get("modality") if c.get("modality") not in (None, "", "none") else None
        m = _CONDUCT.search(quote)
        nodes["conduct"] = m.group(0) if m else None
        cond = _CONDITION.search(quote)
        sib_cond = next((s for s in siblings if s.get("clause_type") == "condition"), None)
        nodes["condition"] = (cond.group(0) if cond else
                              (sib_cond.get("source_quote", "")[:80] if sib_cond else None))
        br = _BREACH.search(quote)
        nodes["breach"] = br.group(0) if br else None
        rem = _REMEDY.search(quote)
        nodes["remedy"] = (rem.group(0) if rem else
                           ("elsewhere in the act" if c.get("act_id") in act_has_remedy else None))
        rows.append({"claim_id": c.get("id"), "act_id": c.get("act_id"),
                     "locator": c.get("locator"), "nodes": nodes,
                     "present": [n for n in MLC_NODES if nodes.get(n)],
                     "missing": [n for n in MLC_NODES if not nodes.get(n)]})
    return rows


# ------------------------------------------------------------ OwlTester

def owltester_audit(path: Path) -> dict:
    """Run OwlTester when configured (``OWLTESTER_CMD``, or an ``owltester``
    binary on PATH). Never raises; returns ``{status, summary, ...}``."""
    cmd_t = os.environ.get("OWLTESTER_CMD", "")
    if not cmd_t and shutil.which("owltester"):
        cmd_t = "owltester audit --profile bfo --fail-on major --json {path}"
    if not cmd_t:
        return {"status": "not-configured",
                "summary": "OwlTester is not configured (set OWLTESTER_CMD or install owltester)."}
    cmd = [part.replace("{path}", str(path)) for part in shlex.split(cmd_t)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:
        return {"status": "error", "summary": f"OwlTester failed to run: {e}"}
    out = {"status": "ok" if proc.returncode == 0 else f"exit {proc.returncode}",
           "command": " ".join(cmd)}
    try:
        data = json.loads(proc.stdout)
        out["report"] = data
        out["summary"] = (data.get("summary") if isinstance(data, dict) else None) or \
            f"{len(data) if hasattr(data, '__len__') else '?'} findings"
    except Exception:
        out["summary"] = (proc.stdout or proc.stderr or "")[-1500:]
    return out


# --------------------------------------------------------------- report

def build_report(job: dict, working_path: Path, working_ns: str,
                 audit: dict | None = None) -> dict:
    claims = legal_claims(job)
    onto_dir = Path(working_path).resolve().parent
    findings = _read_findings(onto_dir)
    facts = _graph_facts(_graph(working_path), working_ns)

    acts: dict[str, dict] = {}
    for c in claims:
        a = acts.setdefault(c.get("act_id") or "unknown", {
            "act_id": c.get("act_id") or "", "title": c.get("act_title") or "",
            "articles": set(), "claims": 0, "committed": 0})
        a["articles"].add((c.get("locator") or "unknown").split("/")[0])
        a["claims"] += 1
        a["committed"] += c.get("status") == "committed"
    for a in acts.values():
        a["articles"] = sorted(a["articles"])

    committed = [c for c in claims if c.get("status") == "committed"]
    verified = [c for c in committed if c.get("quote_verified")]
    para_rejected = [f for f in findings
                     if any(x.get("check") == "paraphrase-provenance" for x in f["findings"])]
    by_check: Counter = Counter(x["check"] for f in findings for x in f["findings"])
    quant: Counter = Counter((c.get("quantifier") or "unstated") for c in committed)
    modal_axioms = [f for f in findings for x in f["findings"]
                    if x["check"] == "modal-universal" and f.get("action") == "committed-as-asserted"]
    defined_twice = []
    for sense, general in facts["scoped"]:
        defined_twice.append({"general": general, "sense": sense,
                              "label": facts["labels"].get(sense, ""),
                              "definition": facts["definitions"].get(sense, "")})

    spans_ok = sum(1 for _, s in facts["spans"]
                   if any(s and s in (c.get("source_quote") or "") for c in claims))
    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "job_id": job.get("job_id"), "job_name": job.get("name"),
        "ontology": str(working_path),
        "sources": list(acts.values()),
        "provenance": {
            "committed_legal_claims": len(committed),
            "with_verbatim_quote": len(verified),
            "verbatim_rate": (len(verified) / len(committed)) if committed else None,
            "spans_in_ontology": len(facts["spans"]),
            "spans_matching_a_quote": spans_ok,
            "claims_rejected_for_paraphrase": len(para_rejected),
        },
        "quantifiers": {"committed_claims_by_quantifier": dict(quant),
                        "class_restrictions_by_kind": facts["restrictions_by_kind"],
                        "modal_derived_axioms_committed": len(modal_axioms)},
        "checks": dict(by_check),
        "scaffolded_axioms": facts["scaffolded"],
        "definitions_in_several_acts": defined_twice,
        "isolated_terms": facts["isolated"],
        "absent_in_source": {"count": len(facts["absent_in_source"]),
                             "share": (len(facts["absent_in_source"]) / len(facts["classes"]))
                             if facts["classes"] else None,
                             "classes": facts["absent_in_source"]},
        "mlc_coverage": mlc_coverage(claims),
        "ledger": [f for f in findings if f.get("action") == "committed-as-asserted"],
        "audit": audit or {"status": "not-run"},
    }
    return report


def _short(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def render_markdown(r: dict) -> str:
    p = r["provenance"]
    rate = f"{p['verbatim_rate']:.0%}" if p["verbatim_rate"] is not None else "n/a"
    L = [f"# Draft quality report: {r.get('job_name') or r.get('job_id')}", "",
         f"Generated {r['generated']}. Evidence only: nothing in this report changed the draft.", "",
         "## Sources", "", "| Act | Title | Articles | Claims | Committed |", "| --- | --- | --- | --- | --- |"]
    for a in r["sources"]:
        L.append(f"| {a['act_id'] or 'unknown'} | {a['title']} | {', '.join(a['articles'])} | "
                 f"{a['claims']} | {a['committed']} |")
    L += ["", "## Provenance", "",
          f"- Committed legal claims: {p['committed_legal_claims']}; with a verbatim quote: "
          f"{p['with_verbatim_quote']} ({rate}; target 100%).",
          f"- Source spans in the ontology: {p['spans_in_ontology']}; matching a claim's quote: "
          f"{p['spans_matching_a_quote']}.",
          f"- Claims rejected for paraphrase: {p['claims_rejected_for_paraphrase']}.", "",
          "## Quantifiers", ""]
    q = r["quantifiers"]
    L.append("- Committed claims by quantifier: " +
             (", ".join(f"{k} {v}" for k, v in sorted(q["committed_claims_by_quantifier"].items())) or "none"))
    L.append("- Class restrictions by kind: " +
             (", ".join(f"{k} {v}" for k, v in sorted(q["class_restrictions_by_kind"].items())) or "none"))
    L.append(f"- Class-level axioms from 'can' or permission committed as asserted (faithful): "
             f"{q['modal_derived_axioms_committed']} (target 0 in curated).")
    L += ["", "## Checks", ""]
    L += [f"- {k}: {v}" for k, v in sorted(r["checks"].items())] or ["- none fired"]
    L += ["", "## Scaffolded axioms", ""]
    L += [f"- {_short(s)}" for s in r["scaffolded_axioms"]] or ["- none"]
    L += ["", "## Definitions in several acts", ""]
    L += [f"- {d['label']} (sense of {_short(d['general'])}): {d['definition'][:200]}"
          for d in r["definitions_in_several_acts"]] or ["- none"]
    L += ["", "## Isolated terms", "", "Classes with no relation other than their BFO parent:", ""]
    L += [f"- {_short(c)}" for c in r["isolated_terms"]] or ["- none"]
    a = r["absent_in_source"]
    share = f"{a['share']:.0%}" if a["share"] is not None else "n/a"
    L += ["", "## absent-in-source", "", f"{a['count']} classes ({share}) have no definition in the source.",
          "", "## MLC coverage", "",
          "Per norm: the chain nodes the text supplied. Missing nodes are reported, never filled in.", "",
          "| Norm | " + " | ".join(MLC_NODES) + " |", "| --- |" + " --- |" * len(MLC_NODES)]
    for row in r["mlc_coverage"]:
        cells = ["yes" if row["nodes"].get(n) else "no" for n in MLC_NODES]
        L.append(f"| {row['act_id']} {row['locator']} | " + " | ".join(cells) + " |")
    if not r["mlc_coverage"]:
        L.append("| (no norms) |" + " |" * len(MLC_NODES))
    L += ["", "## Ledger", ""]
    L += [f"- claim {f.get('claim_id')} ({f.get('locator')}): " +
          "; ".join(x["check"] for x in f["findings"]) for f in r["ledger"]] or ["- no faithful-mode entries"]
    L += ["", "## Audit", "", f"OwlTester: {r['audit'].get('status')}. {r['audit'].get('summary', '')}".strip(), ""]
    return "\n".join(L)


def write_report(job: dict, working_path: Path, working_ns: str,
                 audit_path: Path | None = None) -> dict:
    audit = owltester_audit(audit_path) if audit_path else None
    r = build_report(job, Path(working_path), working_ns, audit=audit)
    onto_dir = Path(working_path).resolve().parent
    (onto_dir / "draft-report.json").write_text(json.dumps(r, indent=2, ensure_ascii=False))
    (onto_dir / "draft-report.md").write_text(render_markdown(r), encoding="utf-8")
    return r


# -------------------------------------------------------------- finishing

CATALOG = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<catalog prefer="public" xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">
  <uri name="{bfo}" uri="bfo.owl"/>
</catalog>
"""


def finish(working_path: Path, bfo_path: Path, manifest: dict, acts: list[dict],
           working_ns: str, language: str = "en") -> dict:
    """Build ``release/<name>.owl`` + ``catalog-v001.xml`` + ``bfo.owl``.

    ``manifest`` fields used: ``name``, ``title``, ``description``,
    ``namespace`` (project namespace; default
    ``https://w3id.org/bfo-agent/<name>/``), ``license`` (default CC BY 4.0),
    ``version`` (default today's date), ``creator``."""
    import rdflib
    from rdflib import Literal, URIRef
    from rdflib.namespace import DCTERMS, OWL, RDF, RDFS

    working_path = Path(working_path)
    name = manifest.get("name") or working_path.parent.name
    ns = manifest.get("namespace") or f"https://w3id.org/bfo-agent/{name}/"
    version = manifest.get("version") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = working_path.parent / "release"
    out_dir.mkdir(exist_ok=True)

    g = rdflib.Graph()
    g.parse(working_path)
    old = working_ns.rstrip("#/")

    def rename(t):
        if isinstance(t, URIRef):
            s = str(t)
            for sep in ("#", "/"):
                if s.startswith(old + sep):
                    return URIRef(ns + s[len(old) + 1:])
            if s == old:
                return URIRef(ns.rstrip("/"))
        return t

    h = rdflib.Graph()
    tagged = 0
    for s, p, o in g:
        if p in (RDFS.label, URIRef(IAO_DEF)) and isinstance(o, Literal) and not o.language \
                and (o.datatype is None or str(o.datatype).endswith("#string")):
            o = Literal(str(o), lang=language)
            tagged += 1
        h.add((rename(s), rename(p), rename(o)))

    for ont in list(h.subjects(RDF.type, OWL.Ontology)):
        h.remove((ont, None, None))
    ont = URIRef(ns.rstrip("/") + ".owl")
    h.add((ont, RDF.type, OWL.Ontology))
    h.add((ont, OWL.versionIRI, URIRef(f"{ns}releases/{version}/{name}.owl")))
    h.add((ont, OWL.versionInfo, Literal(version)))
    h.add((ont, DCTERMS.title, Literal(manifest.get("title") or name, lang=language)))
    h.add((ont, DCTERMS.description, Literal(
        manifest.get("description") or
        f"An ontology drafted by bfo-agent from {', '.join(a.get('title') or a.get('act_id') for a in acts) or 'legal sources'}, "
        f"anchored to BFO 2020 and the Legal Ontology Foundry patterns.", lang=language)))
    h.add((ont, DCTERMS.license, URIRef(manifest.get("license") or CC_BY)))
    h.add((ont, RDFS.comment, Literal(
        "The license covers this ontology. The source texts keep their own reuse terms.",
        lang=language)))
    if manifest.get("creator"):
        h.add((ont, DCTERMS.creator, Literal(manifest["creator"])))
    h.add((ont, URIRef("http://www.w3.org/ns/prov#wasGeneratedBy"), Literal(BFOAGENT_VERSION)))
    h.add((ont, OWL.imports, URIRef(BFO_IRI)))
    from .legal_kernel import act_source_url
    for a in acts:
        src = act_source_url(a.get("act_id") or "")
        if src:
            h.add((ont, DCTERMS.source, URIRef(src)))
    h.add((ont, RDFS.seeAlso, URIRef("https://w3id.org/lof/")))

    out = out_dir / f"{name}.owl"
    h.serialize(out, format="xml")
    (out_dir / "catalog-v001.xml").write_text(CATALOG.format(bfo=BFO_IRI))
    shutil.copyfile(bfo_path, out_dir / "bfo.owl")
    return {"artifact": str(out), "catalog": str(out_dir / "catalog-v001.xml"),
            "namespace": ns, "version": version, "labels_tagged": tagged,
            "triples": len(h)}
