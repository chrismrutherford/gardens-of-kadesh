#!/usr/bin/env python3
"""Spec 004 debug tool: inspect the postgres record store + live trails.

The record store is the twincore_q.messages table (FR-021/FR-030) — this
tool is how an operator (or an AI session) checks game state without
touching the game.

Usage:
  dbg.py                                  → newest runs overview
  dbg.py --match <id>                     → run overview: counts, digest, cadence
  dbg.py --match <id> --state             → reconstructed live state (own fleet, bank, enemies)
  dbg.py --match <id> --tail 30 --kinds decision,command
  dbg.py --match <id> --digests 3         → last M digests (verbatim p0 + projections)
  dbg.py --inbox                          → unread inbox messages (peek, no consume)
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import twincore.queue as Q  # noqa: E402
import twincore.recorder as R  # noqa: E402


def j(v):
    try:
        return json.dumps(v, default=str)
    except (TypeError, ValueError):
        return str(v)


def state_payload(row):
    return row.get("payload", {})


def overview(q, match):
    dg = q.scan(match, kinds=["digest"])
    print("MATCH %s" % match)
    if dg:
        last = dg[-1]
        p = state_payload(last)
        projs = {pr.get("metric"): pr.get("value")
                 for pr in (p.get("projections") or [])}
        print("  last digest  pulse=%s ticks=%s" % (
            p.get("interval_pulse"), j(p.get("tick_range"))))
        print("  bank=%s income=%s own_units=%s threat=%s" % (
            projs.get("bank"), projs.get("income"), projs.get("own_units"),
            projs.get("threat_estimate")))
        p0 = p.get("p0") or []
        print("  p0 open     : %s" % [i.get("kind") for i in p0
                                      if i.get("outcome") == "open"])
    else:
        print("  (no digests yet)")
    dec = q.scan(match, kinds=["decision"])
    if dec:
        last = dec[-1]
        print("  last decision: tick=%s mode=%s issued=%s" % (
            last.get("tick"), last.get("mode"), last.get("issued")))
    cmds = q.scan(match, kinds=["command"])
    print("  commands    : %s" % j(dict(Counter(c.get("tool")
                                                for c in cmds))))
    vals = q.scan(match, kinds=["validation"])
    ok = sum(1 for v in vals if v.get("status") == "ok")
    if vals:
        print("  validation  : %d/%d ok" % (ok, len(vals)))
        rej = [v for v in vals if v.get("status") == "rejected"]
        for r in rej[-3:]:
            print("    REJ %s %s | %s" % (r.get("tool"), r.get("reason"),
                                          str(r.get("message"))[:80]))
    deg = q.scan(match, kinds=["degradation"])
    if deg:
        last = deg[-1]
        print("  last state  : %s → %s (%s)" % (
            last.get("from"), last.get("to"), last.get("reason")))
    bre = q.scan(match, kinds=["breach"])
    print("  breaches    : %d" % len(bre))
    ev = q.scan(match, kinds=["delta"])
    ticks = sorted({d.get("tick") for d in ev})
    print("  delta ticks : %s..%s (%d deltas)" % (
        j(ticks[0]) if ticks else "-", j(ticks[-1]) if ticks else "-",
        len(ev)))
    return dg, dec


def state(q, match):
    dig = q.scan(match, kinds=["digest"])
    if dig:
        p = state_payload(dig[-1])
        projs = {pr.get("metric"): pr.get("value")
                 for pr in (p.get("projections") or [])}
        print("bank", projs.get("bank"), "| income", projs.get("income"),
              "| own_units", projs.get("own_units"),
              "| threat", projs.get("threat_estimate"))
    cmds = q.scan(match, kinds=["command"])
    if cmds:
        last = state_payload(cmds[-1])
        print("last command : %s %s @tick %s (%s)" % (
            cmds[-1].get("tool"), j(last.get("params"))[:80],
            cmds[-1].get("tick"), last.get("validation", {}).get("status")))
    notes = q.scan(match, kinds=["note", "question"])
    for n in notes[-3:]:
        print("note          %s %s" % (n.get("kind"),
                                       j(state_payload(n))[:100]))


def inbox_peek(q):
    rows = q.scan(None, kinds=None, channel=None) if False else None
    conn = q._connect()
    import psycopg2.extras
    import json as J
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "select channel, kind, sender, match_id, payload, created_at"
            " from messages where channel like 'inbox%' and consumed=false"
            " order by id desc limit 20")
        rows = cur.fetchall()
    if not rows:
        print("inbox: nothing unread")
        return
    for r in rows:
        print("%-14s %-6s from=%s match=%s %s" % (
            r["channel"], r["kind"], r["sender"], r["match_id"],
            j(r["payload"])[:100]))


def main():
    ap = argparse.ArgumentParser(description="twin-core postgres debug")
    ap.add_argument("--match")
    ap.add_argument("--state", action="store_true")
    ap.add_argument("--tail", type=int, default=0, help="print N rows")
    ap.add_argument("--kinds")
    ap.add_argument("--digests", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--inbox", action="store_true")
    a = ap.parse_args()
    q = Q.Queue(R.DEFAULT_DSN)
    try:
        if a.inbox:
            return inbox_peek(q)
        if a.list or not a.match:
            conn = q._connect()
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) \
                    as cur:
                cur.execute(
                    "select match_id, count(*) n, max(created_at) last,"
                    " max(tick) tick from messages"
                    " where channel='recorder' group by match_id"
                    " order by last desc limit 12")
                for r in cur.fetchall():
                    age = time.time() - r["last"].timestamp()
                    print("%-40s rows=%-6d tick=%-8s %4ds ago"
                          % (r["match_id"], r["n"], r["tick"], age))
            return
        if a.state:
            return state(q, a.match)
        if a.digests:
            dg = q.scan(a.match, kinds=["digest"])
            for d in dg[-a.digests:]:
                p = state_payload(d)
                print(json.dumps({k: p.get(k) for k in
                                  ("interval_pulse", "tick_range", "p0",
                                   "changes", "order_status", "s1_notes",
                                   "projections")}, default=str)[:900])
            return
        overview(q, a.match)
        if a.tail or a.kinds:
            kinds = a.kinds.split(",") if a.kinds else None
            rows = q.scan(a.match, kinds=kinds)
            for r in rows[-a.tail:]:
                print("%s %-6s %-16s %s" % (
                    r.get("tick"), r.get("kind"), r.get("channel"),
                    j(r.get("payload"))[:160]))
    finally:
        q.close()


if __name__ == "__main__":
    main()