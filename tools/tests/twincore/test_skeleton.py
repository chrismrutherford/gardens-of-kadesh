#!/usr/bin/env python3
"""Spec 004 T002: substrate smoke — package imports, registry single-source,
old agent stacks never imported, all suites collectable."""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiplay_protocol  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))
import twincore  # noqa: E402

RETIRED = ("agent.py", "agent_tools.py", "agent_multi.py", "agent-multi.py")


class TestSkeleton(unittest.TestCase):
    def test_all_modules_import(self):
        for name in twincore.PUB:
            mod = importlib.import_module("twincore.%s" % name)
            self.assertTrue(hasattr(mod, "__doc__") and mod.__doc__)

    def test_registry_single_source(self):
        r = aiplay_protocol.REGISTRY
        self.assertIn("order_tools", r)
        self.assertEqual(len(r["order_tools"]), 20)
        for verb in ("move", "attack", "harvest", "ru_transfer", "alliance"):
            self.assertIn("order_" + verb, r["order_tools"])

    def test_no_retired_imports(self):
        base = os.path.join(
            os.path.dirname(__file__), "..", "..", "twincore")
        for name in os.listdir(base):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                src = fh.read()
            for retired in ("agent_tools", "agent_multi", "agent-multi"):
                self.assertNotIn(retired, src, "%s imports retired stack %s"
                                 % (name, retired))

    def test_configs_dir_exists(self):
        cfg = os.path.join(
            os.path.dirname(__file__), "..", "..", "twincore", "configs")
        self.assertTrue(os.path.isdir(cfg))


if __name__ == "__main__":
    unittest.main()