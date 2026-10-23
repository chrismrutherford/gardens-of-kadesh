#!/usr/bin/env python3
"""Spec 004 tests: postgres store + exchange (T028-T029, FR-030).

recorder.py IS the postgres record store (no files); queue.py is the
exchange layer (inbox consume one-shot, pg_notify listeners). Live tests
against the configured host; skipped when unreachable.
"""
import json as J
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.queue as Q  # noqa: E402
import twincore.recorder as R  # noqa: E402

HOST = os.environ.get("TWINCORE_PG_HOST", "192.168.1.152")
DSN = {"host": HOST, "dbname": "twincore_q", "user": "postgres",
       "connect_timeout": 5}


def reachable():
    try:
        import psycopg2
        c = psycopg2.connect(**DSN)
        c.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(reachable(), "twincore_q postgres unreachable")
class TestStore(unittest.TestCase):
    """The recorder's postgres-backed semantics (FR-021 amended)."""

    def setUp(self):
        self.match = "test-rec-%d" % time.time() * 1000

    def tearDown(self):
        Q.Queue(DSN).delete_match(self.match)

    def test_record_envelope_and_kinds(self):
        rec = R.Recorder(self.match)
        rec.record("decision", tick=42, choice="attack")
        rows = rec.scan(kinds=["decision"])
        self.assertEqual(rows[0]["match_id"], self.match)
        self.assertEqual(rows[0]["tick"], 42)
        self.assertEqual(rows[0]["choice"], "attack")
        self.assertIn("wall_ts", rows[0])

    def test_scan_kinds_and_tick_filters(self):
        rec = R.Recorder(self.match)
        rec.record("decision", tick=1, a=1)
        rec.record("command", tick=2, cmd="move")
        rec.record("decision", tick=3, a=2)
        self.assertEqual([g["a"] for g in rec.scan(kinds=["decision"])],
                         [1, 2])
        self.assertEqual([g["a"] for g in
                          rec.scan(kinds=["decision"],
                                   tick_range=(3, 10))], [2])

    def test_append_across_reopen(self):
        R.Recorder(self.match).record("usage", tick=1, ch="s1")
        R.Recorder(self.match).record("usage", tick=2, ch="s2")
        rows = R.Recorder(self.match).scan(kinds=["usage"])
        self.assertEqual([l["ch"] for l in rows], ["s1", "s2"])

    def test_ring_cap_and_degrade_no_raise(self):
        rec = R.Recorder(self.match, dsn={"host": "127.0.0.1", "port": 59999,
                                          "dbname": "x", "user": "x",
                                          "connect_timeout": 1})
        for i in range(5):
            rec.record("event_raw", tick=i, i=i)
        rec.record("decision", tick=100)
        self.assertEqual(len(rec.ring()), 6)   # all kinds in the ring
        self.assertTrue(rec.degraded)          # FR-030: no gameplay failure
        self.assertEqual(rec.dropped, 6)       # counted losses
        got = rec.scan(kinds=["decision"])
        self.assertEqual(len([g for g in got if g.get("tick") == 100]), 1)

    def test_unknown_kind_rejected(self):
        rec = R.Recorder(self.match)
        with self.assertRaises(ValueError):
            rec.record("nonsense_kind", tick=1)


@unittest.skipUnless(reachable(), "twincore_q postgres unreachable")
class TestExchange(unittest.TestCase):
    """The queue-side: inbox consume + notify (FR-030)."""

    def setUp(self):
        self.q = Q.Queue(DSN)
        self.match = "test-inbox-%d" % (time.time() * 1000)

    def tearDown(self):
        self.q.delete_match(self.match)
        self.q.close()

    def test_inbox_consume_one_shot(self):
        self.q.publish(self.match, "inbox:seat0", kind="wake",
                       sender="analyst", payload={"reason": "test"})
        got = self.q.consume_inbox("inbox:seat0", match_id=self.match)
        self.assertEqual([g["kind"] for g in got], ["wake"])
        self.assertEqual(got[0]["payload"]["reason"], "test")
        self.assertEqual(
            self.q.consume_inbox("inbox:seat0", match_id=self.match), [])
        rows = self.q.scan(self.match, kinds=["wake"])
        self.assertEqual(len(rows), 1)   # consumed rows stay in the LOG

    def test_command_kind_never_publishable_or_consumable(self):
        # FR-002/FR-030: the queue is not a command path — publish refuses
        with self.assertRaises(ValueError):
            self.q.publish(self.match, "inbox:seat0", kind="command",
                           sender="rogue", payload={"verb": "order_attack"})
        with self.assertRaises(ValueError):
            self.q.publish(self.match, "inbox:seat0", kind="order_attack")
        self.assertEqual(
            self.q.consume_inbox("inbox:seat0", match_id=self.match), [])

    def test_notify_listener_receives_full_row(self):
        got = []
        lsn = self.q.listen("inbox:seat0", match_id=self.match,
                            callback=got.append)
        try:
            lsn.start_listening()
            time.sleep(0.6)   # subscription beat
            self.q.publish(self.match, "inbox:seat0", kind="note",
                           payload={"n": 42})
            lsn.wait(timeout=5)
            self.assertTrue(got and got[-1].get("payload", {}).get("n") == 42)
        finally:
            lsn.close()

    def test_multi_match_isolation(self):
        mid = self.match + "-x"
        try:
            self.q.publish(self.match, "inbox:seat0", kind="note",
                           payload={"a": 1})
            self.q.publish(mid, "inbox:seat0", kind="note",
                           payload={"a": 2})
            got = self.q.consume_inbox("inbox:seat0", match_id=self.match)
            self.assertEqual(got[0]["payload"]["a"], 1)
        finally:
            self.q.delete_match(mid)


if __name__ == "__main__":
    unittest.main()