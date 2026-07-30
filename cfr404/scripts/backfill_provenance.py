#!/usr/bin/env python3
"""Phase 2: backfill provenance onto the repaired baseline.

The baseline carries no annotation properties at all, so every stamp here has to be
*earned* from the corpus rather than asserted. A class is traced to a section only
when its label (or a defensible variant) occurs verbatim in that section's text.
Where no trace exists the class is stamped ``UNATTRIBUTED`` / confidence ``low`` /
``approved false`` rather than guessed at, and the coverage rate is reported.

Reads   ontology/cfr404-base.owl, corpus/chunks/*.json
Writes  ontology/cfr404-base.owl (in place), ontology/phase2-backfill-report.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import BFO, CFR, ROOT, ancestors, load, local, named_classes  # noqa: E402

BASE = ROOT / "ontology" / "cfr404-base.owl"
CHUNKS = ROOT / "corpus" / "chunks"
REPORT = ROOT / "ontology" / "phase2-backfill-report.json"
VOCAB_IMPORT = URIRef("http://davidkoepsell.com/cfr404/kernel")

UNATTRIBUTED = "UNATTRIBUTED"

# Sections whose subject matter makes a criteria reading correct when a GDC lands there.
CRITERIA_SECTIONS = re.compile(r"^(App1|App2|404\.152[0-6]|404\.1594|404\.1525|404\.1526)")
# Subpart J is the act/remedy machinery. Reopening and res judicata are the repair paths.
REMEDY_SECTIONS = re.compile(
    r"^404\.(9(0[0-9]|1[0-9]|2[0-9]|3[0-9]|4[0-9]|5[0-9]|6[0-9]|7[0-9]|8[0-9]|9[0-9]))")
REOPENING = re.compile(r"^404\.(98[7-9]|99[0-6]|957)")

_norm_re = re.compile(r"[^a-z0-9]+")


def norm(s: str) -> str:
    return " " + _norm_re.sub(" ", s.lower()).strip() + " "


def variants(label: str) -> list[str]:
    """Label plus a small set of defensible surface variants. No synonym invention."""
    out = [label]
    # Regulations write "disorders of the skeletal spine"; ontologies write
    # "Skeletal Spine Disorder". Allow the singular/plural swap only.
    if label.endswith("y"):
        out.append(label[:-1] + "ies")
    elif not label.endswith("s"):
        out.append(label + "s")
    return out


# Words that carry no clinical or institutional content on their own. A sub-phrase
# consisting only of these is not a trace of anything.
SCAFFOLD_WORDS = {
    "process", "processes", "assessment", "disorder", "disorders", "disease", "diseases",
    "impairment", "impairments", "limitation", "limitations", "function", "functioning",
    "structure", "system", "activity", "state", "status", "period", "event", "entity",
    "role", "quality", "site", "realization", "disruption", "reduction", "development",
    "failure", "deficit", "dysfunction", "abnormality", "abnormal", "of", "the", "and",
}


def subphrases(label: str) -> list[str]:
    """Contiguous sub-phrases of the label, longest first.

    Used only for the weak tier. A hit means the regulation says *part* of what the
    class name says, which is a weaker claim than a full verbatim trace and is
    recorded as such.
    """
    words = label.split()
    out = []
    for length in range(len(words) - 1, 0, -1):
        for start in range(0, len(words) - length + 1):
            sub = words[start:start + length]
            if all(w.lower() in SCAFFOLD_WORDS for w in sub):
                continue
            joined = " ".join(sub)
            if len(joined) >= 8 or len(sub) >= 2:
                out.append(joined)
    return out


def load_chunks() -> dict[str, dict]:
    chunks = {}
    for f in sorted(CHUNKS.glob("*.json")):
        c = json.loads(f.read_text())
        c["ntext"] = norm(c["text"])
        c["nhead"] = norm(c["heading"])
        chunks[c["chunk_id"]] = c
    return chunks


def quote_for(chunk: dict, needle: str, max_words: int = 38) -> str:
    """Pull a verbatim window of the source text around the match, under 40 words."""
    text = chunk["text"]
    m = re.search(re.escape(needle), text, re.IGNORECASE)
    if not m:
        # fall back to the first sentence of the section
        words = text.split()
        return " ".join(words[:max_words])
    start = max(0, m.start() - 160)
    end = min(len(text), m.end() + 160)
    window = text[start:end].replace("\n", " ")
    words = window.split()
    if len(words) > max_words:
        # centre the window on the match
        words = words[:max_words]
    return " ".join(words).strip()


def locus_for(g, cls, section: str, anc: set) -> tuple[str, str]:
    """Assign a chain locus by grounding + traced section. Returns (locus, why)."""
    if BFO["role"] in anc:
        return "L3", "grounded as a BFO role, so it is an assessor-side institutional term"
    if section != UNATTRIBUTED:
        if REOPENING.search(section):
            return "L7", f"traced to {section}, a reopening/res-judicata repair path"
        if REMEDY_SECTIONS.search(section):
            if BFO["process"] in anc:
                return "L5", f"traced to {section} (Subpart J) and grounded as a process"
            return "L7", f"traced to {section}, Subpart J administrative review machinery"
        if CRITERIA_SECTIONS.search(section) and BFO["gdc"] in anc:
            return "L2", (f"traced to {section} and grounded as a generically dependent "
                          "continuant, which is the L2 grounding")
    if BFO["gdc"] in anc:
        return "L2", "grounded as a generically dependent continuant (criteria grounding)"
    return "L0", ("medical or biological substrate: the regulation refers to it but does "
                  "not constitute it, so it occupies no link of the recognition chain")


def main() -> int:
    g = load(BASE)
    g.add((URIRef("http://davidkoepsell.com/cfr404"), OWL.imports, VOCAB_IMPORT))
    chunks = load_chunks()
    classes = sorted(named_classes(g), key=str)

    stats = Counter()
    per_class: dict[str, dict] = {}
    ambiguous_examples: list[dict] = []

    for c in classes:
        labels = [str(x) for x in g.objects(c, RDFS.label)] or [local(c)]
        label = labels[0]
        anc = ancestors(g, c)

        def search(term: str) -> dict[str, tuple[int, int]]:
            nv = norm(term).strip()
            if len(nv) < 5:
                return {}
            pat = " " + nv + " "
            found: dict[str, tuple[int, int]] = {}
            for cid, ch in chunks.items():
                n = ch["ntext"].count(pat) + 3 * ch["nhead"].count(pat)
                if n:
                    found[cid] = (n, ch["chars"])
            return found

        # Tier 1: the whole label, verbatim. This is the only tier that can be approved.
        merged: dict[str, tuple[int, int]] = {}
        matched_on = label
        if len(label) >= 5:
            for v in variants(label):
                merged = search(v)
                if merged:
                    matched_on = v
                    break

        # Tier 2: the longest contiguous sub-phrase of the label that occurs verbatim.
        # Records what the regulation actually says, and never counts as approved.
        weak = False
        if not merged:
            for sub in subphrases(label):
                merged = search(sub)
                if merged:
                    matched_on, weak = sub, True
                    break

        if not merged:
            section, conf, approved = UNATTRIBUTED, "low", False
            note = ("no verbatim occurrence of the class label, or of any contentful "
                    "sub-phrase of it, anywhere in the acquired corpus; left unattributed "
                    "rather than guessed")
            quote = None
        elif weak:
            ranked = sorted(merged.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
            section, conf, approved = ranked[0][0], "low", False
            note = (f"no verbatim trace of the full label; traced on the sub-phrase "
                    f"'{matched_on}' in {len(ranked)} section(s), best {section}. The "
                    "remainder of the class name is the extractor's coinage, not the "
                    "regulation's wording, so this is not approved")
            quote = quote_for(chunks[section], matched_on)
        else:
            # Most occurrences wins; ties break toward the smaller (more specific) chunk.
            ranked = sorted(merged.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
            section = ranked[0][0]
            n_sections = len(ranked)
            if n_sections == 1:
                conf, approved = "high", True
                note = f"unique verbatim match in {section}"
            elif n_sections <= 5:
                conf, approved = "medium", True
                note = ("verbatim match in " + str(n_sections) + " sections; best by "
                        "occurrence count is " + section + "; others: "
                        + ", ".join(k for k, _ in ranked[1:]))
                if len(ambiguous_examples) < 25:
                    ambiguous_examples.append(
                        {"class": local(c), "label": label,
                         "candidates": [k for k, _ in ranked]})
            else:
                conf, approved = "low", False
                note = (f"label occurs in {n_sections} sections and is too generic to "
                        f"attribute; best guess {section} recorded but not approved")
            quote = quote_for(chunks[section], label)

        locus, why = locus_for(g, c, section, anc)

        g.add((c, CFR.sourceSection, Literal(section)))
        g.add((c, CFR.extractionConfidence, Literal(conf)))
        g.add((c, CFR.approved, Literal(approved, datatype=XSD.boolean)))
        g.add((c, CFR.chainLocus, Literal(locus)))
        g.add((c, CFR.reviewNote, Literal(note + " | locus: " + why)))
        if section != UNATTRIBUTED:
            g.add((c, CFR.chunkId, Literal(section)))
        if quote:
            g.add((c, CFR.sourceQuote, Literal(quote)))

        tier = "none" if section == UNATTRIBUTED else ("subphrase" if weak else "verbatim")
        stats["total"] += 1
        stats[f"conf_{conf}"] += 1
        stats[f"locus_{locus}"] += 1
        stats[f"tier_{tier}"] += 1
        if section != UNATTRIBUTED:
            stats["traced"] += 1
        if approved:
            stats["approved"] += 1
        per_class[local(c)] = {
            "label": label, "section": section, "confidence": conf,
            "approved": approved, "locus": locus, "tier": tier,
            "matched_on": matched_on if section != UNATTRIBUTED else None,
        }

    g.serialize(destination=str(BASE), format="pretty-xml")

    traced = stats["traced"]
    total = stats["total"]
    by_section = Counter(v["section"] for v in per_class.values())
    report = {
        "phase": 2,
        "input_classes": total,
        "traced_to_a_section": traced,
        "coverage_rate": round(traced / total, 4),
        "approved": stats["approved"],
        "approval_rate": round(stats["approved"] / total, 4),
        "confidence": {k[5:]: v for k, v in stats.items() if k.startswith("conf_")},
        "chain_locus": {k[6:]: v for k, v in stats.items() if k.startswith("locus_")},
        "trace_tier": {
            "verbatim": stats["tier_verbatim"],
            "subphrase": stats["tier_subphrase"],
            "none": stats["tier_none"],
            "verbatim_rate": round(stats["tier_verbatim"] / total, 4),
        },
        "finding": (
            f"{stats['tier_verbatim']} of {total} baseline classes ({100 * stats['tier_verbatim'] / total:.1f}%) "
            f"have a label the regulation actually uses. {stats['tier_subphrase']} more are "
            "traceable only through a sub-phrase, meaning the rest of the class name is the "
            f"extractor's coinage. {stats['tier_none']} have no verbatim basis in the corpus at "
            "all. This is a measurement of translation-artifact load in the baseline, not a "
            "limitation of the matcher: terms like 'Ascites Realization Process' or "
            "'Hyperglycemic Disruption' are BFO scaffolding the extractor minted to hang "
            "restrictions on, and 20 CFR Part 404 does not contain them."
        ),
        "method": (
            "Tier 1 (approvable): the class rdfs:label, or its plural/singular variant, occurs "
            "verbatim in a section's normalized text. Tier 2 (never approved): the longest "
            "contentful contiguous sub-phrase of the label occurs verbatim. Heading matches "
            "count triple. Ties break toward the smaller chunk as the more specific "
            "attribution. No synonym expansion, no embedding similarity, no LLM: every stamp "
            "has to be checkable by a reader with the corpus in hand."
        ),
        "sentinel": {
            "value": UNATTRIBUTED,
            "meaning": "no verbatim trace found; confidence low, approved false",
            "count": by_section.get(UNATTRIBUTED, 0),
        },
        "top_sections": by_section.most_common(25),
        "ambiguous_examples": ambiguous_examples,
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "ontology" / "phase2-per-class.json").write_text(
        json.dumps(per_class, indent=2), encoding="utf-8")

    print(f"classes {total}, traced {traced} ({100 * traced / total:.1f}%), "
          f"approved {stats['approved']} ({100 * stats['approved'] / total:.1f}%)")
    print(f"trace tier: verbatim {stats['tier_verbatim']}, "
          f"sub-phrase only {stats['tier_subphrase']}, none {stats['tier_none']}")
    print("confidence:", {k[5:]: v for k, v in stats.items() if k.startswith("conf_")})
    print("locus:", {k[6:]: v for k, v in sorted(stats.items()) if k.startswith("locus_")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
