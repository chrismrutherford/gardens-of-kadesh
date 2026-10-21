# AI Strategy Digest (distilled from community MP doctrine — gameplay.txt)

This digest adapts the competitive Homeworld multiplayer doctrine in
`gameplay.txt` to AI play in Gardens of Kadesh (HW1-style races).

## Core economy rules

- The RTS axis: rapid resource acquisition + judicious expenditure. Do not
  turtle on a big fleet — an idle fleet is wasted tempo: attack enemy
  production/resourcing or actively defend vulnerable areas.
- Do NOT settle for 2–5 harvesters. Target ~11–14 collectors saturating
  several asteroid fields. Get a second drop-off (ResourceController, 680)
  once drop-off capacity becomes the limiter.
- The AI here ignores nothing: your income shows in the snapshot
  (`income` RU/min). If income ≈ 0, collectors are idle/paraded/docking.

## Attacking economy (the HW1 kamikaze meta)

- Kamikaze is the micro that makes small races dangerous: vs an enemy
  ResourceCollector it is cost-efficient (a collector costs 650; the
  attackers that kill it cost a fraction).
- Rules learned from live testing (gameplay.txt thread data):
  - Piecemeal kamikaze (2–4 craft, sequential hits) beats a single big
    wave: the burn DoT does not stack; large waves converge and collide.
  - A collector dropping off at the mothership is effectively
    invulnerable — hit collectors at the field, not at the dock.
  - Defenders/interceptors in numbers blunt kamikaze piecemeal — expect
    escorts near enemy resourcing on bigger maps.
- Attack enemy economy when his fleet is committed elsewhere; if he
  booms near you, build fighters/corvettes to shut the node down ASAP.

## Information warfare

- Probes/scouts are the best units: they tell you WHAT to build to
  counter, and whether the map is safe to expand into.
- Continuously reposition your probe (order_move) to keep fresh intel;
  persecute enemy probes when you spot them.
- Read enemy build intent from the snapshot: enemy fleet composition
  (e.g. many fighters → he is rushing; collectors streaming → he booms).

## Force allocation

- Default: economy up first, balanced build, aggressive scouting.
- If he rushes: cut non-counter research; build counters immediately.
- Units first, upgrades "just in time" before the fight that needs them.
- Attack in coordinated groups; kill priority targets (controllers,
  resource ops) before capitals.

## Anti-fragility advice

- Losing fighters piecemeal while his production runs = losing tempo;
  consolidate, heal/repair (dock), then strike in force.
- Keep some fighters as a mothership screen (order_guard) during
  research cycles (ResearchShips and Mothership are your win condition).
- If you are already winning, be the one controlling gravity-well/cloak
  specials (order_special) rather than trading fleets evenly.

## Order semantics the AI should respect

- `order.launch`/`autolaunch on` right after any fighter build queue.
- `order_tactics aggressive` for engaged fleets; evasive to disengage.
- Formations cost speed (slowest ship) — use for capital pushes, not
  strike craft skirmishing.
- Every order tool call carries `id`; audit `orderApplied`/`orderRejected`
  in the event stream (SC-004) instead of assuming success.