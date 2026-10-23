#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/recorder.py — postgres record store.

Envelope {match_id, tick, wall_ts}, kind rows, ring cap 512 for event_raw,
kind/tick replay filters (SC-007), append across reopen, degraded-mode
no-raise. FR-021 (amended: postgres store, no local files).
"""
import json as J
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.recorder as R  # noqa: E402
import twincore.queue as Q  # noqa: E402

ALL_KINDS = [
    "event_raw", "classification", "delta", "rollup", "digest", "packet",
    "packet_rejected", "revision", "decision", "command", "validation",
    "usage", "breach", "degradation", "question", "note",
    "program", "micro",  # spec 005 v4 (T005)
]

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
class TestRecorder(unittest.TestCase):
    def setUp(self):
        self.match = "test-recorder-%d" % (time.time() * 1000)
        self.rec = R.Recorder(self.match)

    def tearDown(self):
        Q.Queue(DSN).delete_match(self.match)
        self.rec.close()

    def test_record_kinds_partitioned(self):
        for kind in ALL_KINDS:
            self.rec.record(kind, tick=1, note=kind)
        for kind in ALL_KINDS:
            rows = self.rec.scan(kinds=[kind])
            self.assertEqual(len(rows), 1, kind)
            self.assertEqual(rows[0]["note"], kind)
            self.assertEqual(rows[0]["kind"], kind)

    def test_envelope_fields(self):
        self.rec.record("decision", tick=42, choice="attack")
        (line,) = self.rec.scan(kinds=["decision"])
        self.assertEqual(line["match_id"], self.match)
        self.assertEqual(line["tick"], 42)
        self.assertIn("wall_ts", line)
        self.assertEqual(line["choice"], "attack")

    def test_payloadless_record_ok(self):
        self.rec.record("decision", tick=7)
        (line,) = self.rec.scan(kinds=["decision"])
        self.assertEqual(
            set(line) - {"match_id", "tick", "wall_ts", "kind"}, set())

    def test_ring_cap(self):
        # general fall-back ring (cap 512, oldest evicted)
        for i in range(5):
            self.rec.record("event_raw", tick=i, i=i)
        self.rec.record("decision", tick=100)
        self.assertEqual(len(self.rec.ring()), 6)
        rec = R.Recorder(self.match, ring_cap=3)
        try:
            for i in range(5):
                rec.record("event_raw", tick=i, i=i)
            self.assertEqual([e["i"] for e in rec.ring()], [2, 3, 4])
        finally:
            rec.close()

    def test_replay_filters(self):
        self.rec.record("decision", tick=1, a=1)
        self.rec.record("command", tick=2, cmd="move")
        self.rec.record("decision", tick=3, a=2)
        got = self.rec.scan(kinds=["decision"])
        self.assertEqual([g["a"] for g in got], [1, 2])
        got = self.rec.scan(kinds=["decision"], tick_range=(3, 10))
        self.assertEqual([g["a"] for g in got], [2])

    def test_append_across_reopen(self):
        self.rec.record("usage", tick=1, ch="s1")
        self.rec.close()
        self.rec = R.Recorder(self.match)
        self.rec.record("usage", tick=2, ch="s2")
        rows = self.rec.scan(kinds=["usage"])
        self.assertEqual([l["ch"] for l in rows], ["s1", "s2"])

    def test_module_scan_helper(self):
        self.rec.record("digest", tick=10, d=True)
        got = R.scan(self.match, kinds=["digest"], tick_range=(10, 19))
        self.assertEqual([g.get("d") for g in got], [True])

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.rec.record("nonsense_kind", tick=1)

    def test_unknown_kind_scan_is_empty(self):
        self.assertEqual(self.rec.scan(kinds=["digest"]), [])


class TestDegradedMode(unittest.TestCase):
    """FR-030: postgres down → ring + degraded flag; never a raise."""

    def setUp(self):
        self.match = "test-degraded-%d" % (time.time() * 1000)

    def tearDown(self):
        try:
            Q.Queue(DSN).delete_match(self.match)
        except Exception:
            pass

    def test_record_survives_db_down(self):
        rec = R.Recorder(self.match, dsn={"host": "127.0.0.1", "port": 59999,
                                          "dbname": "x", "user": "x",
                                          "connect_timeout": 1})
        rec.record("event_raw", tick=1, i=1)
        self.assertTrue(rec.degraded)
        self.assertEqual(rec.dropped, 1)
        rec.record("decision", tick=2)
        self.assertTrue(rec.degraded)
        got = rec.scan(kinds=["decision"])
        self.assertEqual(len([g for g in got if g.get("tick") == 2]), 1)


if __name__ == "__main__":
    unittest.main()