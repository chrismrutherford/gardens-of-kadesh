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


def v4_ledger(own=0, idle_collectors=0, full_collectors=0, asteroids=1,
              builders=1, bank=5000.0):
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
                      "order": "harvest", "cargo": 100, "cargo_max": 100})
        rid += 1
    roids = []
    aid = 500
    for i in range(asteroids):
        roids.append({"id": aid + i, "pos": [float(100 * i), 0.0, 0.0],
                      "ru": 5000.0})
    led = {"own_player": own, "ships": ships, "asteroids": roids,
           "bank": bank}
    led["builders"] = builders
    return led


class FakeLedger:
    """Ledger-shaped wrapper over a v4_ledger dict (validator interface:
    as_dict / in_flight / is_allied / register_command)."""

    def __init__(self, led=None):
        led = led or v4_ledger()
        self._ships_all = {s["id"]: dict(s) for s in led.get("ships", [])}
        if led.get("builders"):
            self._ships_all[90] = {"id": 90, "type": "Mothership",
                                   "owner": led.get("own_player", 0),
                                   "pos": [0.0, 0.0, 0.0], "order": "parade"}
        self._resources = {r["id"]: dict(r) for r in led.get("asteroids", [])}
        self._state = {"ships_all": self._ships_all,
                       "resources": self._resources,
                       "bank": led.get("bank", 5000.0)}
        self._pending = {}

    def as_dict(self):
        return dict(self._state)

    def in_flight(self):
        return dict(self._pending)

    def is_allied(self, player):
        return False

    def register_command(self, command):
        self._pending[command["id"]] = {
            "tool": command.get("tool"),
            "fingerprint": command.get("fingerprint"),
            "issued_tick": command.get("issued_tick", 0),
        }

    def clear_pending(self):
        self._pending.clear()

    def apply_snapshot(self, snapshot):
        """Spec-003-shaped snapshot → state (test double of Ledger)."""
        for s in snapshot.get("ships") or []:
            self._ships_all[s["id"]] = dict(s)
        for r in snapshot.get("resources") or []:
            self._resources[r["id"]] = dict(r)
        for p in snapshot.get("players") or []:
            if p.get("resourceUnits") is not None:
                self._state["bank"] = p["resourceUnits"]
        self._state["ships_all"] = self._ships_all
        self._state["resources"] = self._resources
        return snapshot


class FakeClient:
    """Client test double: records wire orders; errors are scriptable per
    tool (one-shot queues), success returns {"queued": True, "orderId": id}."""

    def __init__(self):
        self.issued = []
        self.error_queue = {}   # tool -> list of error payloads

    def bind(self, player_index=0):
        return {"bound": player_index}

    def order(self, tool, params=None):
        self.issued.append({"tool": tool, "params": dict(params or {})})
        errs = self.error_queue.get(tool)
        if errs:
            return errs.pop(0)
        return {"queued": True, "orderId": (params or {}).get("id")}

    def fail_once(self, tool, error):
        self.error_queue.setdefault(tool, []).append({"error": error})


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def fake_ledger():
    return FakeLedger(v4_ledger(idle_collectors=2, full_collectors=1,
                                asteroids=3, builders=1))


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
