#!/usr/bin/env python3
"""Phase 3: corpus acquisition.

Fetches 20 CFR Part 404 from the eCFR versioner API as structured XML (never
scraped HTML), stores it unmodified under ``corpus/raw/`` with retrieval date and
SHA-256 recorded in ``corpus/manifest.json``, then chunks it at the section level.

Endpoints are discovered at run time from ``/api/versioner/v1/titles.json`` rather
than hardcoded, because these have moved before.

No commercial legal database text enters this pipeline. eCFR is the sole source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "corpus" / "raw"
CHUNKS = ROOT / "corpus" / "chunks"
MANIFEST = ROOT / "corpus" / "manifest.json"

API = "https://www.ecfr.gov/api/versioner/v1"
TITLE = 20
CHAPTER = "III"
PART = "404"

# Subparts carrying the recognition chain. Selection rationale lives in the README;
# it is a corpus-selection decision and must be reported, not buried.
SUBPARTS = {
    "H": "Evidence (L4 presenting facts)",
    "J": "Determinations, Administrative Review Process, and Reopening (L5 acts, L7 remedy)",
    "P": "Determining Disability and Blindness (L2 criteria, L3 assessor, L6 effect)",
    "Q": "Determinations of Disability (L1 authority delegation to State agencies, L3 assessor)",
}

# Appendices fetched separately: the subpart endpoint does not return them, and
# Appendix 1 is the source of the criteria layer the baseline already extracted.
APPENDICES = {
    "Appendix 1 to Subpart P of Part 404": ("P", "Listing of Impairments (L2 criteria)"),
    "Appendix 2 to Subpart P of Part 404": ("P", "Medical-Vocational Guidelines (L2 criteria)"),
}

UA = {"User-Agent": "cfr404-stratum-d/1.0 (research; contact drkoepsell@gmail.com)"}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def get(url: str, *, tries: int = 4, timeout: int = 180) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:  # network flake
            last = str(exc)
        wait = 2 ** i
        print(f"  retry {i + 1}/{tries} after {last}; sleeping {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def discover_as_of() -> str:
    """Ask the API which date Title 20 is current through. Never hardcode."""
    r = get(f"{API}/titles.json", timeout=60)
    for t in r.json()["titles"]:
        if int(t["number"]) == TITLE:
            date = t.get("up_to_date_as_of") or t.get("latest_issue_date")
            if not date:
                raise RuntimeError("eCFR returned no usable date for Title 20")
            return date
    raise RuntimeError("Title 20 not present in eCFR titles.json")


def strip_ns(tree: etree._Element) -> None:
    for el in tree.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def node_text(el: etree._Element) -> str:
    """Flatten a section element to plain text, one paragraph per line."""
    parts: list[str] = []
    for p in el.iter():
        if p.tag in ("P", "FP", "HD", "HD1", "HD2", "HD3"):
            txt = " ".join("".join(p.itertext()).split())
            if txt:
                parts.append(txt)
    return "\n".join(parts)


def fetch_raw(as_of: str) -> list[dict]:
    RAW.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    struct_url = f"{API}/structure/{as_of}/title-{TITLE}.json?chapter={CHAPTER}&part={PART}"
    r = get(struct_url, timeout=120)
    path = RAW / f"structure-title{TITLE}-part{PART}-{as_of}.json"
    path.write_bytes(r.content)
    entries.append(
        {
            "file": str(path.relative_to(ROOT)),
            "url": struct_url,
            "kind": "structure",
            "sha256": sha256_bytes(r.content),
            "bytes": len(r.content),
            "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    print(f"structure: {len(r.content):,} bytes")

    for sp, why in SUBPARTS.items():
        url = f"{API}/full/{as_of}/title-{TITLE}.xml?chapter={CHAPTER}&part={PART}&subpart={sp}"
        r = get(url)
        path = RAW / f"title{TITLE}-part{PART}-subpart{sp}-{as_of}.xml"
        path.write_bytes(r.content)  # unmodified
        entries.append(
            {
                "file": str(path.relative_to(ROOT)),
                "url": url,
                "kind": "subpart",
                "subpart": sp,
                "rationale": why,
                "sha256": sha256_bytes(r.content),
                "bytes": len(r.content),
                "retrieved_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"subpart {sp}: {len(r.content):,} bytes")
        time.sleep(1.0)  # be polite to a public API

    for ident, (sp, why) in APPENDICES.items():
        url = (f"{API}/full/{as_of}/title-{TITLE}.xml?chapter={CHAPTER}&part={PART}"
               f"&subpart={sp}&appendix={requests.utils.quote(ident)}")
        r = get(url)
        slug = ident.split(" to ")[0].replace(" ", "").lower()
        path = RAW / f"title{TITLE}-part{PART}-{slug}-{as_of}.xml"
        path.write_bytes(r.content)
        entries.append(
            {
                "file": str(path.relative_to(ROOT)),
                "url": url,
                "kind": "subpart",
                "subpart": sp,
                "appendix": ident,
                "rationale": why,
                "sha256": sha256_bytes(r.content),
                "bytes": len(r.content),
                "retrieved_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"{ident}: {len(r.content):,} bytes")
        time.sleep(1.0)

    return entries


def chunk(entries: list[dict]) -> dict[str, dict]:
    """Section-level chunks. Never chunk across sections; section number is the id."""
    CHUNKS.mkdir(parents=True, exist_ok=True)
    for old in CHUNKS.glob("*.json"):
        old.unlink()

    chunks: dict[str, dict] = {}
    for e in entries:
        if e["kind"] != "subpart":
            continue
        tree = etree.parse(str(ROOT / e["file"]))
        root = tree.getroot()
        strip_ns(root)

        if e.get("appendix"):
            # Appendices carry no DIV8 sections. Their stable sub-unit is the
            # body-system heading (HD1), e.g. "1.00 Musculoskeletal Disorders".
            # That is the finest boundary the regulation itself numbers, so it is
            # the appendix analogue of a section and chunks never cross one.
            slug = "App" + e["appendix"].split(" to ")[0].replace("Appendix ", "").strip()
            div9 = next(root.iter("DIV9"), None)
            if div9 is None:
                continue
            cur_id = cur_head = None
            buf: list[str] = []

            def flush(cur_id=None, cur_head=None, buf=None):
                if not cur_id:
                    return
                text = "\n".join(buf)
                sec = f"{slug}-{cur_id}"
                rec = {
                    "chunk_id": sec, "section": sec, "subpart": e["subpart"],
                    "appendix": e["appendix"], "heading": cur_head, "text": text,
                    "chars": len(text), "source_file": e["file"],
                    "source_sha256": e["sha256"], "source_url": e["url"],
                    "chunk_sha256": sha256_bytes(text.encode("utf-8")),
                }
                chunks[sec] = rec
                (CHUNKS / f"{sec}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")

            has_hd1 = next(div9.iter("HD1"), None) is not None
            if not has_hd1:
                # Appendix 2 is a table of grid rules with no internal headings.
                # Its whole text is one chunk; splitting it would cut across rules.
                cur_id = "all"
                cur_head = " ".join(
                    "".join(next(div9.iter("HEAD"), div9).itertext()).split())
            for el in div9.iter():
                if el.tag == "HD1":
                    flush(cur_id, cur_head, buf)
                    head = " ".join("".join(el.itertext()).split())
                    num = head.split(" ", 1)[0].rstrip(".")
                    cur_id, cur_head, buf = num, head, []
                elif el.tag in ("P", "FP", "FP-2", "HD2", "HD3", "CAPTION", "TD", "TH"):
                    txt = " ".join("".join(el.itertext()).split())
                    if txt:
                        buf.append(txt)
            flush(cur_id, cur_head, buf)
            continue

        for div8 in root.iter("DIV8"):
            if div8.get("TYPE") not in ("SECTION", "APPENDIX"):
                continue
            sec = (div8.get("N") or "").strip()
            if not sec:
                continue
            hd = div8.find("HEAD")
            head = " ".join("".join(hd.itertext()).split()) if hd is not None else ""
            head = head.lstrip("§ ").strip()
            # Drop the leading section number from the heading, keep the title.
            if head.startswith(sec):
                head = head[len(sec):].lstrip(" .")
            text = node_text(div8)
            rec = {
                "chunk_id": sec,
                "section": sec,
                "subpart": e["subpart"],
                "heading": head,
                "text": text,
                "chars": len(text),
                "source_file": e["file"],
                "source_sha256": e["sha256"],
                "source_url": e["url"],
            }
            rec["chunk_sha256"] = sha256_bytes(text.encode("utf-8"))
            chunks[sec] = rec
            (CHUNKS / f"{sec}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="override the eCFR currency date")
    ap.add_argument("--chunk-only", action="store_true", help="re-chunk from existing raw files")
    args = ap.parse_args()

    if args.chunk_only:
        manifest = json.loads(MANIFEST.read_text())
        entries = manifest["raw"]
        as_of = manifest["as_of"]
    else:
        as_of = args.as_of or discover_as_of()
        print(f"eCFR currency date for Title {TITLE}: {as_of}")
        entries = fetch_raw(as_of)

    chunks = chunk(entries)
    reserved = sum(1 for c in chunks.values() if "[Reserved]" in c["heading"])
    print(f"chunks: {len(chunks)} ({reserved} reserved/empty)")

    manifest = {
        "source": "eCFR versioner API (https://www.ecfr.gov/api/versioner/v1)",
        "title": TITLE,
        "chapter": CHAPTER,
        "part": PART,
        "as_of": as_of,
        "subparts": SUBPARTS,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "license_note": (
            "Federal regulations are public domain under the edicts-of-government doctrine. "
            "No Westlaw, Lexis, or other commercial database text enters this pipeline."
        ),
        "raw": entries,
        "chunk_count": len(chunks),
        "chunks": {
            k: {
                "file": f"corpus/chunks/{k}.json",
                "subpart": v["subpart"],
                "heading": v["heading"],
                "chars": v["chars"],
                "chunk_sha256": v["chunk_sha256"],
            }
            for k, v in sorted(chunks.items())
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
