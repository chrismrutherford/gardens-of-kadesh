"""Spec 004 twin-core: Postgres record store (T003 revised — FR-021/FR-030).

The `twincore_q.messages` table IS the append-only match trail: no local
files for messages or logging (user directive 2026-10-23; supersedes
research D5). Every record(kind, tick, **payload) row carries the
envelope {match_id, tick, wall_ts(=created_at)} + the payload jsonb, with
`kind` columns + indexes replacing per-kind files. Replay = scan()/replay()
filters (kinds, tick_range). event_raw additionally enters a bounded
in-memory ring (cap 512). Postgres unavailability degrades: record() keeps
the newest events in the ring, flags `degraded`, never raises — the match
continues (FR-024 spirit; FR-030's no-gameplay-failure rule).
"""
import itertools
import json
import os
import threading
import time

KINDS = (
    "event_raw", "classification", "delta", "rollup", "digest", "packet",
    "packet_rejected", "revision", "decision", "command", "validation",
    "usage", "breach", "degradation", "question", "note",
    "program", "micro",  # v4 (spec 005): program apply/status, S1 micro
)

RING_CAP = 512


def now_ms():
    return int(time.time() * 1000)

DEFAULT_DSN = {
    "host": os.environ.get("TWINCORE_PG_HOST", "192.168.1.152"),
    "dbname": os.environ.get("TWINCORE_PG_DB", "twincore_q"),
    "user": os.environ.get("TWINCORE_PG_USER", "postgres"),
    "connect_timeout": 5,
}


def _kind_file(name):  # legacy name kept for the record-shape contract notes
    return name


class Recorder:
    """Append-only match trail writer (the queue table is the store)."""

    def __init__(self, match_id, dsn=None, ring_cap=RING_CAP,
                 match_dir_removed=None):
        self.match_id = match_id
        self.ring_cap = ring_cap
        self.dsn = dict(DEFAULT_DSN if dsn is None else dsn)
        self.degraded = False
        self.dropped = 0
        self._conn = None
        self._ring = []
        self._seq = itertools.count()

    # -- connection (resilient) ------------------------------------------------
    def _connect(self):
        if self._conn is not None and getattr(self._conn, "closed", 1) == 0:
            return self._conn
        import psycopg2
        # keepalives + statement timeout: a wedged socket MUST surface as
        # an error (→ degraded ring) instead of freezing every record()
        dsn = dict(self.dsn)
        opts = dsn.pop("options", "") or ""
        if "statement_timeout" not in opts:
            opts = (opts + " -c statement_timeout=5000").strip()
        self._conn = psycopg2.connect(
            keepalives=1, keepalives_idle=30, keepalives_interval=10,
            keepalives_count=3,
            connect_timeout=int(dsn.pop("connect_timeout", 5) or 5),
            options=opts, **dsn)
        self._conn.autocommit = True
        return self._conn

    def close(self):
        try:
            if self._conn is not None and self._conn.closed == 0:
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    # -- writes -----------------------------------------------------------------
    def record(self, kind, tick=None, **payload):
        """Append one record (envelope + payload). Postgres down: ring +
        degraded flag, no raise (FR-030)."""
        if kind not in KINDS:
            raise ValueError("unknown recorder kind: %r" % (kind,))
        line = {
            "match_id": self.match_id,
            "tick": int(tick if tick is not None else 0),
            "kind": kind,
            "wall_ts": time.time(),
        }
        line.update(payload)
        # general in-memory fall-back (cap'd): primary record store is
        # postgres; the ring guarantees degraded-mode replay of the tail
        self._ring.append(line)
        if len(self._ring) > self.ring_cap:
            del self._ring[:len(self._ring) - self.ring_cap]
        self._insert(kind, line)
        return line

    def _insert(self, kind, line):
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    "insert into messages (match_id, channel, sender,"
                    " kind, tick, payload) values (%s,'recorder','driver',"
                    " %s, %s, %s) returning created_at",
                    (self.match_id, kind, line["tick"],
                     json.dumps(line, sort_keys=True, default=str)))
                created_at, = cur.fetchone()
            line["wall_ts"] = created_at.timestamp()
            self.degraded = False
        except Exception:
            # wedged/broken socket: drop the connection so the next
            # record() reconnects; the ring carries the tail meanwhile
            self._conn = None
            self.degraded = True
            self.dropped += 1   # counted; ring/other kinds still carry

    # -- memory ring -------------------------------------------------------------
    def ring(self):
        return list(self._ring)

    # -- scans (replay contract SC-007) -------------------------------------------
    def scan(self, kinds=None, tick_range=None):
        import psycopg2.extras
        if self.degraded:
            return [dict(r) for r in self._ring if _kind_ok(r, kinds,
                                                            tick_range)]
        try:
            conn = self._connect()
        except Exception:
            self.degraded = True
            return [dict(r) for r in self._ring if _kind_ok(r, kinds,
                                                            tick_range)]
        sql = ("select match_id, tick, created_at, kind, payload from"
               " messages where match_id = %s")
        args = [self.match_id]
        if kinds:
            sql += " and kind = any(%s)"
            args.append(list(kinds))
        if tick_range is not None:
            lo, hi = tick_range
            sql += " and tick between %s and %s"
            args.extend([lo, hi])
        sql += " order by id"
        out = []
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) \
                as cur:
            cur.execute(sql, tuple(args))
            for row in cur.fetchall():
                payload = row["payload"]
                row = dict(payload, match_id=row["match_id"],
                           tick=row["tick"], kind=row["kind"],
                           wall_ts=row["created_at"].timestamp())
                out.append(row)
        return out


def _kind_ok(row, kinds, tick_range):
    kinds_ok = kinds is None or row.get("kind") in kinds
    if tick_range is not None:
        lo, hi = tick_range
        return kinds_ok and lo <= row.get("tick", 0) <= hi
    return kinds_ok


def scan(match_id, kinds=None, tick_range=None):
    """Module-level replay: one match's trail filtered (SC-007)."""
    rec = Recorder(match_id)
    try:
        return rec.scan(kinds=kinds, tick_range=tick_range)
    finally:
        rec.close()


def replay(match_id, kinds=None, tick_range=None):
    """Alias kept for contract §6 replay wording."""
    return scan(match_id, kinds=kinds, tick_range=tick_range)