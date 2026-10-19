#!/usr/bin/env python3
"""Launch the agent-multi fleet: commander + 4 read-only peer advisors.

  python3 tools/agent-multi.py [--llm-strategist URL] ... (see --help)

Role/artifact contract: specs/001-agent-multi-llm-fleet/contracts/.
The commander owns the session binding and all orders; peers publish
JSON artifacts that are merged into the commander's round prompts.
"""
import argparse
import json
import os
import sys
import threading
import time

import agent
import agent_multi


DEFAULT_HOST = "http://192.168.2.72"


def parse_args():
    ap = argparse.ArgumentParser(description="Homeworld multi-agent fleet")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__),
                                                     "agent-multi.json"),
                    help="JSON config file (commander/peers endpoints)")
    ap.add_argument("--llm-commander", default=None,
                    help="fast executor model for the command loop")
    ap.add_argument("--llm-strategist", default=None)
    ap.add_argument("--llm-analyst", default=None)
    ap.add_argument("--llm-economist", default=None)
    ap.add_argument("--llm-critic", default=None)
    ap.add_argument("--mcp", default="http://127.0.0.1:8302/mcp")
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--interval", type=float, default=None)
    ap.add_argument("--max-rounds", type=int, default=2000)
    ap.add_argument("--stale-secs", type=float,
                    default=agent_multi.STALE_DEFAULT_SECS)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--logfile", default=agent.__dict__.get("logfile_default",
                                                            "/tmp/aiplay-agent.log"))
    a = ap.parse_args()

    # config file fallback: any not-explicitly-given endpoint comes from JSON
    cfg = {}
    try:
        with open(a.config) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    if not a.llm_commander:
        a.llm_commander = cfg.get("commander") or DEFAULT_HOST + ":9930"
    peers_cfg = cfg.get("peers", {})
    a.llm_strategist = a.llm_strategist or peers_cfg.get("strategist") \
        or cfg.get("strategist") or DEFAULT_HOST + ":9931"
    a.llm_analyst = a.llm_analyst or cfg.get("analyst") \
        or peers_cfg.get("analyst") or DEFAULT_HOST + ":9932"
    a.llm_economist = a.llm_economist or cfg.get("economist") \
        or peers_cfg.get("economist") or DEFAULT_HOST + ":9933"
    a.llm_critic = a.llm_critic or cfg.get("critic") \
        or peers_cfg.get("critic")   # critic optional (config may null it)
    if a.interval is None:
        a.interval = cfg.get("interval", 1.0)
    return a


PROMPTS = {
    "strategist": (
        "You are the STRATEGIST peer of a Homeworld fleet AI. Read-only.\n"
        "Own the long-horizon plan: research path, fleet composition, "
        "attack timing (attack only when massively ahead; otherwise build "
        "fleet first). Reply with directives ≤10 lines."),
    "analyst": (
        "You are the ANALYST peer of a Homeworld fleet AI. Read-only.\n"
        "React to fresh battle events and compressed world state: name "
        "threats and immediate reactions (≤6 lines)."),
    "economist": (
        "You are the ECONOMIST peer of a Homeworld fleet AI. Read-only.\n"
        "Own the production plan: harvest coverage + build queue from the "
        "tech tree and RU income (≤6 lines)."),
    "critic": (
        "You are the CRITIC peer of a Homeworld fleet AI. Read-only.\n"
        "Review the commander's recent orders vs the plan; object only when "
        "something is wrong (rejected orders, doomed attacks, economy "
        "holes). Be terse; silence is approval."),
}


def main():
    a = parse_args()
    session_id = a.session_id or str(int(time.time() * 1000))
    store = agent_multi.ArtifactStore(agent_multi.session_dir(session_id))
    logdir = store.dir

    agent.LOGFILE = a.logfile
    commander_log = agent_multi.LoopLogger("commander", a.logfile)

    mcp = agent.MCP(a.mcp)
    agent.ensure_bound(mcp, a.player)

    peers = []
    for role, url in (("strategist", a.llm_strategist),
                      ("analyst", a.llm_analyst),
                      ("economist", a.llm_economist),
                      ("critic", a.llm_critic)):
        if not url:
            continue            # optional role (e.g. critic) disabled in config
        try:
            llm = agent.LLM(url)
        except Exception as exc:
            commander_log.log("FATAL starting %s peer: %r" % (role, exc))
            # peers are optional; the commander keeps working (US1)
            continue
        peer_store = store
        loop = agent_multi.PeerLoop(
            role=role,
            mcp=agent_multi.PeerSidecar(a.mcp),
            llm=llm,
            store=peer_store,
            logger=agent_multi.LoopLogger(
                role, "%s/%s.log" % (logdir, role)),
            prompt=PROMPTS[role],
            full_snapshot=role in ("strategist", "economist"),
            use_events=role in ("analyst", "critic"),
            orders_store=store if role == "critic" else None,
            peer_stores=(store,),
        )
        peers.append(loop)
        loop.start()

    commander_log.log("fleet up: session=%s peers=%r"
                      % (session_id, [p.name for p in peers]))

    # commander runs in the main thread (same cadence as agent.py) until done
    try:
        os.environ["AIPLAY_MULTI_DIR"] = logdir
        os.environ["AIPLAY_MULTI_PROMPT"] = "executor"
        agent.LOGFILE = a.logfile
        # agent.main parses argv: inject the executor's LLM + fast interval
        argv_backup = sys.argv[1:]
        sys.argv[1:] = ["--mcp", a.mcp, "--player", str(a.player),
                        "--llm", a.llm_commander,
                        "--interval", str(a.interval),
                        "--max-rounds", str(a.max_rounds),
                        "--logfile", a.logfile]
        try:
            agent.main()
        finally:
            sys.argv[1:] = argv_backup
    finally:
        os.environ.pop("AIPLAY_MULTI_PROMPT", None)
        for p in peers:
            p.stop()
        for p in peers:
            p.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())