"""Spec 004 twin-core: Postgres message queue (T028, FR-030 rider).

Dual purpose on 192.168.1.152/twincore_q:
- LOG: publish() mirrors recorder lines (SQL-queryable, replayable later)
- EXCHANGE: wake/note inbox messages, one-shot consumed (SKIP LOCKED)
  with optional realtime listeners (pg_notify).

BINDING SAFETY: the queue is not a command path — inbound kinds are
wake|note only (FR-002: exactly one binding owner). Queue outages never
break recording: QueueRecorder keeps the JSONL file honest (FR-024 spirit
— degrade, don't guess).
"""
import json
import os
import threading
import time

import psycopg2.extras


def now_ms():
    return int(time.time() * 1000)


def messages_table():
    return "messages"


class Queue:
    def __init__(self, dsn):
        import psycopg2
        self.dsn = dict(dsn)
        self._conn = None
        self.errors = 0

    def _connect(self):
        if self._conn is not None and self._conn.closed == 0:
            return self._conn
        import psycopg2
        self._conn = psycopg2.connect(**self.dsn)
        self._conn.autocommit = True
        return self._conn

    def close(self):
        if self._conn is not None and self._conn.closed == 0:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    # -- publishers -----------------------------------------------------------
    FORBIDDEN_KINDS = ("command", "order", "order_*")

    def publish(self, match_id, channel, kind=None, sender="driver",
                tick=None, payload=None):
        if kind in ("command", "order") or \
                (isinstance(kind, str) and kind.startswith("order_")):
            # FR-002/FR-030: the queue is never a command path
            raise ValueError("commands can never travel the queue; the "
                             "tactical core's path is the only binding "
                             "owner")
        pub = {
            "match_id": match_id, "channel": channel,
            "sender": sender, "kind": kind or channel,
            "tick": tick, "payload": json.dumps(payload or {}, default=str),
        }
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                "insert into messages (match_id, channel, sender, kind,"
                " tick, payload) values (%s,%s,%s,%s,%s,%s) returning id,"
                " created_at",
                (pub["match_id"], pub["channel"], pub["sender"],
                 pub["kind"], pub["tick"], pub["payload"]))
            mid, created = cur.fetchone()
            cur.execute("select pg_notify('twincore_msg', %s)",
                        (json.dumps({"id": mid, "channel": channel,
                                     "match_id": match_id}),))
        return {"id": mid, "created_at": created}

    # -- log scans ------------------------------------------------------------
    def scan(self, match_id, kinds=None, channel=None):
        conn = self._connect()
        q, args = [], []
        sql = "select id, created_at, channel, sender, kind, tick, payload" \
              " from messages where match_id = %s"
        args.append(match_id)
        if kinds:
            sql += " and kind = any(%s)"
            args.append(list(kinds))
        if channel:
            sql += " and channel = %s"
            args.append(channel)
        sql += " order by id"
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) \
                as cur:
            cur.execute(sql, tuple(args))
            rows = cur.fetchall()
        return [dict(r, payload=json.loads(
            r["payload"] if isinstance(r["payload"], str)
            else json.dumps(r["payload"]))) for r in rows]

    def delete_match(self, match_id):
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute("delete from messages where match_id = %s",
                        (match_id,))

    # -- inbox consumption (one-shot; wake/note only) ----------------------------
    def consume_inbox(self, channel, match_id=None):
        """SKIP-LOCKED one-shot take for this channel's address. Only
        wake|note kinds may be taken inbound (FR-030/FR-002)."""
        conn = self._connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) \
                as cur:
            cur.execute(
                "update messages set consumed = true, consumed_at = now(),"
                " consumed_by = %s where id = any("
                "  (select id from messages where channel = %s"
                "     and (%s is null or match_id = %s"
                "          or (match_id is not null and match_id = 'any'))"
                "     and kind in ('wake','note')"
                "     and consumed = false"
                "   order by id limit 16 for update skip locked)"
                ") returning id, match_id, sender, kind, tick, payload;",
                ("driver", channel, match_id, match_id))
            rows = cur.fetchall()
        return [dict(r, payload=json.loads(
            r["payload"] if isinstance(r["payload"], str)
            else json.dumps(r["payload"]))) for r in rows]

    # -- realtime (pg_notify) ---------------------------------------------------
    def listen(self, channel, match_id=None, callback=None):
        return Listener(self.dsn, channel, match_id=match_id,
                        callback=callback, fetch=True)

    def fetch(self, message_id):
        """Full message row by id (listener enrichment after notify)."""
        conn = self._connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) \
                as cur:
            cur.execute("select id, created_at, channel, sender, kind,"
                        " tick, payload, consumed, consumed_by"
                        " from messages where id = %s", (message_id,))
            row = cur.fetchone()
        if row is None:
            return None
        payload = row.get("payload")
        row["payload"] = json.loads(
            payload if isinstance(payload, str) else json.dumps(payload))
        return dict(row, payload=row["payload"])


class Listener(threading.Thread):
    """Realtime listener: pg_notify on twincore_msg filtered by channel.
    Delivery is advisory — consumers still poll at their own cadence."""

    daemon = True

    def __init__(self, dsn, channel, match_id=None, callback=None,
                 fetch=False):
        super().__init__(daemon=True)
        self.dsn = dsn
        self.channel = channel
        self.match_id = match_id
        self._on = callback or (lambda message: None)
        self._fetch = fetch
        self._got = threading.Event()
        self.queue = []
        self._stop = threading.Event()

    def run(self):
        import psycopg2
        import select as sel
        conn = psycopg2.connect(**self.dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("listen twincore_msg")
        while not self._stop.is_set():
            try:
                r, w, x = sel.select([conn], [], [], 1.0)
            except Exception:
                break
            if r:
                for _n in range(0, 16):  # drain up to a burst
                    note = conn.poll()
                    if not note:
                        break
                try:
                    while conn.notifies:
                        n = conn.notifies.pop(0)
                        payload = json.loads(n.payload or "{}")
                        if payload.get("channel") == self.channel and \
                                (self.match_id is None or
                                 payload.get("match_id") in
                                 (self.match_id, "any")):
                            message = payload
                            if self._fetch:
                                try:
                                    q2 = Queue(self.dsn)
                                    full = q2.fetch(payload.get("id"))
                                    q2.close()
                                    if full:
                                        message = full
                                except Exception:
                                    pass
                            self._got.set()
                            self._on(message)
                            self.queue.append(message)
                except Exception:
                    pass
        conn.close()

    def start_listening(self):
        self.start()
        return self

    def wait(self, timeout=None):
        return self._got.wait(timeout=timeout)

    def close(self):
        self._stop.set()