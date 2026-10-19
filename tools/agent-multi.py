#!/usr/bin/env python3
"""Launch the agent-multi fleet: commander + 4 read-only peer advisors.

  python3 tools/agent-multi.py [--llm-strategist URL] ... (see --help)

Role/artifact contract: specs/001-agent-multi-llm-fleet/contracts/.
The commander owns the session binding and all orders; peers publish
JSON artifacts that are merged into the commander's round prompts.
"""
import argparse
import os
import sys
import threading
import time

import agent
import agent_multi


DEFAULT_HOST = "http://192.168.2.72"


def parse_args():
    ap = argparse.ArgumentParser(description="Homeworld multi-agent fleet")
    ap.add_argument("--llm-strategist", default=DEFAULT_HOST + ":9931")
    ap.add_argument("--llm-analyst", default=DEFAULT_HOST + ":9932")
    ap.add_argument("--llm-economist", default=DEFAULT_HOST + ":9933")
    ap.add_argument("--llm-critic", default=DEFAULT_HOST + ":9934")
    ap.add_argument("--mcp", default="http://127.0.0.1:8302/mcp")
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--max-rounds", type=int, default=2000)
    ap.add_argument("--stale-secs", type=float,
                    default=agent_multi.STALE_DEFAULT_SECS)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--logfile", default=agent.__dict__.get("logfile_default",
                                                            "/tmp/aiplay-agent.log"))
    return ap.parse_args()


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
        import os
        os.environ["AIPLAY_MULTI_DIR"] = logdir
        agent.main()
    finally:
        os.environ.pop("AIPLAY_MULTI_DIR", None)
        for p in peers:
            p.stop()
        for p in peers:
            p.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())