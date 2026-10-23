"""Gardens of Kadesh twin-core two-model LLM CPU player (spec 004).

Peer-client driver package: S1 tactical core (sole binding owner) + S2
strategist (never touches the game) around a deterministic orchestrator.
Surface: driver-runtime.md §1; registry single-source:
tools.aiplay_protocol. Retired agent stacks are NOT imported (research D7).
"""

PUB = [
    "driver", "config", "client", "events", "digest", "state_store",
    "validator", "economy", "directive", "packet", "s1tick", "s2pulse",
    "llm", "recorder", "replay", "harness",
]