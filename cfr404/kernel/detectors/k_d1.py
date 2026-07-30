"""K-D1: a capacity granted in structure that is not available in practice.

This primitive cannot be computed from the regulation text. The regulation grants
every occupant of an L3 assessor role the same capacity to decide; whether that
capacity is *available* to a claimant depends on who they draw, which is a fact about
the world and not about 20 CFR Part 404.

The evidence is the measured inter-adjudicator variance in SSA's published ALJ
disposition statistics. If that data is not present, this detector reports nothing and
the runner records NOT COMPUTED. A claim that the gap exists without the data would be
an assertion, not a detection, and the spec says so explicitly.
"""

import csv
import json
from pathlib import Path

from base import CFR, Flag, local

PRIMITIVE = "K-D1"

DATA = Path(__file__).resolve().parent.parent.parent / "data" / "ssa-outcomes"
RATE_COLUMNS = ("allowance_rate", "fully_favorable_rate", "favorable_rate",
                "Allowance Rate", "ALLOWANCE_RATE", "Percent Favorable")
ID_COLUMNS = ("alj", "ALJ", "adjudicator", "Name", "alj_name", "Hearing Office")


def _load_rates():
    """One (adjudicator, rate) pair per row, from any CSV SSA publishes."""
    rows = []
    for path in sorted(DATA.glob("*.csv")):
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            rdr = csv.DictReader(fh)
            if not rdr.fieldnames:
                continue
            rate_col = next((c for c in RATE_COLUMNS if c in rdr.fieldnames), None)
            id_col = next((c for c in ID_COLUMNS if c in rdr.fieldnames), None)
            if not rate_col or not id_col:
                continue
            for row in rdr:
                try:
                    rows.append((row[id_col], float(str(row[rate_col]).strip("% "))))
                except (TypeError, ValueError):
                    continue
    return rows


def status():
    f = DATA / "acquisition-status.json"
    if f.exists():
        return json.loads(f.read_text())
    return {"acquired": False, "attempts": [], "consequence_if_not_acquired":
            "no acquisition attempt recorded"}


def detect(ctx):
    rows = _load_rates()
    if len(rows) < 30:
        return []   # runner reports NOT COMPUTED and why

    rates = [r for _, r in rows]
    n = len(rates)
    mean = sum(rates) / n
    var = sum((r - mean) ** 2 for r in rates) / (n - 1)
    sd = var ** 0.5
    lo, hi = min(rates), max(rates)
    spread = hi - lo

    # The criteria at L2 are uniform across adjudicators by construction: every
    # adjudicator applies the same Listings and the same sequential evaluation. Any
    # dispersion beyond sampling noise is unexplained by the criteria.
    if sd < 5.0:
        return []

    flags = []
    for act in sorted(ctx.classes, key=str):
        if ctx.locus.get(act) != "L5":
            continue
        effects = {f for p, _q, f in ctx.restrictions(act)
                   if p == CFR["producesEffect"]}
        if not effects:
            continue
        flags.append(Flag(
            primitive=PRIMITIVE, term=str(act), locus="L6", edge="L5->L6",
            section=ctx.section.get(act, "UNATTRIBUTED"),
            evidence=(f"{local(act)} produces {sorted(local(e) for e in effects)}. The L3 "
                      f"roles that perform it hold a uniform grant of capacity under the "
                      f"regulation, but measured allowance rates across {n} adjudicators "
                      f"range {lo:.1f}%-{hi:.1f}% (spread {spread:.1f} points, sd "
                      f"{sd:.1f}). The L2 criteria are identical across adjudicators, so "
                      f"the dispersion is not explained by the criteria."),
            detector="k_d1"))
    return flags
