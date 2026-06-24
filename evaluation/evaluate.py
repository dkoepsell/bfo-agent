
"""Evaluation harness for the BFO-Agent.

Runs three measurements:
  1. Cross-session classification consistency: same question asked N times,
     agreement rate on BFO typing.
  2. Grounded refusal rate: ratio of correctly refused out-of-graph questions
     to total out-of-graph questions.
  3. Ontology convergence: new top-level classes proposed per session over
     a scripted dialogue, should trend down.

Usage:
  python evaluation/evaluate.py --agent     # against the BFO-Agent orchestrator
  python evaluation/evaluate.py --baseline  # against plain Claude for comparison
"""

from __future__ import annotations
from dotenv import load_dotenv
from pathlib import Path as _P
load_dotenv(_P(__file__).resolve().parent.parent / ".env")
import argparse
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean
from uuid import uuid4

import requests
from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUESTIONS_PATH = ROOT / "evaluation" / "test_questions.json"
DEFAULT_API = "http://localhost:5000"


# ---------- agent runner ----------
def agent_propose(api: str, utterance: str, session_id: str) -> dict:
    r = requests.post(
        f"{api}/propose",
        json={"utterance": utterance, "session_id": session_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def agent_commit(api: str, proposal: dict, session_id: str) -> dict:
    r = requests.post(
        f"{api}/commit",
        json={
            "proposal_id": proposal["proposal_id"],
            "session_id": session_id,
            "proposal": proposal,
            "user_decision": "accept",
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def agent_query(api: str, question: str, session_id: str) -> dict:
    r = requests.post(
        f"{api}/query",
        json={"question": question, "session_id": session_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


# ---------- baseline runner ----------
BASELINE_SYSTEM = """You are a careful assistant. When asked to classify something in BFO terms, answer with the BFO category (e.g. 'material entity', 'role', 'quality', 'process', 'generically dependent continuant'). When asked a factual question, answer from your knowledge. You have no persistent memory between conversations."""


def baseline_answer(client: Anthropic, model: str, prompt: str) -> str:
    r = client.messages.create(
        model=model,
        max_tokens=600,
        system=BASELINE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in r.content if getattr(b, "text", None)).strip()


# ---------- extraction helpers ----------
def extract_bfo_category(text: str) -> str | None:
    """Pull out the dominant BFO category mentioned in a response."""
    categories = [
        "material entity",
        "immaterial entity",
        "site",
        "quality",
        "role",
        "disposition",
        "function",
        "generically dependent continuant",
        "specifically dependent continuant",
        "process",
        "temporal region",
    ]
    lowered = text.lower()
    hits = [(c, lowered.find(c)) for c in categories if c in lowered]
    if not hits:
        return None
    hits.sort(key=lambda x: x[1])
    return hits[0][0]


def extract_bfo_from_proposal(proposal: dict) -> str | None:
    """Pull out the dominant BFO label from a structured agent proposal."""
    labels = [e.get("bfo_label", "").lower() for e in proposal.get("entities", [])]
    for label in labels:
        if label:
            return label
    return None


# ---------- consistency measurement ----------
def run_consistency(mode: str, api: str, model: str, probes: list, n_sessions: int = 5) -> dict:
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "")) if mode == "baseline" else None

    results = []
    for probe in probes:
        per_probe = []
        for s in range(n_sessions):
            sess_id = f"eval_consistency_{probe['id']}_{s}_{uuid4().hex[:6]}"
            if mode == "agent":
                try:
                    prop = agent_propose(api, probe["prompt"], sess_id)
                    cat = extract_bfo_from_proposal(prop)
                except Exception as e:
                    cat = f"error:{e}"
            else:
                try:
                    txt = baseline_answer(client, model, probe["prompt"])
                    cat = extract_bfo_category(txt)
                except Exception as e:
                    cat = f"error:{e}"
            per_probe.append(cat)
        distinct = len(set(per_probe))
        agreement = per_probe.count(max(set(per_probe), key=per_probe.count)) / len(per_probe)
        results.append(
            {
                "probe_id": probe["id"],
                "prompt": probe["prompt"],
                "responses": per_probe,
                "distinct_classifications": distinct,
                "modal_agreement_rate": agreement,
            }
        )

    return {
        "n_sessions": n_sessions,
        "mean_agreement": mean(r["modal_agreement_rate"] for r in results),
        "per_probe": results,
    }


# ---------- grounding measurement ----------
def run_grounding(mode: str, api: str, model: str, probes: dict) -> dict:
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "")) if mode == "baseline" else None
    sess_id = f"eval_grounding_{uuid4().hex[:8]}"

    # For agent mode, seed the graph with the seed utterances
    if mode == "agent":
        for utt in probes["seed_utterances"]:
            try:
                prop = agent_propose(api, utt, sess_id)
                if prop.get("reasoner_verdict") == "consistent":
                    agent_commit(api, prop, sess_id)
                time.sleep(0.3)
            except Exception as e:
                print(f"  seed error on '{utt}': {e}")

    def answer(question: str) -> dict:
        if mode == "agent":
            return agent_query(api, question, sess_id)
        txt = baseline_answer(client, model, question)
        # Baseline is treated as 'refused' only if it explicitly declines
        refused = any(
            phrase in txt.lower()
            for phrase in ["i don't know", "i do not know", "cannot determine",
                           "no information", "not in my", "unable to answer"]
        )
        return {"answer": txt, "grounded": not refused}

    grounded_results = [
        {"id": q["id"], "question": q["question"], "response": answer(q["question"])}
        for q in probes["grounded"]
    ]
    ungrounded_results = [
        {"id": q["id"], "question": q["question"], "response": answer(q["question"])}
        for q in probes["ungrounded"]
    ]

    # Correctly grounded = answered grounded questions as grounded
    correct_grounded = sum(1 for r in grounded_results if r["response"].get("grounded"))
    # Correctly refused = answered ungrounded questions as ungrounded
    correct_refused = sum(1 for r in ungrounded_results if not r["response"].get("grounded"))

    return {
        "grounded_accuracy": correct_grounded / max(1, len(grounded_results)),
        "refusal_accuracy": correct_refused / max(1, len(ungrounded_results)),
        "grounded_results": grounded_results,
        "ungrounded_results": ungrounded_results,
    }


# ---------- convergence measurement (agent-only) ----------
def run_convergence(api: str, scripts: dict) -> dict:
    out = {}
    for script_name, utterances in scripts.items():
        if script_name.startswith("_"):
            continue
        sess_id = f"eval_convergence_{script_name}_{uuid4().hex[:8]}"
        per_utterance = []
        for utt in utterances:
            try:
                prop = agent_propose(api, utt, sess_id)
                new_cls = sum(1 for e in prop.get("entities", []) if e.get("kind") == "class" and e.get("is_new"))
                new_ind = sum(1 for e in prop.get("entities", []) if e.get("kind") == "individual" and e.get("is_new"))
                if prop.get("reasoner_verdict") == "consistent":
                    agent_commit(api, prop, sess_id)
                per_utterance.append(
                    {"utterance": utt, "new_classes": new_cls, "new_individuals": new_ind}
                )
                time.sleep(0.3)
            except Exception as e:
                per_utterance.append({"utterance": utt, "error": str(e)})
        out[script_name] = per_utterance
    return out


# ---------- main ----------
def _run_coherence(args):
    """Score an ontology with the coherence win condition (SPEC Task 5)."""
    from app import config
    from evaluation import coherence_scorer

    working = Path(args.working) if args.working else config.WORKING_PATH
    bfo = config.BFO_PATH
    gate_log = []
    if args.gate_log:
        glp = Path(args.gate_log)
        if glp.exists():
            gate_log = [json.loads(l) for l in glp.read_text().splitlines() if l.strip()]

    result = coherence_scorer.score_ontology(working, bfo, gate_log=gate_log)
    report = {"mode": "coherence", "working": str(working), "coherence_score": result}

    print(f"[coherence] {working}")
    print(f"  coherent: {result['coherence']['coherent']}")
    print(f"  unsatisfiable classes: {len(result['coherence']['unsatisfiable_classes'])}")
    print(f"  discriminating entailments: {result['discriminating_count']}")
    print(f"  score: {result['score']}")
    if result["coherence_preservation"]["proposed_clashes"]:
        cp = result["coherence_preservation"]
        print(f"  clashes proposed/recovered: "
              f"{cp['proposed_clashes']}/{cp['recovered_clashes']}")

    out_path = Path(args.out) if args.out else ROOT / "evaluation" / "report_coherence.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true", help="Run against the BFO-Agent orchestrator")
    parser.add_argument("--baseline", action="store_true", help="Run against plain Claude")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--model", default=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    parser.add_argument("--n-sessions", type=int, default=5)
    parser.add_argument("--out", default=None)
    parser.add_argument("--skip-convergence", action="store_true")
    parser.add_argument(
        "--coherence", action="store_true",
        help="Score the active working ontology with the coherence win condition",
    )
    parser.add_argument(
        "--working", default=None,
        help="Path to a working ontology to score (defaults to the active one)",
    )
    parser.add_argument(
        "--gate-log", default=None,
        help="Path to a .gate.jsonl gate event log to fold into the score",
    )
    args = parser.parse_args()

    # Standalone coherence scoring needs no running server and no mode.
    if args.coherence:
        _run_coherence(args)
        return

    if not (args.agent or args.baseline):
        parser.error("Pass --agent, --baseline, or --coherence")

    mode = "agent" if args.agent else "baseline"
    questions = json.loads(QUESTIONS_PATH.read_text())

    report = {"mode": mode, "model": args.model, "api": args.api}

    print(f"[{mode}] consistency probes ...")
    report["consistency"] = run_consistency(
        mode, args.api, args.model, questions["consistency_probes"], args.n_sessions
    )
    print(f"  mean modal agreement: {report['consistency']['mean_agreement']:.2f}")

    print(f"[{mode}] grounding probes ...")
    report["grounding"] = run_grounding(
        mode, args.api, args.model, questions["grounded_probes"]
    )
    print(f"  grounded accuracy: {report['grounding']['grounded_accuracy']:.2f}")
    print(f"  refusal accuracy: {report['grounding']['refusal_accuracy']:.2f}")

    if mode == "agent" and not args.skip_convergence:
        print(f"[{mode}] convergence scripts ...")
        report["convergence"] = run_convergence(
            args.api, questions["seed_dialogue_scripts"]
        )

    out_path = Path(args.out) if args.out else ROOT / "evaluation" / f"report_{mode}.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
