"""Interim health check on a feed run in progress.

Reads from sessions/*.jsonl and ontology/working.owl WITHOUT calling any
LLM or touching the orchestrator state. Safe to run while the browser is
still feeding claims.

Usage:
  python scripts/interim_report.py                         # auto-pick latest feed session
  python scripts/interim_report.py --session feed_SOoL_... # specific session
  python scripts/interim_report.py --save report.md        # write markdown report
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "sessions"
WORKING = ROOT / "ontology" / "working.owl"

BFO_LABELS = {
    "BFO_0000001": "entity",
    "BFO_0000002": "continuant",
    "BFO_0000003": "occurrent",
    "BFO_0000004": "independent continuant",
    "BFO_0000008": "temporal region",
    "BFO_0000011": "spatiotemporal region",
    "BFO_0000015": "process",
    "BFO_0000016": "disposition",
    "BFO_0000017": "realizable entity",
    "BFO_0000019": "quality",
    "BFO_0000020": "specifically dependent continuant",
    "BFO_0000023": "role",
    "BFO_0000029": "site",
    "BFO_0000031": "generically dependent continuant",
    "BFO_0000034": "function",
    "BFO_0000040": "material entity",
    "BFO_0000141": "immaterial entity",
}


def find_latest_feed_session() -> Path | None:
    candidates = sorted(SESSIONS.glob("feed_*.jsonl"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def load_events(path: Path) -> list[dict]:
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def summarize(events: list[dict]) -> dict:
    """Derive summary stats from a sequence of session events."""
    n_propose = n_commit = n_reject = 0
    verdicts: Counter = Counter()
    bfo_type_hist: Counter = Counter()
    new_class_count_per_claim: list[int] = []
    parent_attachments_per_claim: list[tuple[int, int, int]] = []  # (working, bfo, other)
    inconsistent_examples: list[dict] = []
    error_examples: list[dict] = []
    utterances_seen: set[str] = set()

    for ev in events:
        t = ev.get("event_type")
        p = ev.get("payload", {}) or {}

        if t == "propose":
            n_propose += 1
            proposal = p.get("proposal", {}) or {}
            verdict = proposal.get("reasoner_verdict")
            if verdict:
                verdicts[verdict] += 1

            entities = proposal.get("entities", []) or []
            new_classes_here = 0
            for e in entities:
                bfo_type = e.get("bfo_type", "?")
                bfo_type_hist[bfo_type] += 1
                if e.get("kind") == "class" and e.get("is_new"):
                    new_classes_here += 1
            new_class_count_per_claim.append(new_classes_here)

            # Track where new subclasses attach: to BFO top-levels, to
            # working-ontology classes (reuse), or to anonymous class
            # expressions (defined classes via OWL restrictions).
            sub_bfo = sub_working = sub_other = 0
            for rel in proposal.get("relations", []) or []:
                if rel.get("p") not in ("rdfs:subClassOf", "subClassOf"):
                    continue
                obj = rel.get("o", "") or ""
                if obj.startswith("bfo:") or obj.startswith("BFO_"):
                    sub_bfo += 1
                elif obj.startswith("working:"):
                    sub_working += 1
                else:
                    sub_other += 1
            parent_attachments_per_claim.append((sub_working, sub_bfo, sub_other))

            if verdict == "inconsistent" and len(inconsistent_examples) < 10:
                inconsistent_examples.append(
                    {
                        "utterance": proposal.get("utterance", "")[:160],
                        "rationale_summary": proposal.get("rationale_summary", "")[:200],
                        "detail_head": (proposal.get("reasoner_detail") or "")[:400],
                        "entities": [
                            f"{e.get('iri_suggestion')} :: {e.get('bfo_label')}"
                            for e in entities
                        ],
                    }
                )
            if verdict == "error" and len(error_examples) < 5:
                error_examples.append(
                    {
                        "utterance": proposal.get("utterance", "")[:160],
                        "detail_head": (proposal.get("reasoner_detail") or "")[:300],
                    }
                )

            utt = proposal.get("utterance")
            if utt:
                utterances_seen.add(utt)

        elif t == "commit":
            n_commit += 1
        elif t == "reject":
            n_reject += 1

    # Convergence: new classes per 100 claims, first vs last quartile
    def window_avg(lst: list[int], start: int, end: int) -> float:
        sl = lst[start:end]
        return (sum(sl) / len(sl)) if sl else 0.0

    def window_attach_fraction(attachments: list, start: int, end: int) -> dict:
        """Fraction of subClassOf edges pointing to working vs BFO vs other."""
        sl = attachments[start:end]
        total = sum(sum(t) for t in sl)
        if not total:
            return {"working_pct": 0.0, "bfo_pct": 0.0, "other_pct": 0.0, "n": 0}
        w = sum(t[0] for t in sl)
        b = sum(t[1] for t in sl)
        o = sum(t[2] for t in sl)
        return {
            "working_pct": round(100 * w / total, 1),
            "bfo_pct": round(100 * b / total, 1),
            "other_pct": round(100 * o / total, 1),
            "n": total,
        }

    convergence = {}
    n = len(new_class_count_per_claim)
    if n >= 8:
        q = n // 4
        convergence = {
            "n_claims": n,
            "q1_new_classes_per_claim": round(window_avg(new_class_count_per_claim, 0, q), 3),
            "q2_new_classes_per_claim": round(window_avg(new_class_count_per_claim, q, 2 * q), 3),
            "q3_new_classes_per_claim": round(window_avg(new_class_count_per_claim, 2 * q, 3 * q), 3),
            "q4_new_classes_per_claim": round(window_avg(new_class_count_per_claim, 3 * q, n), 3),
            "q1_attach": window_attach_fraction(parent_attachments_per_claim, 0, q),
            "q2_attach": window_attach_fraction(parent_attachments_per_claim, q, 2 * q),
            "q3_attach": window_attach_fraction(parent_attachments_per_claim, 2 * q, 3 * q),
            "q4_attach": window_attach_fraction(parent_attachments_per_claim, 3 * q, n),
        }

    # Overall attach totals
    total_working = sum(t[0] for t in parent_attachments_per_claim)
    total_bfo = sum(t[1] for t in parent_attachments_per_claim)
    total_other = sum(t[2] for t in parent_attachments_per_claim)
    total_attach = total_working + total_bfo + total_other
    attach_overall = {
        "subClassOf_working": total_working,
        "subClassOf_bfo": total_bfo,
        "subClassOf_other_defined": total_other,
        "working_pct": round(100 * total_working / total_attach, 1) if total_attach else 0.0,
        "bfo_pct": round(100 * total_bfo / total_attach, 1) if total_attach else 0.0,
        "other_pct": round(100 * total_other / total_attach, 1) if total_attach else 0.0,
    }

    return {
        "n_propose": n_propose,
        "n_commit": n_commit,
        "n_reject": n_reject,
        "verdicts": dict(verdicts),
        "bfo_type_histogram": dict(bfo_type_hist.most_common()),
        "convergence": convergence,
        "attach_overall": attach_overall,
        "inconsistent_examples": inconsistent_examples,
        "error_examples": error_examples,
        "unique_utterances": len(utterances_seen),
    }


def ontology_stats() -> dict:
    """Quick descriptive stats on working.owl without loading owlready2.

    Uses regex patterns that match owlready2's rdfxml serialization. Not
    exact, but the relative magnitudes are reliable enough for an interim
    health check. For authoritative counts, load the graph with rdflib or
    owlready2 after the run.
    """
    if not WORKING.exists():
        return {"error": "working.owl not found"}
    text = WORKING.read_text(encoding="utf-8", errors="replace")

    # Classes: owl:Class both as element and as rdf:type attribute form
    classes = len(re.findall(r"<owl:Class[\s>]", text))
    named_indivs = len(re.findall(r"<owl:NamedIndividual[\s>]", text))

    # Object properties: owlready2 often serializes these as elements; also
    # catch property references via owl:onProperty in restrictions.
    obj_props_elem = len(re.findall(r"<owl:ObjectProperty[\s>]", text))
    on_property_refs = len(re.findall(r"<owl:onProperty\b", text))

    # Restrictions are a common vehicle for defined classes
    restrictions = len(re.findall(r"<owl:Restriction[\s>]", text))

    # Axioms via owl:Axiom wrappers (annotation axioms, etc.)
    axioms = len(re.findall(r"<owl:Axiom[\s>]", text))

    # Structural statements
    subclassofs = len(re.findall(r"<rdfs:subClassOf\b", text))
    inherits_from = len(re.findall(r"<rdf:type\b", text))

    return {
        "file_size_kb": round(WORKING.stat().st_size / 1024, 1),
        "classes": classes,
        "named_individuals": named_indivs,
        "object_property_declarations": obj_props_elem,
        "onProperty_references_in_restrictions": on_property_refs,
        "restrictions_defined_classes": restrictions,
        "owl_axiom_wrappers": axioms,
        "subclassof_statements": subclassofs,
        "rdf_type_statements": inherits_from,
        "_note": "regex-based; see final report for authoritative counts via owlready2",
    }


def render_markdown(session: Path, summary: dict, ontstats: dict) -> str:
    lines: list[str] = []
    lines.append(f"# BFO-Agent Interim Report\n")
    lines.append(f"Session: `{session.name}`  ")
    lines.append(f"Events file size: {round(session.stat().st_size / 1024, 1)} KB\n")

    lines.append("## Feed progress\n")
    lines.append(f"- Proposals made: **{summary['n_propose']}**")
    lines.append(f"- Claims committed: **{summary['n_commit']}**")
    lines.append(f"- Claims rejected by user: {summary['n_reject']}")
    lines.append(f"- Unique utterances seen: {summary['unique_utterances']}")

    lines.append("\n## Reasoner verdicts on proposals\n")
    v = summary["verdicts"]
    total = sum(v.values()) or 1
    for verdict, count in sorted(v.items(), key=lambda x: -x[1]):
        pct = 100 * count / total
        lines.append(f"- **{verdict}**: {count} ({pct:.1f}%)")

    inc = v.get("inconsistent", 0)
    con = v.get("consistent", 0)
    if total >= 20:
        ratio = inc / total
        lines.append("")
        if ratio < 0.02:
            lines.append("> Diagnosis: almost no inconsistencies. HermiT is not doing much work. Usually means the seed ontology lacks disjointness axioms; consider adding some and rerunning later, but the current feed is not a problem.")
        elif ratio < 0.15:
            lines.append("> Diagnosis: healthy inconsistency rate. Reasoner is catching real conflicts. This is the target zone.")
        elif ratio < 0.30:
            lines.append("> Diagnosis: elevated inconsistency rate. Worth examining inconsistent examples for a pattern. If they cluster on one type of confusion (e.g. role vs. bearer), a prompt tweak could help, but not worth restarting unless >30%.")
        else:
            lines.append("> **Diagnosis: high inconsistency rate. Consider pausing.** The proposer may be systematically misusing BFO categories. See inconsistent examples below; if a clear pattern emerges, restarting with a refined prompt will save time overall.")

    lines.append("\n## BFO type histogram (over proposed entities)\n")
    lines.append("| BFO type | Label | Count |")
    lines.append("|---|---|---|")
    for bfo_type, count in list(summary["bfo_type_histogram"].items())[:20]:
        label = BFO_LABELS.get(bfo_type, "?")
        lines.append(f"| `{bfo_type}` | {label} | {count} |")

    lines.append("\n## Ontology file stats\n")
    for k, v in ontstats.items():
        lines.append(f"- **{k}**: {v}")

    attach = summary.get("attach_overall") or {}
    if attach.get("subClassOf_working", 0) + attach.get("subClassOf_bfo", 0) > 0:
        lines.append("\n## Reuse pattern: where new classes attach\n")
        lines.append(f"- **subClassOf existing working class (reuse):** {attach['subClassOf_working']} ({attach['working_pct']}%)")
        lines.append(f"- **subClassOf BFO top-level (new top-level concept):** {attach['subClassOf_bfo']} ({attach['bfo_pct']}%)")
        lines.append(f"- **subClassOf anonymous class expression (defined class):** {attach['subClassOf_other_defined']} ({attach['other_pct']}%)")
        w_pct = attach["working_pct"]
        lines.append("")
        if w_pct >= 60:
            lines.append("> Diagnosis: strong reuse. The proposer is placing new classes under existing ones rather than minting parallel top-level concepts. This is the healthy pattern and the signal you want for the paper.")
        elif w_pct >= 40:
            lines.append("> Diagnosis: moderate reuse. Some elaboration is happening, but a large fraction of new classes still attach directly to BFO. Could indicate the proposer's context window on the existing graph is too small (check `max_items` in ontology_manager).")
        else:
            lines.append("> **Diagnosis: weak reuse.** New classes are mostly attaching to BFO top-levels, producing parallel concepts rather than hierarchy depth. Consider raising the class-summary cap passed to the proposer.")

    conv = summary.get("convergence") or {}
    if conv:
        lines.append("\n## Growth curve by quartile\n")
        lines.append("| Quartile | New classes/claim | Attach to working | Attach to BFO | Defined classes |")
        lines.append("|---|---|---|---|---|")
        for k in ("q1", "q2", "q3", "q4"):
            a = conv.get(f"{k}_attach", {})
            lines.append(
                f"| {k.upper()} | {conv.get(f'{k}_new_classes_per_claim', 0)} | "
                f"{a.get('working_pct', 0)}% | {a.get('bfo_pct', 0)}% | {a.get('other_pct', 0)}% |"
            )
        q1_w = conv.get("q1_attach", {}).get("working_pct", 0)
        q4_w = conv.get("q4_attach", {}).get("working_pct", 0)
        q1_b = conv.get("q1_attach", {}).get("bfo_pct", 0)
        q4_b = conv.get("q4_attach", {}).get("bfo_pct", 0)
        lines.append("")
        if q4_w > q1_w + 10 and q4_b < q1_b - 5:
            lines.append("> **Diagnosis: convergence is happening.** Late-book claims attach to existing working classes more than early-book claims did, and attach directly to BFO less. This is the curve the hypothesis predicts.")
        elif q4_b < q1_b:
            lines.append("> Diagnosis: mild convergence. BFO-attachment fraction is declining across the book. Worth confirming in the final report.")
        elif q4_w < q1_w:
            lines.append("> **Diagnosis: anti-convergence.** Later claims are reusing LESS than earlier ones. Check whether the proposer's context window is saturating or whether the book genuinely introduces new top-level concepts throughout.")
        else:
            lines.append("> Diagnosis: flat reuse pattern. The fraction of reuse vs. new concepts is roughly stable across the book. Not the strongest signal for the paper, but not a problem either.")

        lines.append("")
        lines.append("Note: total new classes per claim can rise even when reuse is healthy, because each claim elaborates existing hierarchy with multiple fine-grained subclasses. Read the attachment columns, not the count column, for the reuse story.")

    if summary["inconsistent_examples"]:
        lines.append("\n## Sample inconsistent proposals\n")
        for i, ex in enumerate(summary["inconsistent_examples"], 1):
            lines.append(f"### Example {i}")
            lines.append(f"**Utterance:** {ex['utterance']}")
            if ex["rationale_summary"]:
                lines.append(f"**Proposer rationale:** {ex['rationale_summary']}")
            if ex["entities"]:
                lines.append(f"**Entities proposed:**")
                for e in ex["entities"]:
                    lines.append(f"- {e}")
            if ex["detail_head"]:
                lines.append(f"**Reasoner detail (first 400 chars):**")
                lines.append(f"```\n{ex['detail_head']}\n```")
            lines.append("")

    if summary["error_examples"]:
        lines.append("\n## Sample reasoner errors\n")
        for i, ex in enumerate(summary["error_examples"], 1):
            lines.append(f"### Error {i}")
            lines.append(f"**Utterance:** {ex['utterance']}")
            lines.append(f"```\n{ex['detail_head']}\n```\n")

    lines.append("\n## Restart decision heuristic\n")
    lines.append("Restart is worth it if ALL of these are true:")
    lines.append("- Inconsistency rate is above 30%, AND")
    lines.append("- The inconsistencies cluster on a single type of confusion (not scattered), AND")
    lines.append("- You can describe the fix in one sentence that would go in the proposer prompt")
    lines.append("")
    lines.append("Otherwise, let it finish. Mid-run prompt changes produce incoherent graphs that mix pre- and post-fix typing styles, which is worse than either extreme.")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=None, help="Session id (without .jsonl); defaults to latest feed_*")
    parser.add_argument("--save", default=None, help="Write markdown to this path in addition to stdout")
    parser.add_argument("--json", default=None, help="Also write raw summary JSON to this path")
    args = parser.parse_args()

    if args.session:
        path = SESSIONS / (args.session if args.session.endswith(".jsonl") else args.session + ".jsonl")
    else:
        path = find_latest_feed_session()
        if not path:
            print("No feed_*.jsonl sessions found.", file=sys.stderr)
            return 1

    if not path.exists():
        print(f"Session not found: {path}", file=sys.stderr)
        return 1

    events = load_events(path)
    summary = summarize(events)
    ontstats = ontology_stats()
    report = render_markdown(path, summary, ontstats)

    print(report)
    if args.save:
        Path(args.save).write_text(report, encoding="utf-8")
        print(f"\n[saved to {args.save}]", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"session": path.name, "summary": summary, "ontology": ontstats}, indent=2),
            encoding="utf-8",
        )
        print(f"[saved raw json to {args.json}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
