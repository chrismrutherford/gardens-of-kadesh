#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/events.py (T006).

Covers: raw engine event consumption (aiplay.h:109-118 types + aux semantics
from spec 003 data-model), snapshot-vs-last-seen diffs (created/damaged/
vanished), same-tick combine, no-op snapshots emit nothing.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "tests", "fixtures"))

import twincore.events as E  # noqa: E402

FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "aiplay")


def snap(tick, ships):
    return {"tick": tick, "paused": False, "income": 5.0,
            "ships": ships, "resources": []}


def ship(sid, owner=0, health=100.0, typ="Interceptor", pos=(0, 0, 0)):
    return {"id": sid, "owner": owner, "type": typ, "pos": list(pos),
            "vel": [0, 0, 0], "health": health, "healthMax": 100.0}


class TestSnapshotDiffs(unittest.TestCase):
    def test_created_only_first_observe_builds_baseline(self):
        c = E.DeltaConsumer()
        # first observation: baseline — created list empty (state store
        # rebuild owns initialisation), nothing reported
        self.assertEqual(c.consume_snapshot(snap(100, [ship(1), ship(2)])), [])

    def test_created_and_damaged(self):
        c = E.DeltaConsumer()
        c.consume_snapshot(snap(100, [ship(1)]))
        deltas = c.consume_snapshot(
            snap(130, [ship(1, health=40.0), ship(2)]))
        kinds = set()
        for d in deltas:
            kinds.update(d["kinds"])
        self.assertIn("created", kinds)
        self.assertIn("damaged", kinds)
        created = next(d for d in deltas if "created" in d["kinds"])
        self.assertEqual(created["created"], [2])
        self.assertEqual(created["tick"], 130)

    def test_vanished_without_event(self):
        c = E.DeltaConsumer()
        c.consume_snapshot(snap(100, [ship(1), ship(2)]))
        deltas = c.consume_snapshot(snap(130, [ship(1)]))
        vanished = [d for d in deltas if d["kinds"] == ["vanished"]]
        self.assertEqual(vanished[0]["vanished"], [2])

    def test_noop_snapshot_no_delta(self):
        c = E.DeltaConsumer()
        c.consume_snapshot(snap(100, [ship(1)]))
        self.assertEqual(c.consume_snapshot(snap(100, [ship(1)])), [])
        self.assertEqual(c.consume_snapshot(snap(100, [ship(1)])), [])

    def test_healing_not_damage(self):
        c = E.DeltaConsumer()
        c.consume_snapshot(snap(100, [ship(1, health=50.0)]))
        deltas = c.consume_snapshot(snap(130, [ship(1, health=90.0)]))
        kinds = {k for d in deltas for k in d["kinds"]}
        self.assertIn("healed", kinds)
        self.assertNotIn("damaged", kinds)


class TestEngineEvents(unittest.TestCase):
    def test_fixture_fixture_types(self):
        c = E.DeltaConsumer()
        raw = json.load(
            open(os.path.join(FIXTURE_DIR, "events_live.json")))
        res = raw["result"]
        deltas = c.consume_events(res)
        merged = E.combine(deltas)
        kinds = {k for d in merged for k in d["kinds"]}
        self.assertIn("lifecycle", kinds)
        self.assertIn("destroyed", kinds)
        destroyed = next(d for d in merged if "destroyed" in d["kinds"])
        self.assertEqual(destroyed["destroyed"].get(32), 0)  # aux 0 = no killer
        self.assertEqual(destroyed["tick"], 121942)

    def test_order_rejected_aux(self):
        c = E.DeltaConsumer()
        deltas = c.consume_events({"tick": 5, "events": [
            {"type": "orderRejected", "tick": 5, "id": 9, "aux": 2,
             "owner": 0}]})
        d = deltas[0]
        self.assertEqual(d["orders_rejected"], [{"id": 9, "aux": 2}])

    def test_order_applied(self):
        c = E.DeltaConsumer()
        deltas = c.consume_events({"tick": 7, "events": [
            {"type": "orderApplied", "tick": 7, "id": 9, "aux": 0,
             "owner": 0}]})
        self.assertEqual(deltas[0]["orders_applied"], [9])

    def test_combine_same_tick(self):
        a = {"tick": 9, "kinds": ["created"], "created": [3]}
        b = {"tick": 9, "kinds": ["destroyed"], "destroyed": {4: 1}}
        merged = E.combine([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(sorted(merged[0]["kinds"]),
                         ["created", "destroyed"])
        self.assertEqual(merged[0]["created"], [3])
        self.assertEqual(merged[0]["destroyed"], {4: 1})

    def test_unknown_event_type_raises(self):
        c = E.DeltaConsumer()
        with self.assertRaises(ValueError):
            c.consume_events({"tick": 1, "events": [
                {"type": "shipSpawned", "tick": 1, "id": 1, "aux": 0,
                 "owner": 0}]})

    def test_lifecycle_events(self):
        c = E.DeltaConsumer()
        deltas = c.consume_events({"tick": 1, "events": [
            {"type": "matchStarted", "tick": 0, "id": 0, "aux": 0,
             "owner": -1},
            {"type": "matchOver", "tick": 1000, "id": 0, "aux": 2,
             "owner": -1}]})
        kinds = [d["lifecycle"] for d in deltas]
        self.assertEqual(kinds, ["matchStarted", "matchOver"])


if __name__ == "__main__":
    unittest.main()