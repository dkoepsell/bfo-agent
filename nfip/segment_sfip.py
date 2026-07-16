"""Phase B: deterministic segmentation of the NFIP SFIP Dwelling Form.

Reads 44CFR61NFIP.txt (44 CFR Part 61, Appendix A(1): Dwelling Form) and pulls
the clauses the coverage kernel needs, VERBATIM, each tagged with its CFR/SFIP
citation. No LLM. The verbatim text is retained so every finding can quote the
source clause (extraction rule E-5; the regulatory text is public domain).

Scope: the Dwelling Form only (the first of the three SFIP forms in the file).
We anchor on phrase matches rather than fixed line numbers, and take the FIRST
occurrence so we stay inside Appendix A(1).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Optional

SOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "44CFR61NFIP.txt")
OUT = os.path.join(os.path.dirname(__file__), "sfip_clauses.json")

# Clause type vocabulary (matches the kernel taxonomy).
GRANT, EXCLUSION, CARVEBACK, CONDITION, DEFINITION = (
    "Grant", "Exclusion", "Carveback", "Condition", "Definition",
)


@dataclass
class Clause:
    id: str
    cfr_cite: str
    sfip_section: str
    clause_type: str
    verbatim_text: str
    source_lines: str
    note: str = ""


def _lines() -> list[str]:
    with open(SOURCE, encoding="utf-8", errors="replace") as fh:
        return fh.read().split("\n")


_NOISE = ("44 CFR", "enhanced display", "Insurance Coverage and Rates", "page ")


def _clean(seg: list[str]) -> str:
    """Join a line range into clause text, dropping page-header noise."""
    keep = []
    for ln in seg:
        t = ln.strip()
        if not t:
            continue
        if any(n in t for n in _NOISE):
            continue
        keep.append(t)
    text = " ".join(keep)
    return re.sub(r"\s+", " ", text).strip()


def _find(lines: list[str], pattern: str, limit: int = 1400) -> Optional[int]:
    """First line index (within the Dwelling Form region) matching pattern."""
    rx = re.compile(pattern, re.I)
    for i, ln in enumerate(lines):
        if i > limit:
            break
        if rx.search(ln):
            return i
    return None


def segment() -> list[Clause]:
    lines = _lines()
    out: list[Clause] = []

    def span(start_pat: str, end_pat: str, pad_end: int = 40) -> tuple[str, str]:
        a = _find(lines, start_pat)
        if a is None:
            raise RuntimeError(f"anchor not found: {start_pat!r}")
        b = _find(lines, end_pat, limit=a + pad_end)
        if b is None:
            b = a + pad_end
        return _clean(lines[a:b]), f"L{a}-{b}"

    # II.B  Flood definition (the grant's reach; includes Mudflow at B.1.c).
    txt, loc = span(r"^\s*B\.\s*Flood, as used in this flood insurance policy, means",
                    r"^\s*C\.\s*The following are the other key definitions")
    out.append(Clause("SFIP-DW-II-B", "44 CFR App. A(1) to Pt. 61, II.B", "II.B Definitions - Flood",
                      DEFINITION, txt, loc,
                      "Flood is defined to include Mudflow (II.B.1.c)."))

    # II.C.20 Mudflow definition (the boundary clause: what is / is not mudflow).
    a = _find(lines, r"^\s*20\.\s*Mudflow\.")
    txt = _clean(lines[a:a + 3])
    out.append(Clause("SFIP-DW-II-C-20", "44 CFR App. A(1) to Pt. 61, II.C.20", "II.C.20 Definitions - Mudflow",
                      DEFINITION, txt, f"L{a}-{a+3}",
                      "Defines mudflow as earth carried by a current of water; "
                      "explicitly says landslide/slope failure/saturated soil mass "
                      "moving down a slope are NOT mudflows."))

    # Coverage A insuring clause (the grant).
    a = _find(lines, r"We only pay for direct physical loss by or from flood")
    if a is not None:
        txt = _clean(lines[a:a + 4])
        out.append(Clause("SFIP-DW-III-A", "44 CFR App. A(1) to Pt. 61, III.A", "III Coverage A - Building Property",
                          GRANT, txt, f"L{a-2}-{a+4}",
                          "Primary insuring grant: direct physical loss by or from flood."))

    # Coverage D (Increased Cost of Compliance) grant + the 'subject to' cross-ref.
    a = _find(lines, r"subject to Coverage D Exclusion 5\.g")
    if a is not None:
        txt = _clean(lines[a - 1:a + 2])
        out.append(Clause("SFIP-DW-III-D", "44 CFR App. A(1) to Pt. 61, III Coverage D", "III Coverage D - Increased Cost of Compliance",
                          GRANT, txt, f"L{a-4}-{a+2}",
                          "Grant conditioned on an exclusion by explicit cross-reference "
                          "('subject to Coverage D Exclusion 5.g')."))

    # V.C  Earth Movement exclusion.
    a = _find(lines, r"We do not insure for loss to property caused directly by earth movement")
    b = _find(lines, r"^\s*We do, however, pay for losses from mudflow", limit=a + 20)
    txt = _clean(lines[a:b])
    out.append(Clause("SFIP-DW-V-C", "44 CFR App. A(1) to Pt. 61, V.C", "V Exclusions - Earth Movement",
                      EXCLUSION, txt, f"L{a}-{b}",
                      "Excludes earth movement even if caused by flood; examples include "
                      "earthquake, landslide, land subsidence, sinkholes, gradual erosion."))

    # V.C  carveback (the 'We do, however, pay ...' sentence).
    c = _find(lines, r"specifically insured under our definition of flood", limit=b + 6)
    txt = _clean(lines[b:c + 1])
    out.append(Clause("SFIP-DW-V-C-CB", "44 CFR App. A(1) to Pt. 61, V.C", "V Exclusions - Earth Movement carveback",
                      CARVEBACK, txt, f"L{b}-{c+1}",
                      "Carveback to the earth-movement exclusion: restores mudflow and "
                      "erosion-driven land subsidence 'specifically insured under our "
                      "definition of flood'. modifies -> SFIP-DW-V-C."))

    return out


def main() -> None:
    clauses = segment()
    data = [asdict(c) for c in clauses]
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"form": "SFIP Dwelling Form (44 CFR Part 61, Appendix A(1))",
                   "source": os.path.basename(SOURCE),
                   "clause_count": len(data), "clauses": data}, fh, indent=2)
    print(f"wrote {len(data)} clauses -> {OUT}")
    for c in clauses:
        print(f"  [{c.clause_type:9}] {c.id:16} {c.sfip_section}")


if __name__ == "__main__":
    main()
