# Research: fleet refactor — doctrine mining + code assessment

## Part A: gameplay.txt → doctrine (classic-HW1 applicable only)

Source: `gameplay.txt` (r/homeworld competitive strategy thread).
Filter: keep only what classic Homeworld (GoK engine, Kushan/Taiidan, no
modules/platforms/shipyards) supports. Each item names its consumer role.

### D1. Economy doctrine → ECONOMIST + EXECUTOR

- Collectors scale to saturation: aim for **~6+ ResourceCollectors**
  (thread says 11-14 across 3 nodes for HW:R maps; classic maps are
  smaller — 6-8 saturates 1-2 patches)
- **ResourceController** = mobile dropoff; position near the second patch —
  collectors spend most of their trip returning RU, so dropoff distance
  dominates income
- Never sit on RU: idle RU bank > ~1.5x your next planned build = build
  something (compounding)
- Early order: collectors FIRST, research/units after economy is online

### D2. Information warfare → ANALYST + STRATEGIST

- **Probes/scouts are the best units**: constant scouting tells you what
  to build and where it's safe
- **Kill enemy probes/scouts** on sight in your space — deny their intel
- React to enemy composition with counters, not panic building

### D3. Counter system → STRATEGIST + EXECUTOR (targeting)

Classic HW rock-paper-scissors:
- Interceptors/Defenders beat **strike craft**
- AttackBombers / IonFrigates beat **capital ships** (bombers die to
  fighters)
- Corvettes: LightCorvette/HeavyCorvette general strike; MinelayerCorvette
  area denial
- **Attack priority**: enemy ResourceCollectors (kills income) >
  ResearchShips (kills tech) > frigates/destroyers > Mothership last
  (160k HP, only when everything else is dead)
- "Fleet is better used attacking production/resourcing than turtling" —
  but our executor's standing rule stays defense-before-offense until
  clearly ahead

### D4. Kamikaze/scuttle → EXECUTOR (new tool use)

- `order_scuttle` = kamikaze. Cheap scouts scuttled piecemeal kill enemy
  collectors at massive RU advantage (scout 30 RU vs collector 650 RU)
- Use only when behind / enemy collector exposed and unguarded; never
  against defended blobs (collision check kills the attacker first)

### D5. Research just-in-time → ECONOMIST

- Build the UNITS first, research upgrades just before they're needed
- Research prerequisites for hulls are mandatory (already handled);
  stat upgrades wait until a fight is coming

### D6. Positioning → STRATEGIST

- Mothership/Controller parked near patches (already in executor prompt);
  fleet groups positioned to respond to both economy and front

### D7. Force allocation → CRITIC + ANALYST

- "Don't keep forces where they aren't needed" — critic flags idle
  guards on a safe Mothership while collectors die elsewhere
- Small strike group to pick off strays (enemy probes, wandering
  collectors) is high-value

## Part B: current-code maintainability assessment

- `tools/agent.py` (716 lines): monolithic main(); **env-gated hooks**
  (merge_peer_artifacts, merge_orders_ring, commander_queue,
  commander_queue_action) — control flow through env vars; argv-injection
  hack to reuse main(); prompts + playbook assembly inline; nags inline;
  dedupe logic inline in dispatch loop.
- `tools/agent_multi.py` (609 lines): PeerLoop mixes observe/context/LLM/
  publish/queue-edit; InstructionQueue.save() re-reads its own file to bump
  version (fragile); LoopLogger minimal (no levels, no JSONL).
- `tools/agent-multi.py` (165): config + prompts + wiring in one file.
- `tools/agent_tools.py`: one-line descriptions, no constraints, no
  when-to-use.
- Observability: ad-hoc text logs only; PERF lines partial; fleet-tail.sh
  helps humans but nothing is machine-readable; no fleet-status.

## Decisions

- **Module split**: see plan.md — six focused modules, prompts all in one
  place (agent_prompts.py) so the user can review ALL model-facing text in
  one file.
- **FleetContext object** replaces env vars + argv injection (R3-style
  explicit wiring; the single-agent CLI wraps the same pipeline).
- **JSONL round events** (one per loop per round) + fleet-status.py
  aggregator; human logs unchanged.
- **Doctrine cards**: DOCTRINE dict in agent_prompts.py; each role prompt =
  role-specific card + shared data-dictionary reference; all ≤ ~25 lines.
- **Tool descriptions**: purpose + when-to-use + constraints per tool.

## Alternatives considered

- Keep monolith + add comments — rejected: every feature so far wedged
  into main(); review impossible.
- SQLite for events — rejected (YAGNI): JSONL + artifact JSON covers audit
  needs; no query interface demanded.
- Full plugin architecture for roles — rejected: five roles are stable;
  dataclass config is enough.