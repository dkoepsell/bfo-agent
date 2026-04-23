"""Detect canonicalization candidates in the working ontology.

Produces a JSON report of potential merges, ranked by confidence. Does
NOT modify the ontology; safe to run while the feed is active.

Detection strategies (most confident first):

  1. word-bag identity: classes whose names contain exactly the same
     words in different order or spacing. Almost always safe to merge.

  2. word-bag near-identity: classes whose names differ by one stop-word
     (like "the" or "of") or by a minor morphological variant.

  3. shared stem with suffix permutation: same root concept expressed
     as Process, Event, Act, Thesis, Principle. NOT automatically
     mergeable (these are often genuinely distinct), but flagged for
     review.

  4. embedding similarity (optional): classes whose labels have high
     cosine similarity in a sentence embedding. Slower, requires the
     sentence-transformers package.

The report is written as JSON with one bucket per candidate group,
including source-quote provenance for each class if available in the
session log.

Usage:
    source .venv/bin/activate
    python scripts/detect_canonicalization.py
    python scripts/detect_canonicalization.py --with-embeddings  # slower, more thorough
    python scripts/detect_canonicalization.py --out report.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ontology_manager import OntologyManager
from app import config


STOPWORDS = {"the", "of", "a", "an", "and", "or", "in", "on", "to", "for", "by",
             "with", "as", "is", "at", "from"}

# Common suffixes used to split CamelCase into concept + role
BFO_SUFFIXES = [
    "Process", "Event", "Act", "Thesis", "Principle", "Claim",
    "Relation", "Dependence", "Condition", "Capacity", "Disposition",
    "Quality", "Role", "Function", "Network", "Structure", "Entity",
    "Commitment", "Framework",
]


def camel_split(name: str) -> list[str]:
    """Split CamelCase/PascalCase into a list of lowercase word tokens."""
    # Match sequences of upper+lower, or runs of uppers (for acronyms), or digits
    tokens = re.findall(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|[a-z]+|\d+", name)
    return [t.lower() for t in tokens]


def bag(name: str) -> tuple[str, ...]:
    """Sorted word bag, stopwords removed."""
    words = [w for w in camel_split(name) if w not in STOPWORDS]
    return tuple(sorted(words))


def extract_stem(name: str) -> str | None:
    """If name ends in a BFO-flavored suffix, return the stem (lowercased)."""
    for suf in BFO_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf) + 3:
            return name[: -len(suf)].lower()
    return None


def load_provenance(sessions_dir: Path) -> dict[str, str]:
    """Best-effort: map class local-name -> first source quote we find.

    Reads feed session logs looking for commit events that introduced
    each class. Returns class_name -> source_quote (truncated).
    """
    prov: dict[str, str] = {}
    if not sessions_dir.exists():
        return prov

    for session_file in sessions_dir.glob("feed_*.jsonl"):
        try:
            with session_file.open() as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event_type") != "commit":
                        continue
                    payload = ev.get("payload", {})
                    quote = payload.get("source_quote", "")
                    for name in payload.get("new_class_names", []):
                        if name not in prov and quote:
                            prov[name] = quote[:200]
        except Exception:
            continue
    return prov


def detect_wordbag_identity(names: list[str]) -> list[dict]:
    """Classes whose names are the same bag of words."""
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for n in names:
        b = bag(n)
        if len(b) >= 2:  # trivial one-word classes don't count
            buckets[b].append(n)
    return [
        {
            "kind": "wordbag_identity",
            "confidence": "high",
            "members": sorted(group),
            "suggestion": "merge all into a single canonical class",
        }
        for group in buckets.values()
        if len(group) > 1
    ]


def detect_stem_variants(names: list[str]) -> list[dict]:
    """Classes sharing a stem but with different BFO suffixes.

    Flagged for human review; these are often genuinely distinct and
    should not be auto-merged.
    """
    buckets: dict[str, list[str]] = defaultdict(list)
    for n in names:
        stem = extract_stem(n)
        if stem and len(stem) >= 4:
            buckets[stem].append(n)
    return [
        {
            "kind": "stem_variant",
            "confidence": "review_required",
            "stem": stem,
            "members": sorted(group),
            "suggestion": (
                "these share a root concept but have different BFO suffixes; "
                "usually DO NOT merge, as they express different aspects "
                "(process vs role vs relation, etc.)"
            ),
        }
        for stem, group in buckets.items()
        if len(group) > 1
    ]


def detect_embedding_clusters(
    names: list[str], threshold: float = 0.92
) -> list[dict]:
    """Classes whose label embeddings are very similar.

    Requires sentence-transformers; returns [] if unavailable.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        print("  [skip] sentence-transformers not installed; run: "
              "pip install sentence-transformers")
        return []

    # Use a small, fast model
    model = SentenceTransformer("all-MiniLM-L6-v2")

    # Turn CamelCase names into readable strings for embedding
    labels = [" ".join(camel_split(n)) for n in names]
    embeddings = model.encode(labels, show_progress_bar=True,
                              convert_to_numpy=True, normalize_embeddings=True)

    # Pairwise cosine similarity via dot product (embeddings already normalized)
    sim = embeddings @ embeddings.T

    # Find clusters above threshold using simple union-find
    n = len(names)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        pa, pb = find(a), find(b)
        if pa != pb:
            parent[pa] = pb

    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    results = []
    for cluster in clusters.values():
        if len(cluster) < 2:
            continue
        members = [names[i] for i in cluster]
        # Skip clusters already found by wordbag detection
        bags = {bag(m) for m in members}
        if len(bags) == 1:
            continue
        # Report the minimum pairwise similarity in the cluster
        min_sim = float(min(
            sim[i, j] for i in cluster for j in cluster if i != j
        )) if len(cluster) > 1 else 1.0
        confidence = "high" if min_sim >= 0.96 else "medium"
        results.append({
            "kind": "embedding_cluster",
            "confidence": confidence,
            "min_similarity": round(min_sim, 3),
            "members": sorted(members),
            "suggestion": (
                "high-similarity labels; review manually. Often these are "
                "near-synonyms that should merge, but check source quotes."
            ),
        })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-embeddings", action="store_true",
                    help="Also run embedding-based detection (slower)")
    ap.add_argument("--out", default="evaluation/canonicalization_report.json",
                    help="Path to write the report JSON")
    ap.add_argument("--threshold", type=float, default=0.92,
                    help="Cosine similarity threshold for embedding clusters")
    args = ap.parse_args()

    print(f"Loading working ontology from {config.WORKING_PATH} ...")
    mgr = OntologyManager(
        bfo_path=config.BFO_PATH,
        working_path=config.WORKING_PATH,
        seed_path=config.SEED_PATH,
    )

    classes = list(mgr.working.classes())
    names = [c.name for c in classes]
    print(f"Loaded {len(names)} classes.")
    print()

    print("Loading provenance from session logs ...")
    provenance = load_provenance(config.SESSIONS_DIR)
    print(f"Found source quotes for {len(provenance)} classes.")
    print()

    print("[1/3] Detecting wordbag-identity merges ...")
    wb = detect_wordbag_identity(names)
    print(f"  {len(wb)} buckets found")

    print("[2/3] Detecting stem variants (for review) ...")
    sv = detect_stem_variants(names)
    print(f"  {len(sv)} stem groups found")

    eb = []
    if args.with_embeddings:
        print(f"[3/3] Running embedding similarity (threshold={args.threshold}) ...")
        eb = detect_embedding_clusters(names, threshold=args.threshold)
        print(f"  {len(eb)} clusters found")
    else:
        print("[3/3] Embedding detection skipped (pass --with-embeddings to enable)")

    # Attach provenance
    all_candidates = wb + sv + eb
    for cand in all_candidates:
        cand["provenance"] = {
            m: provenance.get(m, "") for m in cand["members"]
        }

    # Rank: wordbag high-confidence first, then embedding high, then medium, stem last
    def rank_key(c):
        kind_order = {"wordbag_identity": 0, "embedding_cluster": 1,
                      "stem_variant": 2}
        conf_order = {"high": 0, "medium": 1, "review_required": 2}
        return (kind_order.get(c["kind"], 3),
                conf_order.get(c["confidence"], 3),
                -len(c["members"]))

    all_candidates.sort(key=rank_key)

    report = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "ontology_class_count": len(names),
        "candidates_total": len(all_candidates),
        "summary": {
            "wordbag_identity_count": len(wb),
            "stem_variant_count": len(sv),
            "embedding_cluster_count": len(eb),
            "classes_in_wordbag_merges": sum(len(c["members"]) for c in wb),
        },
        "candidates": all_candidates,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print()
    print(f"Wrote {out}")
    print()

    # Pretty print summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total classes: {len(names)}")
    print(f"Wordbag-identity buckets (HIGH confidence auto-merge): {len(wb)}")
    print(f"  Classes involved: {sum(len(c['members']) for c in wb)}")
    print(f"Stem variants (review, usually DO NOT merge): {len(sv)}")
    print(f"Embedding clusters: {len(eb)}")
    print()
    if wb:
        print("Top 10 wordbag merges:")
        for c in wb[:10]:
            print(f"  {c['members']}")
    print()
    print("Next step:")
    print(f"  python scripts/execute_canonicalization.py --report {out} --dry-run")


if __name__ == "__main__":
    main()
