#!/usr/bin/env python3
"""Repair library files that redefine BFO_0000196 as "has disposition".

BFO 2020 BFO_0000196 is "bearer of" (inverse of "inheres in", range
specifically dependent continuant). Library seeds declared it as
"has disposition" with ``rdfs:range`` Disposition (BFO_0000016), so any
quality inhering in anything made the ontology inconsistent. The real
"has disposition" relation is RO_0000091. The app strips the bad axioms in
memory on load (OntologyManager._sanitize_bfo_relation_signatures); this
script fixes the files on disk so exports and seed clones are clean too.

For every ontology under the library root it:
  * rewrites the BFO_0000196 block in seed/*.ttl to the BFO 2020 definition;
  * removes, inside the BFO_0000196 ObjectProperty block of working.owl, the
    narrowed range and the "has disposition" label (nothing else changes);
  * writes a ``.bak-<stamp>`` copy next to each file it changes.
Seeds are always fixed (clone_seeds_from copies them into new ontologies).
A finalized ontology's working.owl is only reported unless
--include-finalized is given.

Usage:
  scripts/fix_bfo0000196_seeds.py [--library ontology/library] [--apply]
                                  [--include-finalized]
Without --apply it is a dry run that lists what would change.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

O = "http://purl.obolibrary.org/obo/"
TTL_BLOCK = re.compile(r"obo:BFO_0000196\s+a\s+owl:ObjectProperty\s*;.*?\.[ \t]*\n",
                       re.S)
TTL_FIXED = ("obo:BFO_0000196 a owl:ObjectProperty ;\n"
             "    rdfs:label \"bearer of\" ;\n"
             "    owl:inverseOf obo:BFO_0000197 ;\n"
             "    rdfs:range obo:BFO_0000020 .\n")
XML_BLOCK = re.compile(
    r'(<owl:ObjectProperty rdf:about="http://purl\.obolibrary\.org/obo/'
    r'BFO_0000196">)(.*?)(</owl:ObjectProperty>)', re.S)
XML_BAD_LINES = [
    re.compile(r'[ \t]*<rdfs:range rdf:resource="http://purl\.obolibrary\.org/'
               r'obo/BFO_00000(?!20")\d+"/>[ \t]*\n'),
    re.compile(r'[ \t]*<rdfs:label[^>]*>has disposition</rdfs:label>[ \t]*\n'),
]


def fix_ttl(text: str) -> tuple[str, int]:
    n = 0

    def repl(m):
        nonlocal n
        body = m.group(0)
        if "has disposition" in body or re.search(
                r"rdfs:range\s+obo:BFO_00000(?!20\b)\d+", body):
            n += 1
            return TTL_FIXED
        return body
    return TTL_BLOCK.sub(repl, text), n


def fix_xml(text: str) -> tuple[str, int]:
    n = 0

    def repl(m):
        nonlocal n
        body = m.group(2)
        for pat in XML_BAD_LINES:
            body, k = pat.subn("", body)
            n += k
        return m.group(1) + body + m.group(3)
    return XML_BLOCK.sub(repl, text), n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--library", default="ontology/library", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-finalized", action="store_true")
    a = ap.parse_args(argv)
    if not a.library.is_dir():
        print(f"no library at {a.library}", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {"changed": [], "skipped_finalized": []}
    for lib in sorted(p for p in a.library.iterdir() if p.is_dir()):
        try:
            finalized = json.loads((lib / "manifest.json").read_text()).get(
                "status") == "finalized"
        except (OSError, ValueError):
            finalized = False
        targets = [(f, fix_ttl) for f in sorted((lib / "seed").glob("*.ttl"))]
        if (lib / "working.owl").exists():
            targets.append((lib / "working.owl", fix_xml))
        for path, fixer in targets:
            text = path.read_text(encoding="utf-8")
            new, n = fixer(text)
            if not n:
                continue
            rel = f"{lib.name}/{path.relative_to(lib)}"
            # Seeds are always fixed: clone_seeds_from copies them into new
            # ontologies, which is how the bad definition spread. Only the
            # frozen working.owl of a finalized ontology is left alone.
            if (finalized and not a.include_finalized
                    and path.name == "working.owl"):
                report["skipped_finalized"].append(rel)
                continue
            report["changed"].append({"file": rel, "edits": n})
            if a.apply:
                shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
                path.write_text(new, encoding="utf-8")
    print(json.dumps({"applied": a.apply, **report}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
