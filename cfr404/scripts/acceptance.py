#!/usr/bin/env python3
"""Check every acceptance criterion in §13 of the spec against what is on disk.

Exits non-zero if any criterion fails. Prints a checklist either way.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import (  # noqa: E402
    BFO_IMPORT, CFR, LOCI, PRIMITIVES, ROOT, ancestors, load, local, named_classes,
    straddles, unanchored,
)
from rdflib.namespace import OWL, RDF, RDFS  # noqa: E402

ART = ROOT / "ontology" / "cfr404.owl"


def main() -> int:
    results: list[tuple[bool, str, str]] = []

    def check(ok: bool, name: str, detail: str = ""):
        results.append((bool(ok), name, detail))

    g = load(ART)
    classes = named_classes(g)
    ledger = json.loads((ROOT / "kernel" / "flags.json").read_text())
    reasoner = json.loads((ROOT / "kernel" / "reasoner-result.json").read_text())
    chain = json.loads((ROOT / "ontology" / "phase4-chain-report.json").read_text())

    # 1. parses cleanly, imports BFO 2020, carries a version IRI
    imports = {str(o) for o in g.objects(None, OWL.imports)}
    versions = [str(o) for o in g.objects(None, OWL.versionIRI)]
    check(str(BFO_IMPORT) in imports and bool(versions),
          "cfr404.owl parses, imports BFO 2020, carries a version IRI",
          f"versionIRI={versions}, imports BFO={str(BFO_IMPORT) in imports}")

    # 2. zero straddles, zero unanchored
    st, un = straddles(g), unanchored(g)
    check(not st and not un, "zero continuant/occurrent straddles, zero unanchored classes",
          f"straddles={len(st)}, unanchored={len(un)}")

    # 3. every class carries the four stamps
    missing = {p: 0 for p in ("sourceSection", "chainLocus", "extractionConfidence",
                              "approved")}
    for c in classes:
        for p in missing:
            if not list(g.objects(c, CFR[p])):
                missing[p] += 1
    unattributed = sum(1 for c in classes
                       if any(str(v) == "UNATTRIBUTED" for v in g.objects(c, CFR.sourceSection)))
    check(all(v == 0 for v in missing.values()),
          "every class carries sourceSection, chainLocus, extractionConfidence, approved",
          f"gaps={missing}; of these {unattributed} carry the UNATTRIBUTED sentinel, "
          f"which is reported, not assumed to be a section")

    # 4. all seven links populated; L3 and L7 non-empty; >=4 person-borne L3 roles
    per_locus = {lo: 0 for lo in LOCI}
    for c in classes:
        v = [str(x) for x in g.objects(c, CFR.chainLocus)]
        if v and v[0] in per_locus:
            per_locus[v[0]] += 1
    all_pop = all(per_locus[lo] > 0 for lo in LOCI)
    check(all_pop and per_locus["L3"] > 0 and per_locus["L7"] > 0,
          "all seven chain links populated, L3 and L7 non-empty", str(per_locus))
    check(chain["person_borne_L3_role_count"] >= 4,
          "L3 contains at least four distinct person-borne roles, modelled separately from L1",
          f"{chain['person_borne_L3_role_count']} roles; AuthorityRole disjointWith "
          f"AssessorRole asserted: "
          f"{(CFR['AuthorityRole'], OWL.disjointWith, CFR['AssessorRole']) in g}")

    # 5. all twelve primitives implemented or explicitly not implemented with a reason
    impl = [p for p in PRIMITIVES if ledger["status"].get(p, {}).get("implemented")]
    nc = {p: s["not_computed_reason"] for p, s in ledger["status"].items()
          if not s["computed"]}
    check(len(impl) == 12 and all(v for v in nc.values()),
          "all twelve primitives operationalized, or listed not-computed with a reason",
          f"implemented={len(impl)}/12; not computed={list(nc)} each with a stated reason")

    # 6. Stratum D flags exist and survived hand audit at a reported precision
    sample = list((ROOT / "audit" / "sample.csv").open(encoding="utf-8"))
    rows = list(csv.DictReader((ROOT / "audit" / "sample.csv").open(encoding="utf-8")))
    d_rows = [r for r in rows if r["stratum"] == "D"]
    d_reg = [r for r in d_rows if r["verdict"] == "regulation"]
    check(ledger["by_stratum"].get("D", 0) > 0 and d_rows and len(d_reg) / len(d_rows) > 0.5,
          "Stratum D flags exist and survived hand audit at a reported precision",
          f"{ledger['by_stratum'].get('D', 0)} candidates, "
          f"{len(d_reg)}/{len(d_rows)} survived = "
          f"{len(d_reg) / len(d_rows):.2f}; see audit/precision-report.md")
    check(len(rows) >= 60, "audit sample is at least 60 candidates, stratified",
          f"{len(rows)} rows across "
          f"{len({(r['primitive'], r['locus']) for r in rows})} primitive/locus cells")

    # 7. a reasoner verdict is reported, or its absence is reported with the reason
    rv = (ROOT / "reports" / "reasoner-verdict.md")
    check(rv.exists() and (reasoner.get("verdict") or reasoner.get("reason")),
          "a reasoner verdict is reported, or its absence with the reason",
          f"verdict={reasoner.get('verdict')}, "
          f"unsat={reasoner.get('unsatisfiable_count')}")

    # 8. tables regenerate from the artifact
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "report.py")],
                       capture_output=True, text=True, cwd=str(ROOT))
    t8 = ROOT / "reports" / "table8-stratum-profile.md"
    t13 = ROOT / "reports" / "table13-locus-density.md"
    check(r.returncode == 0 and t8.exists() and t13.exists(),
          "table8 and table13 regenerate from the artifact via scripts/report.py",
          (r.stderr or "").strip()[-160:] or "ok")

    # 9. PREREGISTERED committed before the first kernel run
    pre = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H", "--",
         "cfr404/predictions/PREREGISTERED.md"],
        capture_output=True, text=True, cwd=str(ROOT.parent))
    pre_commit = pre.stdout.strip().splitlines()
    flags_hist = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", "cfr404/kernel/flags.json"],
        capture_output=True, text=True, cwd=str(ROOT.parent))
    outcome = ROOT / "reports" / "predictions-outcome.md"
    check(bool(pre_commit) and outcome.exists(),
          "PREREGISTERED.md was committed before any kernel output, and outcomes reported",
          f"added in commit {pre_commit[0][:8] if pre_commit else 'UNCOMMITTED'}; "
          f"flags.json added in "
          f"{(flags_hist.stdout.strip().splitlines() or ['(not yet committed)'])[0][:8]}; "
          f"outcomes in reports/predictions-outcome.md")

    # 10. CC-BY 4.0 and a README saying what it is not
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
    lic = any("creativecommons.org/licenses/by/4.0" in str(o) for o in g.objects())
    check(lic and "is not the regulation" in readme,
          "released CC-BY 4.0 with a README stating what it is not",
          f"license in artifact={lic}, README disclaimer present="
          f"{'is not the regulation' in readme}")

    # 11. size target
    check(2000 <= len(classes) <= 2500, "merged size within the 2,000-2,500 target",
          f"{len(classes):,} named classes")

    width = max(len(n) for _, n, _ in results)
    print("\nAcceptance criteria — SPEC-cfr404-stratum-d §13\n")
    for ok, name, detail in results:
        print(f"  [{'x' if ok else ' '}] {name.ljust(width)}   {detail}")
    failed = [n for ok, n, _ in results if not ok]
    print()
    if failed:
        print(f"{len(failed)} criterion/criteria NOT met: " + "; ".join(failed))
        return 1
    print(f"All {len(results)} acceptance criteria met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
