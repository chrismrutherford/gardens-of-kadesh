# Research: V4 — Strategist-Driven LLM CPU Player

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23
Spec: [spec.md](spec.md) | Plan: [plan.md](plan.md)

All NEEDS CLARIFICATION: none outstanding from the spec. Decisions below
resolve design unknowns; alternatives recorded per plan-step format.

## D1 — Where v4 control code lives: three new modules beside v3

**Decision**: New `tools/twincore/program.py` (schema/versioning/input
builder), `executor.py` (deterministic translator + reflexes), and
`v4driver.py` (assembly + pulse loop). v3's `driver.py` is left untouched as
the v3 entry point.
**Rationale**: the v3 driver's `Driver` class hard-wires s1tick/s2pulse/economy
assembly (driver.py:68); surgically hollowing it would churn validated code
and blur the "retired, unreferenced" requirement (FR-014). A v4 entry keeps
the retirement declarative and the regression surface zero.
**Alternatives**: (a) edit `driver.py` in place behind a `--mode v4` flag —
rejected: couples retired and active control paths, riskier regression,
violates the spirit of "not part of the v4 driver"; (b) fully delete v3
control modules — rejected: out of scope, cleanup is a separate decision.

## D2 — Pulse loop: strip S2PulseLoop to program semantics, keep async two-phase

**Decision**: `v4driver.PulseLoop` re-implements the pulse skeleton (async
fire → fill on arrival → never block the tick loop, carried from v3's
`S2PulseLoop`) but with program semantics: no digest-window Q/A, no
directive/corroboration; one program per pulse (FR-007/FR-008).
**Rationale**: the async two-phase pattern is v3's proven live behaviour for
"PULSES NEVER BLOCK" (spec Edge Cases); the digest-window logic
(s2pulse.py:68 core) is precisely the v3 protocol v4 retires.
**Alternatives**: import S2PulseLoop and subclass — rejected: base class
carries the very protocol the spec retires; subclassing would inherit
directive semantics and dead branches.

## D3 — Program validation: whole-packet, JSON-schema-style structural check in `program.py`

**Decision**: A program is a JSON object `{version, issued_tick, posture,
production[], tasking[]}`; `program.validate_packet()` checks the whole
packet (types, verbs, param shapes, one-shot vs standing flags) before
anything applies; invalid → nothing applies, previous version stands
(FR-008). Validation is structural + registry-grounded: verbs must be order
verbs the spec-003 layer knows; subject/target ids checked against the
ledger at executor time (fresh-validation, not packet time — ids go stale on
match load).
**Rationale**: all-or-nothing application is FR-008; separating schema
validity (packet time) from id liveness (executor time) handles the
stale-ids edge case cleanly.
**Alternatives**: jsonschema library — rejected: YAGNI, adds a dependency
for a two-hundred-line structural check the substrate already styles by hand
(packet.py validates without deps).

## D4 — Pulse input builder: static-prefix echo + P0 pinning + program statuses

**Decision**: `program.build_pulse_input()` assembles the ≤12k packet:
(1) static charter block (race/seat/playbook material pointer), (2) ledger
echo line-per-unit-group from `state_store`, (3) event rollups via
`digest.rollup()` with P0 items verbatim pinned at top (FR-009/FR-010,
`digest.P0Tracker` reused), (4) the previous program with per-item executor
status (executed/failed(blocked: reason)/pending), (5) reflex digest.
Budget enforcement prunes oldest program history first, records the prune
(FR-013), `digest.units()` reused for sizing.
**Rationale**: FR-009 fixes the layout; reusing the proven P0 rule set and
unit counter keeps classification and budget logic identical to what v3
already hardened.
**Alternatives**: new classification code — rejected: FR-004 forbids
re-inventing doctrine in the driver; digest.py's rules are carried-as-config
per FR-010.

## D5 — Executor: per-tick translation with 6-command budget and program-item traceability

**Decision**: `executor.Executor.on_tick(state)` walks program items in
fixed order (production then tasking), mints commands through `Validator`
(the FR-002 lane), caps issue rate at 6/tick, records per-item
`ProgramStatus` (issued tick, command ids, resolution incl. engine
orderRejected aux) for the trail and the next pulse input; standing items
re-issue until superseded, one-shot items retire on first apply. Reflexes
(FR-006, exactly: idle-collector→nearest-asteroid harvest, full-collector→
deliver) run only for ships NOT covered by any live program item (program
wins, spec US2-AS3). Every reflex action also mints validated commands and
trail rows.
**Rationale**: FR-005 demands a mechanical translator with no doctrine;
traceability (US1-AS4/SC-002 ≥80% program-cited) falls out naturally when
every command row cites its program version+item.
**Alternatives**: command queue with retry policies — rejected: retries
belong to the pulse cycle (≤1/program lifetime per FR-001-AS3), not the tick
loop; any scheduling heuristic would be doctrine by another name.

## D6 — Trail: queue/recorder reused verbatim; new row kind `program`

**Decision**: All v4 messaging/logging flows through `twincore_q.messages`
(`queue.py`, FR-030/FR-011); the in-memory `Recorder` ring keeps the tail
during postgres outage and counts drops (FR-US4-AS3, already implemented in
recorder.py). Row kind `program` is added to the taxonomy for program
application/status events; `packet`/`revision` kinds keep carrying program
history snapshots. `replay.py`/`tail.py` filters gain `program` kind with no
changes to their row-shape contract.
**Rationale**: SC-007 replayability-from-trail is already v3-proven; a new
kind is the minimum delta.
**Alternatives**: separate programs table — rejected: splits the trail,
forces join reconstruction in replay, contradicts FR-011 single-store.

## D7 — Config: `v4` section in `config.py`; endpoint = fast rack default

**Decision**: `v4:` config section: `pulse_seconds` (default 20, clamp
10–30, FR-007), `tick_seconds` (default ~1), `budgets.s2_in=12288/
s2_out=3072` (FR-013), `reflex: {…}` on/off, `p0_rules` passthrough (the
digest rule set as config, FR-010), `endpoints.s2` defaulted to the fast
rack (.106, ~2.7 s/call measured live — spec Assumptions). `llm.py` reused
unchanged for calls; ≤1 bounded repair attempt per pulse for malformed
output (Edge Cases), implemented in the pulse loop.
**Rationale**: configuration-only endpoints (FR-015) and cadences in game
time (FR-003) are carried constraints; defaults land where evidence points
(speed of the S2 call dominates pulse latency).
**Alternatives**: hard-code pulse at 20 s — rejected: FR-007 says
configuration; two racks exist and rack choice must stay config-legal.
**Amended by D8**: pulse default is now 120 s, S2 endpoint = slow/large
rack; the S1 fast rack serves the micro-pulse channel instead.

## D8 — Three tiers: S1 small model returns inside a delegation envelope (decision 17, 2026-10-23)

**Decision**: The v4 driver ships THREE tiers: executor (deterministic
code, ~1 s tick) + **S1** small/fast model (micro-pulse default 8 s game
time, budgeted input ≤4k / out ≤900) acting ONLY inside the **delegation
envelope** declared in S2's Program (`delegate[]`: allowed verbs, subject
ships/scope, zones, optional override-of-tasking grants) + **S2**
strategist (pulse default 120 s game time — sized to the measured ~2 min
large-model latency). New module `tools/twincore/s1micro.py` (S1 loop) +
envelope validation in `program.py` + a micro command lane in
`executor.py`; new trail kind `micro`. S2/slow-endpoint + S1/fast-endpoint,
both config-only.
**Rationale**: measured large-model latency (~2 min/output) makes the
single-model 10–30 s pulse impossible and the two-reflex blind window at
~2 min pulses unacceptable (a raid kills the base between pulses). The
spec's own assumption anticipated exactly this amendment if reflexes
proved insufficient; the user chose to fold it in while the spec is still
draft and nothing is implemented. The envelope keeps FR-004's single
strategic intent authority: S2 decides WHAT and licenses HOW-much-S1;
every S1 command cites program version + grant + decision id (auditable).
**Alternatives**: (a) v4-as-written with ~2–4 min pulses — rejected: blind
window unacceptable; (b) S1 as full tactical brain — rejected: two-brains
drift, heavier audit; (c) S1 advisory-only — rejected: does not close the
blind window; (d) renumber tiers s1=code/s2=small/s3=big — rejected:
churns specs 004/005 terminology. Degradation ladder gains a tier (S2
down → last program + S1 micro + reflexes; S1 down → program + reflexes;
both down → reflexes only). Config switch disables S1 entirely if live
rounds show the envelope unnecessary.

## Open items for the tasks step

- Fixture plan for determinism tests (GameClock + fake S2/S1 LLM endpoints
  + fake client) — fake S1 returns micro-packets, in-envelope and outside.
- Live-round checklist (launch-and-poll workflow, cash-inject savegame);
  degrade checks now include BOTH channels (S2 stop, then S1 stop).
- Playbook doc load path: `documentation/community/cpu-player-playbook.md`
  verbatim into S2's charter AND S1's micro-charter (data files, verified
  by the harness per FR-016).

## D9 — Verdict text as the bounded teach channel (Draft v3, decision 19)

**Decision**: executor denies/coerces/clamps and engine async rejections
emit ONE actionable verdict line (refused + what IS allowed), bounded
(~200 chars) and streamed to three sinks: `validation` trail rows, S2's
per-item ProgramStatus, S1's rolling verdict section. The executor never
invents orders — the verdict restates current legality only; recurring
identical denials gain a `# repeat <n>` marker so S2 sees a loop.
**Rationale**: live round — guessed tech `MassDrive2Kt` was engine-rejected
async and invisible (status said `applied`); Qwen repeated empty-param
calls forever in silence; GLM refused research guessing tech names. A deny
that names the allowed alternative converts the violation into curriculum.
**Alternatives**: (a) raw aux codes only — rejected: GLM/Qwen cannot
interpret `E_BAD_REQUEST` reliably, and issued-vs-accepted stayed
conflated; (b) executor auto-corrects the model's item (e.g. swap tech) —
rejected: invents orders, violates FR-004/FR-005 doctrine-free law;
(c) reject whole packets on unknown names — rejected: all-or-nothing
already governs schema; semantic names should cost one item, not one pulse.

## D10 — Arc: model-authored memory, code-enforced budget (Draft v3)

**Decision**: program packet gains `arc` (default ≤600 chars) — S2's
rewritten running story each pulse; driver stores LATEST only, re-renders
every pulse input (`# ARC (your summary so far)`), code-seeds from
mechanical rollups when missing/stale, length-clamps oversize with a
verdict (never drops, never rejects). Raw history NEVER re-enters S2's
window (trail keeps it for replay/harness). S1's memory is symmetric but
S2-owned: `s1_context {max_exchanges, ticks}` truncates S1's rolling
micro-history mechanically, FIRST-pruned under budget breach; absent ⇒
stateless (current behaviour).
**Rationale**: live round — GLM burned thinking tokens each pulse
reconstructing "the last program issued at 32202, applied at 47202"
because nothing compacted its own past; its context is ~24k and the pulse
input is a 12k budget. Model-authored narrative + code-enforced budgets
keeps meaning without unbounded growth, and keeps doctrine out (the arc is
the strategist's own words, not driver-synthesised doctrine).
**Alternatives**: (a) code-summarised history digests fed to S2 — rejected
(driver-authored narrative = hidden doctrine, FR-004 risk) though the
mechanical rollup NUMBERS stay code-made; (b) S1 keeps full conversation
history — rejected: unbounded growth vs the 4k budget; (c) vector/embedded
memory stores — rejected: overkill vs a 600-char arc; YAGNI (Constitution V).
