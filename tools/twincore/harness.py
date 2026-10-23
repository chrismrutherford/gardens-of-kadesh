"""Spec 005 v4: Draft v3 harness measures (T038, FR-020/021/009, SC-002/006).

Pure functions over `twincore_q.messages` trail rows (postgres record
store, FR-011). The full series/report CLI is T018/T019 (v4 harness);
these measures are the v3-aware additions those phases will assemble:

- deny_rate: verdicts / (verdicts + applied commands) per channel-ish
- repeated_identical_denial_rate: verdict rows with repeat > 1 / verdict rows
- verdict_legibility: fraction of verdict rows naming ≥1 allowed alternative
- arc_coverage / arc_max_gap_ticks: program rows carrying an S2 `arc`
- reflex_stale_redispatches: dropped stale in-flight harvest oids (0 ideal)
- superset_presence: s2_in usage rows rendering every FR-009 world section
"""
LEGIBLE_MARKERS = (
    "allowed", "researchable now", "buildable now", "coerced to array",
    "per-tick cap", "already researched", "keep the running story tighter",
)
SUPERSET_SECTIONS = ("arc", "enemy", "asteroids", "build_menu",
                     "research_menu")


def v4_draftv3_measures(rows, own_player=0):
    """Compute Draft v3 measures from trail rows (dicts with kind/payload
    at top level — recorder scan() shape). Returns a flat dict."""
    verdicts = [r for r in rows if r.get("kind") == "validation"
                and r.get("status") == "verdict"]
    commands = [r for r in rows if r.get("kind") == "command"]
    applied = [c for c in commands if c.get("status") == "applied"]
    denied = [c for c in commands if c.get("status") in
              ("engine_denied", "rejected", "blocked")]

    n_verdicts = len(verdicts)
    n_attempts_for_verdicts = n_verdicts + len(applied)
    deny_rate = (n_verdicts / n_attempts_for_verdicts
                 if n_attempts_for_verdicts else 0.0)

    repeats = [v for v in verdicts if int(v.get("repeat") or 1) > 1]
    repeated_rate = (len(repeats) / n_verdicts if n_verdicts else 0.0)

    legible = [v for v in verdicts
               if any(m in (v.get("verdict_text") or "")
                      for m in LEGIBLE_MARKERS)]
    legibility = (len(legible) / n_verdicts if n_verdicts else 1.0)
    illegible = [v for v in verdicts if v not in legible]

    progs = [r for r in rows if r.get("kind") == "program"
             and r.get("event") == "applied"]
    with_arc = [r for r in progs if r.get("arc")]
    arc_coverage = (len(with_arc) / len(progs) if progs else 0.0)
    arc_ticks = sorted(int(r.get("tick") or 0) for r in with_arc)
    arc_max_gap = 0
    for a, b in zip(arc_ticks, arc_ticks[1:]):
        arc_max_gap = max(arc_max_gap, b - a)

    stale = [r for r in rows if r.get("kind") == "validation"
             and r.get("status") == "stale_inflight_dropped"]

    s2_in = [r for r in rows if r.get("kind") == "usage"
             and r.get("channel") == "s2_in"]
    full_superset = [r for r in s2_in
                     if all((r.get("sections") or {}).get(s, 0) > 0
                            for s in SUPERSET_SECTIONS)]
    superset_presence = (len(full_superset) / len(s2_in) if s2_in else 0.0)

    return {
        "deny_rate": round(deny_rate, 4),
        "verdicts": n_verdicts,
        "repeated_identical_denial_rate": round(repeated_rate, 4),
        "verdict_legibility": round(legibility, 4),
        "verdict_legibility_violations": len(illegible),
        "arc_coverage": round(arc_coverage, 4),
        "arc_max_gap_ticks": arc_max_gap,
        "reflex_stale_redispatches": len(stale),
        "superset_presence": round(superset_presence, 4),
        "superset_inputs": len(s2_in),
    }
