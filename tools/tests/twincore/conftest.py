#!/usr/bin/env python3
"""Spec 004 twincore test conftest: postgres test rows cleanup."""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "twincore"))

DSN = {"host": os.environ.get("TWINCORE_PG_HOST", "192.168.1.152"),
       "dbname": "twincore_q", "user": "postgres", "connect_timeout": 5}

TEST_PREFIXES = ("test-", "m-", "w-", "mw-", "x-")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    try:
        import twincore.queue as Q
        q = Q.Queue(DSN)
        conn = q._connect()
        with conn.cursor() as cur:
            cur.execute(
                "delete from messages where created_at < now() - interval"
                " '10 minutes' and (%s)",
                tuple())
            cur.execute(
                "delete from messages where "
                " (match_id like 'test-%' or match_id like 'm-%'"
                "  or match_id like 'w-%' or match_id like 'mw-%'"
                "  or match_id like 'x-%')"
                " and created_at < now() - interval '2 minutes'")
        print("conftest: stale test rows purged (incl. old ts-suffixed)")
    except Exception as e:
        print("conftest purge skipped:", e)
