#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/recorder.py.

Covers: append-only JSONL per kind (protocol §6), mandatory line envelope
{match_id, tick, wall_ts}, event_raw ring cap (512), kind/tick replay filters,
append across reopen (no truncation). FR-021, SC-007.
"""
import glob
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.recorder as R  # noqa: E402

ALL_KINDS = [
    "event_raw", "classification", "delta", "rollup", "digest", "packet",
    "packet_rejected", "revision", "decision", "command", "validation",
    "usage", "breach", "degradation", "question",
]


class TestRecorder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="twincore-rec-")
        self.rec = R.Recorder(self.dir, match_id="m1")

    def tearDown(self):
        self.rec.close()

    def _lines(self, kind, match_dir=None):
        path = os.path.join(match_dir or self.dir, kind + ".jsonl")
        with open(path, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_record_kinds_partition(self):
        for kind in ALL_KINDS:
            self.rec.record(kind, tick=1, note=kind)
        for kind in ALL_KINDS:
            lines = self._lines(kind)
            self.assertEqual(len(lines), 1, kind)
            self.assertEqual(lines[0]["note"], kind)

    def test_envelope_fields(self):
        self.rec.record("decision", tick=42, choice="attack")
        (line,) = self._lines("decision")
        self.assertEqual(line["match_id"], "m1")
        self.assertEqual(line["tick"], 42)
        self.assertIn("wall_ts", line)
        self.assertEqual(line["choice"], "attack")

    def test_payloadless_record_ok(self):
        self.rec.record("decision", tick=7)
        (line,) = self._lines("decision")
        self.assertEqual(
            set(line) - {"match_id", "tick", "wall_ts", "_seq"}, set())

    def test_ring_cap_event_raw_only(self):
        rec = R.Recorder(self.dir, match_id="m1", ring_cap=3)
        try:
            for i in range(5):
                rec.record("event_raw", tick=i, i=i)
            rec.record("decision", tick=100)
            self.assertEqual(len(rec.ring()), 3)
            self.assertEqual([e["i"] for e in rec.ring()], [2, 3, 4])
            self.assertEqual(len(self._lines("decision")), 1)
        finally:
            rec.close()

    def test_replay_kind_filter(self):
        self.rec.record("decision", tick=1, a=1)
        self.rec.record("command", tick=2, cmd="move")
        self.rec.record("decision", tick=3, a=2)
        got = R.scan(self.dir, kinds=["decision"])
        self.assertEqual([g["a"] for g in got], [1, 2])
        got = R.scan(self.dir, kinds=["decision"], tick_range=(3, 10))
        self.assertEqual([g["a"] for g in got], [2])

    def test_replay_tick_filter(self):
        self.rec.record("digest", tick=10, d=True)
        self.rec.record("digest", tick=20, d=False)
        got = R.scan(self.dir, kinds=["digest"], tick_range=(10, 19))
        self.assertEqual([g.get("d") for g in got], [True])

    def test_append_no_truncate_on_reopen(self):
        self.rec.record("usage", tick=1, ch="s1")
        self.rec.close()
        self.rec = R.Recorder(self.dir, match_id="m1")
        self.rec.record("usage", tick=2, ch="s2")
        lines = self._lines("usage")
        self.assertEqual([l["ch"] for l in lines], ["s1", "s2"])

    def test_flush_per_line_and_files_list(self):
        self.rec.record("breach", tick=1, ch="s1_out")
        files = sorted(os.path.basename(p)
                       for p in glob.glob(os.path.join(self.dir, "*.jsonl")))
        self.assertEqual(files, ["breach.jsonl"])

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.rec.record("nonsense_kind", tick=1)

    def test_unknown_kind_scan_is_empty(self):
        got = R.scan(self.dir, kinds=["digest"])
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()