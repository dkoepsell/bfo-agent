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
        }

    return {
        "n_propose": n_propose,
        "n_commit": n_commit,
        "n_reject": n_reject,
        "verdicts": dict(verdicts),
        "bfo_type_histogram": dict(bfo_type_hist.most_common()),
        "convergence": convergence,
        "inconsistent_examples": inconsistent_examples,
        "error_examples": error_examples,
        "unique_utterances": len(utterances_seen),
    }


def ontology_stats() -> dict:
    """Quick descriptive stats on working.owl without loading owlready2."""
    if not WORKING.exists():
        return {"error": "working.owl not found"}
    text = WORKING.read_text(encoding="utf-8", errors="replace")

    # Rough counts via regex. Not perfect but cheap and safe.
    classes = len(re.findall(r"<owl:Class\b", text))
    named_indivs = len(re.findall(r"<owl:NamedIndividual\b", text))
    obj_props = len(re.findall(r"<owl:ObjectProperty\b", text))
    axioms = len(re.findall(r"<owl:Axiom\b", text))
    subclassofs = len(re.findall(r"<rdfs:subClassOf\b", text))

    return {
        "file_size_kb": round(WORKING.stat().st_size / 1024, 1),
        "classes": classes,
        "named_individuals": named_indivs,
        "object_properties": obj_props,
        "axioms": axioms,
        "subclassof_statements": subclassofs,
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

    conv = summary.get("convergence") or {}
    if conv:
        lines.append("\n## Convergence: new classes per claim, by quartile\n")
        lines.append(f"- Q1 (first quarter of claims so far): {conv['q1_new_classes_per_claim']}")
        lines.append(f"- Q2: {conv['q2_new_classes_per_claim']}")
        lines.append(f"- Q3: {conv['q3_new_classes_per_claim']}")
        lines.append(f"- Q4 (most recent): {conv['q4_new_classes_per_claim']}")
        q1, q4 = conv["q1_new_classes_per_claim"], conv["q4_new_classes_per_claim"]
        lines.append("")
        if q1 == 0:
            lines.append("> Diagnosis: no new classes at start; everything reused the seed. Can't assess convergence yet.")
        elif q4 < 0.5 * q1:
            lines.append("> **Diagnosis: convergence is happening.** Late-stage claims are reusing earlier classes. This is the key finding you want for the paper.")
        elif q4 < q1:
            lines.append("> Diagnosis: mild convergence. The curve is bending but not sharply. Could mean the book genuinely introduces new universals throughout, or that the proposer is not reusing aggressively enough.")
        else:
            lines.append("> Diagnosis: no convergence. The proposer keeps minting new classes at the same rate. Usually a prompt issue: the 'reuse before mint' instruction is not landing. Worth a mid-run prompt check.")

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
