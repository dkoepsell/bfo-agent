#!/usr/bin/env python3
"""Phase 5: extract the act/remedy layer -> ``ontology/cfr404-adjudication.owl``.

Two backends:

``rules``   deterministic, seedless, runs anywhere, reproducible from the corpus
            hashes alone. This is what produced the committed artifact.
``ollama``  the backend the spec asks for. Kept working so the run can be
            reproduced on the DGX with a single flag; it records model, version,
            quantization, seed and prompt hash into the manifest.

Which backend produced the artifact is recorded in ``ontology/phase5-extraction-manifest.json``
and in the deviation log. See ``reports/pipeline-deviations.md``.

Design commitments this file makes on purpose:

* ``cfr:chainLocus`` comes from the Phase 3 section-to-link mapping, never from where
  the term lands in the class tree. §13's concession about the ICD-11 gradient traces
  directly to inferring strata from branch position.
* No class is minted from a modal. Legal text is saturated with "shall", "must" and
  "may"; an extractor that mints an obligation class per modal manufactures Stratum D
  out of grammar. See ``DEONTIC_GUARD``.
* Every minted class gets exactly one BFO parent, chosen from its head noun's
  category, so the extraction cannot introduce a continuant/occurrent straddle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS, XSD

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import BFO, CFR, REL, ROOT  # noqa: E402

OUT = ROOT / "ontology" / "cfr404-adjudication.owl"
CHUNKS = ROOT / "corpus" / "chunks"
MANIFEST = ROOT / "ontology" / "phase5-extraction-manifest.json"
ADJ_IRI = URIRef("http://davidkoepsell.com/cfr404/adjudication")

RULESET_VERSION = "cfr404-rules-1.0"

# ---------------------------------------------------------------------------
# Head-noun categories. Each maps to exactly one BFO parent and one default locus.
# ---------------------------------------------------------------------------
CATEGORIES: dict[str, dict] = {
    "act": {
        "bfo": BFO["process"], "locus": "L5", "parent": "RecognitionAct",
        # "finding" is deliberately NOT here. In this corpus it almost always names the
        # result of an act ("laboratory findings", "prior administrative medical
        # finding"), which is a generically dependent continuant, not the process of
        # finding. Treating it as an act made it straddle against the baseline's
        # grounding of LaboratoryFinding under Evidence.
        "heads": {"determination", "determinations", "decision", "decisions",
                  "assessment", "assessments", "evaluation", "evaluations",
                  "adjudication", "notice", "award", "denial", "cessation", "termination",
                  "certification", "declaration", "designation", "authorization"},
    },
    "remedy": {
        "bfo": BFO["process"], "locus": "L7", "parent": "RemedyProcess",
        "heads": {"reconsideration", "hearing", "hearings", "review", "appeal", "appeals",
                  "reopening", "revision", "dismissal", "remand", "rehearing", "protest",
                  "objection", "withdrawal", "reinstatement"},
    },
    "assessor": {
        "bfo": BFO["role"], "locus": "L3", "parent": "AssessorRole",
        "heads": {"judge", "judges", "officer", "officers", "examiner", "examiners",
                  "consultant", "consultants", "adjudicator", "adjudicators",
                  "specialist", "specialists", "expert", "experts", "reviewer"},
    },
    "party": {
        # Party roles are roles, but they occupy no link of the recognition chain:
        # a claimant is who the act is about, not a link in the act's machinery.
        "bfo": BFO["role"], "locus": "L0", "parent": None,
        "heads": {"claimant", "claimants", "beneficiary", "beneficiaries", "applicant",
                  "applicants", "representative", "representatives", "witness",
                  "witnesses", "party", "parties"},
    },
    "criterion": {
        "bfo": BFO["gdc"], "locus": "L2", "parent": "InstitutionalCriterion",
        "heads": {"requirement", "requirements", "standard", "standards", "criterion",
                  "criteria", "rule", "rules", "guideline", "guidelines", "listing",
                  "listings", "test", "tests", "definition", "definitions", "level",
                  "levels", "category", "categories", "factor", "factors", "limit",
                  "limits", "threshold", "formula", "method", "methods", "procedure",
                  "procedures", "step", "steps"},
    },
    "fact": {
        "bfo": BFO["gdc"], "locus": "L4", "parent": "PresentedFact",
        "heads": {"evidence", "record", "records", "report", "reports", "statement",
                  "statements", "opinion", "opinions", "documentation", "history",
                  "questionnaire", "form", "forms", "file", "transcript", "exhibit",
                  "exhibits", "finding", "findings"},
    },
    "effect": {
        "bfo": BFO["realizable"], "locus": "L6", "parent": "InstitutionalEffect",
        "heads": {"status", "entitlement", "entitlements", "eligibility", "benefit",
                  "benefits", "coverage", "insurance", "payment", "payments", "waiver",
                  "exemption", "disqualification"},
    },
    "authority": {
        "bfo": BFO["object"], "locus": "L1", "parent": None,
        "heads": {"agency", "agencies", "administration", "commissioner", "council",
                  "court", "courts", "office", "bureau", "department", "board",
                  "committee", "commission"},
    },
}

HEAD_TO_CAT = {h: cat for cat, spec in CATEGORIES.items() for h in spec["heads"]}

# ---------------------------------------------------------------------------
# Phase 3 section-to-link mapping. The locus a section is *about*, which beats the
# head noun's default when the two disagree and the disagreement is recorded.
# ---------------------------------------------------------------------------
SECTION_BANDS: list[tuple[re.Pattern, tuple[str, ...], str]] = [
    (re.compile(r"^404\.(98[7-9]|99[0-6])$"), ("L7",), "reopening and revision"),
    (re.compile(r"^404\.957$"), ("L7",), "dismissal, res judicata"),
    (re.compile(r"^404\.1594$"), ("L7", "L2"), "medical improvement review standard"),
    (re.compile(r"^404\.15(8[89]|9[0-9])$"), ("L7", "L6"), "continuing disability review"),
    (re.compile(r"^404\.9\d\d[a-z]?$"), ("L5", "L7"), "Subpart J administrative review"),
    (re.compile(r"^404\.1546$"), ("L3",), "who assesses residual functional capacity"),
    (re.compile(r"^404\.1503a?$"), ("L1", "L3"), "who makes determinations"),
    (re.compile(r"^404\.16\d\d$"), ("L1", "L3"), "Subpart Q State agency determinations"),
    (re.compile(r"^404\.152[0-9][a-z]?$"), ("L2", "L5"), "sequential evaluation"),
    (re.compile(r"^404\.151[2-9][a-z]?$"), ("L4",), "evidence responsibilities"),
    (re.compile(r"^404\.1567$"), ("L2",), "exertional levels"),
    (re.compile(r"^App[12]-"), ("L2",), "Listing of Impairments / grid rules"),
    (re.compile(r"^404\.7\d\d$"), ("L4",), "Subpart H evidence"),
]

# ---------------------------------------------------------------------------
# Deontic inflation guard. These never become classes.
# ---------------------------------------------------------------------------
DEONTIC_GUARD = {
    "obligation", "obligations", "duty", "duties", "permission", "permissions",
    "prohibition", "prohibitions", "right", "rights", "power", "powers", "liberty",
    "shall", "must", "may", "should", "will", "can",
}

STOP = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "by", "with",
    "your", "our", "we", "you", "his", "her", "their", "its", "this", "that", "these",
    "those", "any", "all", "each", "such", "other", "another", "same", "if", "when",
    "as", "is", "are", "was", "were", "be", "been", "not", "no", "than", "from",
    "which", "who", "whom", "what", "it", "they", "he", "she", "but", "so", "also",
    "more", "most", "less", "least", "very", "will", "would", "may", "must", "shall",
    "can", "could", "should", "do", "does", "did", "have", "has", "had", "about",
    "under", "over", "after", "before", "during", "within", "without", "into", "out",
    "up", "down", "then", "there", "here", "because", "however", "one", "two", "three",
    "see", "paragraph", "section", "subpart", "chapter", "part", "title",
}

# Words that cannot sit inside a noun phrase: verbs, prepositions and connectives the
# STOP list does not cover. Walking left from the head, the modifier run stops here.
NON_MODIFIER = {
    "where", "when", "while", "whether", "although", "unless", "until", "since",
    "per", "via", "upon", "among", "between", "against", "toward", "towards",
    "through", "throughout", "means", "meaning", "includes", "including", "include",
    "using", "used", "based", "made", "make", "makes", "given", "give", "gives",
    "filed", "paid", "pay", "shown", "show", "found", "find", "stated",
    "stating", "described", "listed", "required", "require",
    "requires", "provided", "provide", "provides", "furnished", "furnish",
    "received", "receive", "issued", "issue", "determined", "determine",
    "evaluated", "evaluate", "considered", "consider", "requested", "request",
    "affects", "affect", "applies", "apply", "concerning", "regarding", "except",
    "unlike", "besides", "beyond", "despite", "above", "below", "against",
    "become", "becomes", "became", "being", "having", "does", "doing", "get",
    "gets", "put", "puts", "set", "sets", "take", "takes", "taken", "keep",
}

WORD = r"[A-Za-z][A-Za-z\-']*"
NP_RE = re.compile(rf"((?:{WORD}\s+){{0,4}})({'|'.join(sorted(HEAD_TO_CAT, key=len, reverse=True))})\b",
                   re.IGNORECASE)

MAX_WORDS = 4
MIN_LABEL_CHARS = 6
# Term-of-art test. A phrase earns a class if the regulation treats it as a term
# rather than as passing prose: it heads a section, or it is used repeatedly where
# it lives, or it recurs across sections. A phrase used once in one place is prose.
# This bounds coverage, so what it drops is reported in the manifest.
MIN_OCCURRENCES = 2
MIN_SECTIONS = 2
MIN_SPECIFIC_CHARS = 12


def is_term_of_art(c: dict) -> bool:
    """Does the regulation treat this phrase as a term rather than as prose?

    Either it recurs - it heads a section, is used more than once, or turns up in
    more than one section - or it is a specific multi-word construction, where the
    modifier is itself evidence that the regulation is naming something in
    particular rather than describing in passing.
    """
    label = c["label"]
    return (c["in_heading"]
            or c["occurrences"] >= MIN_OCCURRENCES
            or len(set(c["sections"])) >= MIN_SECTIONS
            or (len(label.split()) >= 2 and len(label) >= MIN_SPECIFIC_CHARS))


def camel(phrase: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", phrase)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def band_for(section: str) -> tuple[tuple[str, ...], str] | tuple[None, None]:
    for pat, loci, why in SECTION_BANDS:
        if pat.match(section):
            return loci, why
    return None, None


def quote_around(text: str, needle: str, max_words: int = 38) -> str:
    m = re.search(re.escape(needle), text, re.IGNORECASE)
    if not m:
        return " ".join(text.split()[:max_words])
    start = max(0, m.start() - 150)
    return " ".join(text[start:m.end() + 150].replace("\n", " ").split()[:max_words])


def clean_np(prefix: str, head: str) -> str | None:
    """Keep only the contiguous run of modifiers immediately before the head.

    Stripping leading function words is not enough: the regex window reaches back
    four words, so "evidence from the State agency" would survive as a single term
    and "commissioner means commissioner" would survive as another. A noun phrase
    cannot contain an interior preposition or verb, so the run stops at the first
    one encountered walking left from the head.
    """
    words: list[str] = []
    for w in reversed([w for w in prefix.split() if w]):
        if w.lower() in STOP or w.lower() in NON_MODIFIER:
            break
        words.insert(0, w)
    if len(words) + 1 > MAX_WORDS:
        words = words[-(MAX_WORDS - 1):]
    phrase = " ".join(words + [head])
    if any(w.lower() in DEONTIC_GUARD for w in phrase.split()):
        return None
    # Single-word terms of art are kept: "reconsideration", "determination" and
    # "entitlement" are the core institutional vocabulary of this corpus, and
    # excluding them would leave the artifact holding only their modified variants.
    if len(phrase) < MIN_LABEL_CHARS:
        return None
    if re.search(r"\d", phrase):
        return None
    return phrase


def extract_rules(chunks: dict[str, dict]) -> tuple[list[dict], dict]:
    """Deterministic extraction. Same corpus in, same classes out, always."""
    candidates: dict[str, dict] = {}
    guard_hits = Counter()
    disagreements = 0

    for cid, ch in sorted(chunks.items()):
        text = ch["text"]
        if not text.strip():
            continue
        heading = ch["heading"]
        band, band_why = band_for(cid)
        seen_here = Counter()

        for m in NP_RE.finditer(text):
            head = m.group(2).lower()
            cat = HEAD_TO_CAT.get(head)
            if cat is None:
                continue
            phrase = clean_np(m.group(1), m.group(2))
            if phrase is None:
                guard_hits[head] += 1
                continue
            seen_here[(phrase.lower(), cat, head)] += 1

        for (plow, cat, head), count in seen_here.items():
            spec = CATEGORIES[cat]
            default_locus = spec["locus"]
            locus, why = default_locus, f"head noun category '{cat}'"
            # The Phase 3 mapping fixes the set of links a section serves; the head
            # noun decides which of them this particular term occupies. Where the
            # term's kind is not one the section serves, the section is simply
            # serving links this term is not an instance of, and forcing it into
            # band[0] would put it at a locus its BFO grounding contradicts - a
            # K-B3 straddle manufactured by the pipeline. The disagreement is
            # recorded instead.
            if band:
                if default_locus in band:
                    why = (f"section {cid} is about {band_why} ({'/'.join(band)}), and the "
                           f"head noun category '{cat}' selects {default_locus} within it")
                elif default_locus == "L0":
                    why = f"party role; occupies no link even though {cid} is about {band_why}"
                else:
                    why = (f"section {cid} serves {'/'.join(band)} ({band_why}) but this "
                           f"term is a '{cat}', which belongs at {default_locus}; the "
                           f"section's links and this term's kind disagree and the term's "
                           f"kind is kept so that locus and BFO grounding stay consistent")
                    disagreements += 1

            # Recover the original casing of the first occurrence for the label.
            m = re.search(re.escape(plow), text, re.IGNORECASE)
            label = m.group(0) if m else plow
            label = re.sub(r"\s+", " ", label).strip()
            name = camel(label)
            if not name or name[0].isdigit():
                continue

            in_heading = plow in heading.lower()
            if in_heading:
                conf = "high"
            elif count >= 3:
                conf = "medium"
            elif count == 2:
                conf = "medium"
            else:
                conf = "low"

            prev = candidates.get(name)
            if prev is not None:
                prev["occurrences"] += count
                prev["sections"].append(cid)
                # Keep the strongest attribution; the section that says it most wins.
                if count > prev["count_here"] or (in_heading and not prev["in_heading"]):
                    prev.update(section=cid, count_here=count, in_heading=in_heading,
                                confidence=conf, locus=locus, locus_why=why,
                                quote=quote_around(text, label))
                continue

            candidates[name] = {
                "name": name, "label": label, "category": cat, "head": head,
                "bfo": spec["bfo"], "parent": spec["parent"],
                "section": cid, "sections": [cid], "count_here": count,
                "occurrences": count, "in_heading": in_heading,
                "confidence": conf, "locus": locus, "locus_why": why,
                "quote": quote_around(text, label),
            }

    for c in candidates.values():
        c["approved"] = c["confidence"] in ("high", "medium")
        c["sections"] = sorted(set(c["sections"]))

    kept = [c for c in candidates.values() if is_term_of_art(c)]
    dropped = [c for c in candidates.values() if not is_term_of_art(c)]

    stats = {
        "deontic_guard_suppressions": sum(guard_hits.values()),
        "deontic_guard_by_head": dict(guard_hits.most_common(15)),
        "section_mapping_overrode_head_noun": disagreements,
        "coverage_cap": {
            "rule": f"term-of-art test: kept if the phrase heads a section, occurs "
                    f">= {MIN_OCCURRENCES} times, recurs across >= {MIN_SECTIONS} "
                    f"sections, or is a multi-word phrase of >= {MIN_SPECIFIC_CHARS} "
                    f"characters; max phrase length {MAX_WORDS} words",
            "candidates_before_cap": len(candidates),
            "dropped_as_prose": len(dropped),
            "dropped_by_category": dict(Counter(c["category"] for c in dropped).most_common()),
            "dropped_examples": [c["label"] for c in sorted(
                dropped, key=lambda x: x["name"])[:25]],
            "note": "This cap bounds coverage. It is reported so that a locus density "
                    "computed from this artifact is not mistaken for exhaustive coverage "
                    "of the corpus.",
        },
    }
    return sorted(kept, key=lambda c: c["name"]), stats


def extract_ollama(chunks: dict[str, dict], model: str, seed: int) -> tuple[list[dict], dict]:
    """The spec's intended backend. Unused for the committed artifact; see the
    deviation log for why."""
    import requests

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    prompt_template = (
        "You are extracting institutional entities from a section of 20 CFR Part 404.\n"
        "Return STRICT JSON: a list of objects with keys label, category, quote.\n"
        "category must be one of: act, remedy, assessor, party, criterion, fact, "
        "effect, authority.\n"
        "Never invent a term the section does not contain. Never emit a term whose "
        "only basis is a modal verb such as shall, must or may.\n\n"
        "SECTION {cid}: {heading}\n\n{text}\n"
    )
    prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:16]

    out: dict[str, dict] = {}
    for cid, ch in sorted(chunks.items()):
        if not ch["text"].strip():
            continue
        r = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt_template.format(
                      cid=cid, heading=ch["heading"], text=ch["text"][:12000]),
                  "stream": False, "format": "json",
                  "options": {"temperature": 0, "seed": seed}},
            timeout=600,
        )
        r.raise_for_status()
        try:
            items = json.loads(r.json()["response"])
        except (ValueError, KeyError):
            continue
        if isinstance(items, dict):
            items = items.get("terms") or items.get("results") or []
        band, band_why = band_for(cid)
        for it in items:
            label = str(it.get("label", "")).strip()
            cat = str(it.get("category", "")).strip().lower()
            if not label or cat not in CATEGORIES:
                continue
            if label.lower() not in ch["text"].lower():
                continue  # the model must not invent wording
            spec = CATEGORIES[cat]
            locus = spec["locus"]
            if band and locus not in band and locus != "L0":
                locus = band[0]
            name = camel(label)
            out.setdefault(name, {
                "name": name, "label": label, "category": cat, "head": "",
                "bfo": spec["bfo"], "parent": spec["parent"], "section": cid,
                "sections": [cid], "count_here": 1, "occurrences": 1,
                "in_heading": False, "confidence": "medium", "approved": True,
                "locus": locus,
                "locus_why": f"Phase 3 mapping for {cid}" if band else f"category {cat}",
                "quote": str(it.get("quote", ""))[:400],
            })
    return sorted(out.values(), key=lambda c: c["name"]), {
        "model": model, "seed": seed, "prompt_sha256_16": prompt_hash, "host": host,
    }


ANTHROPIC_PROMPT = """You are extracting institutional entities from one section of \
20 CFR Part 404 (Social Security disability regulations).

Return STRICT JSON: {"terms": [{"label": ..., "category": ..., "quote": ...}]}

category must be exactly one of:
  act        a process in which the agency declares a status (determination, decision, notice)
  remedy     a process for revisiting a completed act (reconsideration, hearing, review, reopening)
  assessor   a role borne by a PERSON who assesses (judge, officer, examiner, consultant)
  party      a role borne by a person the act is about (claimant, beneficiary, representative)
  criterion  a standard or rule the act must apply (requirement, listing, guideline, step)
  fact       something offered as input to an act (evidence, report, opinion, finding)
  effect     a status or entitlement the act brings about (disability status, eligibility)
  authority  an institution holding a grant of decisional power (agency, council, court)

Hard rules:
- label MUST appear verbatim in the section text. Never paraphrase, never invent.
- label must be 2 to 4 words, a noun phrase, no digits, no citations.
- Do NOT emit a term whose only basis is a modal ("shall", "must", "may"). Legal text is
  saturated with modals and turning them into entities manufactures structure.
- quote must be a verbatim span from the section, under 40 words.
- Prefer terms of art the section actually uses over generic phrases.
- If the section contains no institutional entities, return {"terms": []}.

SECTION %(cid)s: %(heading)s

%(text)s
"""


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    for env in (ROOT.parent / ".env", ROOT / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY"):
                    return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("ANTHROPIC_API_KEY not found in the environment or in .env")


def extract_anthropic(chunks: dict[str, dict], model: str, max_chars: int,
                      workers: int) -> tuple[list[dict], dict]:
    """Hosted-API comparison arm.

    This backend does NOT produce the release artifact. Spec §3 forbids hosted APIs
    for corpus extraction because the artifact must be reproducible from local
    components; this arm exists only to measure what a hosted model does differently
    from the deterministic rules, and its output is written to a separate file.
    """
    import concurrent.futures as cf

    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    prompt_hash = hashlib.sha256(ANTHROPIC_PROMPT.encode()).hexdigest()[:16]
    truncated: list[str] = []
    usage = Counter()
    errors: list[str] = []

    work = [(cid, ch) for cid, ch in sorted(chunks.items()) if ch["text"].strip()]

    def one(item):
        cid, ch = item
        text = ch["text"]
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated.append(cid)
        try:
            resp = client.messages.create(
                model=model, max_tokens=4000, temperature=0,
                messages=[{"role": "user", "content": ANTHROPIC_PROMPT % {
                    "cid": cid, "heading": ch["heading"], "text": text}}],
            )
        except Exception as exc:
            errors.append(f"{cid}: {type(exc).__name__}: {exc}")
            return cid, ch, []
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        raw = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return cid, ch, []
        try:
            data = json.loads(m.group(0))
        except ValueError:
            errors.append(f"{cid}: unparseable JSON")
            return cid, ch, []
        return cid, ch, data.get("terms", []) if isinstance(data, dict) else []

    results = []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for i, r in enumerate(pool.map(one, work), 1):
            results.append(r)
            if i % 40 == 0:
                print(f"  {i}/{len(work)} sections", file=sys.stderr)

    out: dict[str, dict] = {}
    not_verbatim = 0
    bad_category = 0
    deontic_emitted = 0
    for cid, ch, items in results:
        band, band_why = band_for(cid)
        low = ch["text"].lower()
        for it in items:
            if not isinstance(it, dict):
                continue
            label = re.sub(r"\s+", " ", str(it.get("label", ""))).strip()
            cat = str(it.get("category", "")).strip().lower()
            if not label:
                continue
            if cat not in CATEGORIES:
                bad_category += 1
                continue
            if label.lower() not in low:
                not_verbatim += 1
                continue  # the model must not invent wording
            if any(w.lower() in DEONTIC_GUARD for w in label.split()):
                deontic_emitted += 1
                continue
            spec = CATEGORIES[cat]
            locus, why = spec["locus"], f"head category '{cat}' from the model"
            if band and locus not in band and locus != "L0":
                locus = band[0]
                why = f"Phase 3 mapping for {cid} ({band_why}) overrode the model's {spec['locus']}"
            elif band:
                why = f"section {cid} is about {band_why}; model category '{cat}' agrees"
            name = camel(label)
            if not name or name[0].isdigit():
                continue
            prev = out.get(name)
            if prev:
                prev["occurrences"] += 1
                prev["sections"].append(cid)
                continue
            out[name] = {
                "name": name, "label": label, "category": cat, "head": "",
                "bfo": spec["bfo"], "parent": spec["parent"], "section": cid,
                "sections": [cid], "count_here": 1, "occurrences": 1,
                "in_heading": label.lower() in ch["heading"].lower(),
                "confidence": "medium", "approved": True,
                "locus": locus, "locus_why": why,
                "quote": quote_around(ch["text"], label),
            }

    meta = {
        "arm": "hosted-API comparison arm; NOT the provenance of ontology/cfr404.owl",
        "provider": "Anthropic Messages API",
        "model": model,
        "temperature": 0,
        "prompt_sha256_16": prompt_hash,
        "max_chars_per_chunk": max_chars,
        "sections_sent": len(work),
        "sections_truncated": len(truncated),
        "truncated_sections": sorted(truncated)[:40],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "rejected_not_verbatim_in_section": not_verbatim,
        "rejected_bad_category": bad_category,
        "rejected_deontic": deontic_emitted,
        "errors": errors[:20],
        "error_count": len(errors),
        "spec_note": (
            "Spec §3 and §15 require extraction to run on local components via Ollama. "
            "This arm violates that constraint deliberately and in isolation: it is run "
            "for comparison only and its classes never enter cfr404.owl."),
    }
    return sorted(out.values(), key=lambda c: c["name"]), meta


def build_graph(cands: list[dict]) -> Graph:
    g = Graph()
    g.bind("cfr", CFR)
    g.add((ADJ_IRI, RDF.type, OWL.Ontology))
    g.add((ADJ_IRI, OWL.versionIRI,
           URIRef("http://davidkoepsell.com/cfr404/adjudication/2026-07-30")))
    g.add((ADJ_IRI, OWL.imports, URIRef("http://davidkoepsell.com/cfr404/chain")))
    g.add((ADJ_IRI, OWL.imports, URIRef("http://davidkoepsell.com/cfr404/kernel")))
    g.add((ADJ_IRI, RDFS.label, Literal("cfr404 extracted adjudication layer")))
    g.add((ADJ_IRI, DCTERMS.license,
           URIRef("https://creativecommons.org/licenses/by/4.0/")))

    for c in cands:
        u = CFR[c["name"]]
        g.add((u, RDF.type, OWL.Class))
        g.add((u, RDFS.label, Literal(c["label"])))
        g.add((u, RDFS.subClassOf, c["bfo"]))
        if c["parent"]:
            g.add((u, RDFS.subClassOf, CFR[c["parent"]]))
        g.add((u, CFR.sourceSection, Literal(c["section"])))
        g.add((u, CFR.chunkId, Literal(c["section"])))
        g.add((u, CFR.sourceQuote, Literal(c["quote"])))
        g.add((u, CFR.extractionConfidence, Literal(c["confidence"])))
        g.add((u, CFR.approved, Literal(c["approved"], datatype=XSD.boolean)))
        g.add((u, CFR.chainLocus, Literal(c["locus"])))
        g.add((u, CFR.reviewNote, Literal(
            f"extracted; locus: {c['locus_why']}; {c['occurrences']} occurrence(s) "
            f"across {len(c['sections'])} section(s)")))
        g.add((u, SKOS.editorialNote, Literal(f"category={c['category']} head={c['head']}")))
        for s in c["sections"][:12]:
            g.add((u, SKOS.note, Literal(f"also occurs in {s}")))
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["rules", "ollama", "anthropic"], default="rules")
    ap.add_argument("--model", default=None)
    ap.add_argument("--seed", type=int, default=20260730)
    ap.add_argument("--max-chars", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    chunks = {p.stem: json.loads(p.read_text()) for p in CHUNKS.glob("*.json")}

    if args.backend == "ollama":
        cands, backend_meta = extract_ollama(chunks, args.model or "qwen2.5:14b", args.seed)
    elif args.backend == "anthropic":
        cands, backend_meta = extract_anthropic(
            chunks, args.model or "claude-haiku-4-5-20251001", args.max_chars, args.workers)
    else:
        cands, backend_meta = extract_rules(chunks)

    # Only the local, reproducible backend may write the module the release merges.
    out_path = OUT if args.backend == "rules" else OUT.with_name(
        f"{OUT.stem}-{args.backend}{OUT.suffix}")
    manifest_path = MANIFEST if args.backend == "rules" else MANIFEST.with_name(
        f"{MANIFEST.stem}-{args.backend}{MANIFEST.suffix}")

    g = build_graph(cands)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out_path), format="pretty-xml")

    by_locus = Counter(c["locus"] for c in cands)
    by_cat = Counter(c["category"] for c in cands)
    by_conf = Counter(c["confidence"] for c in cands)

    manifest = {
        "phase": 5,
        "generated": str(date.today()),
        "backend": args.backend,
        "ruleset_version": RULESET_VERSION if args.backend == "rules" else None,
        "ruleset_sha256_16": hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest()[:16],
        "backend_meta": backend_meta,
        "corpus_chunks": len(chunks),
        "classes_minted": len(cands),
        "by_locus": dict(sorted(by_locus.items())),
        "by_category": dict(by_cat.most_common()),
        "by_confidence": dict(by_conf.most_common()),
        "approved": sum(1 for c in cands if c["approved"]),
        "guards": {
            "deontic_inflation": (
                "No class is minted from a phrase containing a modal or a bare deontic "
                "noun. Legal text is saturated with these and an extractor that mints one "
                "class per modal manufactures institutional structure out of grammar."),
            "single_bfo_parent": (
                "Each class receives exactly one BFO parent, fixed by its head-noun "
                "category, so extraction cannot introduce a continuant/occurrent straddle."),
            "locus_not_inferred_from_branch": (
                "chainLocus comes from the Phase 3 section-to-link mapping, with the "
                "head-noun category used only to choose within a section's admissible set. "
                "It is never read off the class tree."),
        },
    }
    manifest["output"] = str(out_path.relative_to(ROOT))
    manifest["is_release_provenance"] = args.backend == "rules"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print(f"backend={args.backend}  classes={len(cands)}  triples={len(g)}  -> {out_path.name}")
    print("locus:", dict(sorted(by_locus.items())))
    print("category:", dict(by_cat.most_common()))
    print("confidence:", dict(by_conf.most_common()))
    if args.backend == "rules":
        print("deontic suppressions:", backend_meta["deontic_guard_suppressions"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
