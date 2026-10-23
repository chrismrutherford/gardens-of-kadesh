#!/usr/bin/env python3
"""Spec 004: real-time tail of the postgres message trail.

  python3 tools/twincore/tail.py                    # follow newest match
  python3 tools/twincore/tail.py --match <id>       # follow a specific match
  python3 tools/twincore/tail.py --kinds decision,command,packet   # filter
  python3 tools/twincore/tail.py --backlog 50       # replay the last N first
  python3 tools/twincore/tail.py --digests          # digest lines only (verbose)

Polls twincore_q.messages (id cursor, 0.5 s); Ctrl-C stops.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import twincore.queue as Q  # noqa: E402
import twincore.recorder as R  # noqa: E402


def fmt(row):
    tick = row.get("tick")
    kind = row.get("kind")
    stamp = ""
    if row.get("created_at") is not None:
        try:
            stamp = row["created_at"].strftime("%H:%M:%S") if hasattr(row["created_at"], "strftime") else ""
            if stamp:
                stamp = time.strftime("%H:%M:%S",
                                      time.localtime(row["created_at"]
                                                     .timestamp())) \
                    if not isinstance(row["created_at"], str) else ""
        except Exception:
            stamp = ""
    body = []
    if kind == "decision":
        body = ["mode=%s issued=%s" % (row.get("mode"), row.get("issued") if
                                        row.get("issued") is not None else "")]
        if row.get("history_op"):
            body.append("history_op=%s" % row["history_op"])
        if row.get("streak"):
            body.append("streak=%s" % row["streak"])
    elif kind == "command":
        p = row.get("params") or {}
        body = ["%-16s %s" % (row.get("tool"), json.dumps(
            {k: p[k] for k in p if k != "id"})[:90])]
        v = row.get("validation") or {}
        if v.get("status") and v.get("status") != "ok":
            body.append("[%s]" % v.get("reason"))
    elif kind in ("packet", "revision"):
        body = ["pulse=%s applied=%s dropped=%s" % (
            row.get("pulse"), row.get("applied"), row.get("dropped"))]
        if row.get("audits"):
            body.append("| %s" % "; ".join(map(str, row["audits"]))[:140])
    elif kind == "packet_rejected":
        body = [str(row.get("reason"))[:120]]
    elif kind == "digest":
        p0 = row.get("p0") or []
        ch = row.get("changes") or []
        body = ["pulse=%s %s p0=%s changes=%d" % (
            row.get("interval_pulse"), row.get("tick_range"),
            [i.get("kind") for i in p0], len(ch))]
    elif kind == "classification":
        body = ["%s %s" % (row.get("class"), row.get("rule"))]
    elif kind == "validation":
        body = ["%-16s %s %s" % (row.get("tool"), row.get("status"),
                                 str(row.get("message") or "")[:70])]
    elif kind == "usage":
        ch = row.get("channel")
        body = ["%s %s" % (ch, row.get("total") or
                           row.get("context_units") or
                           row.get("completion"))]
    elif kind == "degradation":
        body = ["%s -> %s (%s)" % (row.get("from"), row.get("to"),
                                   row.get("reason"))]
    elif kind == "delta":
        bits = ["%s=%d" % (k, len(row.get(k) or ()))
                for k in ("created", "destroyed", "damaged", "vanished",
                          "orders_applied", "orders_rejected")
                if row.get(k)]
        body = [" ".join(bits) or "(tick echo)"]
    elif kind == "event_raw":
        body = ["%-16s id=%s aux=%s" % (row.get("type"), row.get("id"),
                                        row.get("aux"))]
    elif kind == "note":
        body = ["[%s] %s" % (row.get("message_kind"),
                             j_short(row.get("payload")))]
    else:
        body = [j_short({k: row[k] for k in row.keys()
                         & kind_keys(kind)} if True else {})]

    return "%s tick=%-7s %-14s %s" % (stamp, tick, kind,
                                      " | ".join(b for b in body if b))


def kind_keys(kind):
    return {"k", "kind"}


def j_short(v):
    try:
        return json.dumps(v, default=str)[:90]
    except Exception:
        return str(v)[:90]


def newest_match(q):
    import psycopg2.extras
    conn = q._connect()
    with conn.cursor() as cur:
        cur.execute("select match_id from messages where channel='recorder'"
                    " order by id desc limit 1")
        r = cur.fetchone()
    return r[0] if r else None


def _poll(q, match, last_id, kinds, limit=400):
    """Lightweight cursor poll: rows after last_id (no full trail load)."""
    import psycopg2.extras
    conn = q._connect()
    sql = ("select id, match_id, channel, sender, kind, tick, payload,"
           " created_at from messages where match_id = %s")
    args = [match]
    if kinds:
        sql += " and kind = any(%s)"
        args.append(list(kinds))
    sql += " and id > %s order by id limit %s"
    args.extend([last_id, limit])
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, tuple(args))
        rows = cur.fetchall()
    out = []
    for r in rows:
        p = r.get("payload", {})
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                p = {"raw": p}
        out.append(dict(r, **(p if isinstance(p, dict) else {})))
    return out


def _backlog(q, match, n, kinds):
    import psycopg2.extras
    conn = q._connect()
    sql = ("select id, match_id, channel, sender, kind, tick, payload,"
           " created_at from messages where match_id = %s")
    args = [match]
    if kinds:
        sql += " and kind = any(%s)"
        args.append(list(kinds))
    sql += " order by id desc limit %s"
    args.append(n)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, tuple(args))
        rows = [dict(r) for r in cur.fetchall()]
    out = []
    for r in reversed(rows):
        p = r.get("payload", {})
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                p = {"raw": p}
        out.append(dict(r, **(p if isinstance(p, dict) else {})))
    return out


def main():
    ap = argparse.ArgumentParser(description="tail the postgres match trail")
    ap.add_argument("--match")
    ap.add_argument("--kinds")
    ap.add_argument("--backlog", type=int, default=5)
    ap.add_argument("--digests", action="store_true")
    ap.add_argument("--interval", type=float, default=0.5)
    a = ap.parse_args()
    q = Q.Queue(R.DEFAULT_DSN)
    last_id = 0
    match = a.match
    if match is None:
        match = newest_match(q)
        if match is None:
            print("tail: no recorded match yet; waiting for one")
        else:
            print("tail: %s" % match)
    kinds = a.kinds.split(",") if a.kinds else None
    if match:
        for r in _backlog(q, match, a.backlog, kinds):
            print(fmt(r), flush=True)
            last_id = max(last_id, r.get("id", 0))
    if a.digests:
        kinds = ["digest"]
    try:
        while True:
            if match is None:
                match = newest_match(q)
                if match:
                    print("tail: %s" % match)
                time.sleep(a.interval)
                continue
            for r in _poll(q, match, last_id, kinds):
                print(fmt(r), flush=True)
                last_id = max(last_id, r.get("id", 0))
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    finally:
        q.close()


if __name__ == "__main__":
    main()