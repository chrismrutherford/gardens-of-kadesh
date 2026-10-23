#!/usr/bin/env python3
"""Spec 004/005 twincore test conftest: postgres test rows cleanup + v4
program/ledger builders (T001 increment A, spec 005)."""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "twincore"))

DSN = {"host": os.environ.get("TWINCORE_PG_HOST", "192.168.1.152"),
       "dbname": "twincore_q", "user": "postgres", "connect_timeout": 5}

TEST_PREFIXES = ("test-", "m-", "w-", "mw-", "x-")


# --- spec 005 v4 builders (used by test suites; data-only, no doctrine) ---

def v4_grant(gid="d1", **over):
    """A delegation envelope grant (FR-017)."""
    g = {"grant_id": gid, "verbs": ["order_attack", "order_guard"],
         "ships": "scope:defense", "override_tasking": True}
    g.update(over)
    return g


def v4_packet(version=1, **over):
    """A minimal valid Program packet (production + tasking + envelope)."""
    p = {"version": version, "issued_tick": 100,
         "posture": "conftest posture",
         "delegate": [v4_grant()],
         "production": [{"id": "p1", "verb": "order_build",
                         "params": {"type": "ResourceCollector",
                                    "quantity": 1}}],
         "tasking": [{"id": "t1", "verb": "order_guard", "ships": [12, 13],
                      "target": 90}]}
    p.update(over)
    return p


def v4_micro(seq=1, program_version=1, **over):
    """A minimal S1 micro-packet (FR-018)."""
    m = {"seq": seq, "program_version": program_version,
         "tasking": [{"id": "m1", "grant": "d1", "verb": "order_guard",
                      "ships": [14], "target": 90}]}
    m.update(over)
    return m


def v4_ledger(own=0, idle_collectors=0, full_collectors=0, asteroids=1):
    """A tiny deterministic state-view for executor tests (refined per
    spec-003 snapshot shape in later increments)."""
    ships, rid = [], 100
    for _ in range(idle_collectors):
        ships.append({"id": rid, "type": "ResourceCollector",
                      "owner": own, "pos": [0.0, 0.0, 0.0],
                      "order": "parade", "cargo": 0})
        rid += 1
    for _ in range(full_collectors):
        ships.append({"id": rid, "type": "ResourceCollector",
                      "owner": own, "pos": [10.0, 0.0, 0.0],
                      "order": "harvest", "cargo": 100})
        rid += 1
    roids = []
    aid = 500
    for i in range(asteroids):
        roids.append({"id": aid + i, "pos": [float(100 * i), 0.0, 0.0],
                      "ru": 5000.0})
    return {"own_player": own, "ships": ships, "asteroids": roids}


@pytest.fixture
def clock():
    """Deterministic game-time clock (ticks only, no wall clock)."""
    class DetClock:
        def __init__(self):
            self.tick = 0

        def advance(self, n=1):
            self.tick += n
            return self.tick

    return DetClock()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    try:
        import twincore.queue as Q
        q = Q.Queue(DSN)
        conn = q._connect()
        with conn.cursor() as cur:
            cur.execute(
                "delete from messages "
                " where (match_id like 'test-%' or match_id like 'm-%'"
                "        or match_id like 'w-%' or match_id like 'mw-%'"
                "        or match_id like 'x-%' or match_id like 'dbg-%'"
                "        or match_id like 'test-rec-%' or match_id like"
                "        'test-inbox-%' or match_id like 'test-corb-%')"
                "   and created_at < now() - interval '2 minutes'")
        print("conftest: stale test rows purged (incl. old ts-suffixed)")
    except Exception as e:
        print("conftest purge skipped:", e)
