# The CPU Playbook — Multiplayer Strategy Extracted from the Computer Player

*For human players. This distils the multiplayer computer player's documented
intelligence (spec `specs/001-cpu-player-intelligence/spec.md`, data inventory
`specs/001-cpu-player-intelligence/baseline-reference.md`) into strategies you can
actually play. Two parts: Part A steals the CPU's plays for your own use; Part B is
how to counter a CPU opponent - including what it can and cannot do at each
difficulty level.*

---

## The CPU's brain on one page

The computer player runs one loop, on a fixed cadence, forever:

> **Economy → Knowledge → Offence → Defence → Execute → Rebuild.**

Every few seconds it: rates its own and the enemy's force as spatial groups
("blobs"), refreshes what it knows about every enemy ship class it has ever seen,
picks attack plans from a weighted menu, responds to distress in its territory,
then spends its build money in a fixed budget split:

- **30% of build capacity → attack**, **30% → defence**, **40% → economy/resource fleet**
- It attacks when it rates its force at **~1.4× (overkill) the target's** strength
- Its default group formation is **Broad** (F6)

Everything else in this document is variations of those five behaviours.

---

## Part A — Play the CPU's strategy yourself

### A1. Opening (the economy phase)

The CPU's resource manager opens exactly like this:

1. **Launch and dispatch every collector immediately** to the nearest good patch;
   it also keeps a spare construction craft (command centre or carrier) free to
   keep building.
2. **Grow collectors toward a per-difficulty target**, then add **resource
   controllers** (mobile refuelling/transfer hubs for your collectors) and
   **support frigates** (mobile repair/refuel for strike craft) in set ratios.
3. **Only ask for research ships when you need what research unlocks**
   (demand-driven research; the CPU also uses research-ship construction as a
   convenient carrier escort).
4. **Do not stockpile without spending** - unspent budget just sits.

Human translation - a solid opening for any map: collectors first (5-8 for a
typical map), a resource controller once harvesters are flowing, first research
ship around the time your first raiding group is affordable, then strike craft
for patrol/defence while frigates queue.

**Fuel and repair discipline**: strike craft have a real fuel gauge - tanks burn in
flight (faster under afterburner), and a ship that flames out is a drifting target.
The CPU's support frigates do double duty, and its watchdogs pick which role applies:
**fuel-low teams dock and refuel before running dry**, while **teams taking heavy
fire retreat and dock for refuel + repair**. The resource manager even
**forward-stations a support frigate between the two motherships** ahead of the
action, so its harass and guard wings always have a pump/bay within reach. Learn the
same: park a support frigate behind your fighters, cycle them through refuel rather
than bleeding them, and pull a battered group back to a repair dock before
re-committing it.

### A2. Recon before force

The CPU never attacks blind on purpose: **probe the enemy command centre** (static
recon), **patrol actively** to map their fleet, and run **recon ahead of raids**
(the "armada" plans always require scout confirmation that the target is visible
first: ~1 scout team per 5 enemy blobs).

Human translation: before committing any main force, buy eyes. A probe on the enemy
mothership plus one patrolling scout tells you what to build against them, and the
same intel doubles as early warning. Track **what hull classes they have**
(the CPU tracks every enemy ship class it has seen - so should you).

### A3. The attack library (use these as your plan names)

The CPU picks attack plans from a 15-entry weighted menu. As a human, take the
whole menu as your playbook, weighted by situation:

| Play | Composition / trigger | When to use |
| --- | --- | --- |
| **Cheap frigate push** | frigates, small | Constant pressure early; cheapest cap-class pressure |
| **Frigate/destroyer + guards** | 1 heavy group + guard wing | The workhorse "balanced" push |
| **Big fleet** | frigate + destroyer | Mid-game main attack |
| **Heavy cruiser run** | 1-2 heavy cruisers | Late; expensive but the CPU's top-end plan |
| **Takeout target** | mixed group for one specific ship | Kill the enemy's command centre, research ships, or a specific problem frigate |
| **Fancy takeout** | takeout team **plus a guard team** for it | When their defence is real; the guard wing eats the interceptors |
| **Fighter strike** | pack of fighters | Cheap, fast raids for the raiding stage |
| **Corvette strike** | pack of corvettes | Same, but survives light AA |
| **Frigate strike** | frigate-only group | Sustained pressure on frigate-weak defences |
| **Harass big** (the economy killer) | light interceptors, replace losses at 12/15; heavy-equivalent 14 units; bombers count 14 | Attack the collectors, support craft, and soft capital - standoff at 25,000, strike, refuel, repeat |
| **Capture op** | salvage corvettes | Steal their ships (free value); excellent on expensive hulls |
| **Mine lay** | minelayer corvettes | Sow approach corridors to your (or their) territory |
| **Cloak generator** | generator + cloaked fighters | Snipe with no warning; protect your generator with dedicated guarding |
| **Gravwell** | gravwell generator | Paralyse capital groups inside its field - a trap play the CPU sets up near its own territory |
| **Kamikaze** | fighters/corvettes below ~health threshold ram | The CPU only does it (25% draw) at advanced, but damaged strike craft ramming capitals trades cheap for expensive - use it deliberately, not out of panic |

Two more plans the CPU uses that humans habitually under-use:

- **Armada run** - a *recon-confirmed* multi-team attack (0 teams at beginner,
  1 at intermediate, 2 at advanced in the current data). The principle is "no main
  push before the scouts confirm the target".
- **Guard-cooperation** - a team that explicitly cooperates with a cloak generator
  team; special forces play.

The CPU's tactic settings are also a lesson: **strike craft go aggressive; delicate
operations (capture, mine, cloaking) run evasive**. Humans default ships to neutral
and forget - copy the CPU.

### A4. Defence doctrine (the "sphere of influence")

The CPU's defence is a zone, not units:

1. Define a **sphere around your command centre**; refresh it regularly.
2. Keep **guard layers**: small spherical guard teams on your resource ships
   (Sphere formation - F10), large flexible guard/roving wings for reaction.
3. Split the reaction wings into **fast teams** (few ships, arrive quickly,
   low threshold to engage) and **slow teams** (larger - roughly half or a
   quarter of the local fleet at thresholds 2 / 4 depending on your fleet
   count - committed when the problem is big).
4. When the enemy brings real force into the sphere, three escalation tiers:
   - **Mothership defence (default)**: idle ships converge.
   - **Medium tier**: recall the guard and roving wings as well (trigger ≈
     enemy fleet at 1.4× strength inside your zone).
   - **Hardcore tier**: everything available defends the command centre.
5. **Seen a cloaked ship?** Build proximity sensors: a few on contact, and a
   roving sensor swarm when one is detected *inside* your sphere.
6. Priority when things go wrong at once: **defend the command centre first**,
   then the oldest distress first.

Human translation: decide your perimeter, put cheap guards on your harvesters,
keep one fast reaction wing and one heavier reserve, and know your two recall
red lines in advance - don't improvise them mid-firefight.

### A5. The hyperspace wallet

The CPU banks a slice of resource income for **hyperspace plays** (jump-strikes
and jump-retreats) when the map supports it. Humans: do the same on hyperspace
maps - either hold a deliberate jump budget for emergencies, or plan one
jump-strike on their undefended rear once your main fleet pins their front.

### A6. Budget your build money like a CPU

The fixed 30/30/40 split maps neatly onto human budgeting:

- **~40% into economy** until your income target is met (collectors, controller,
  support frigate).
- **~30% into your standing attack force** (the next push's composition).
- **~30% into defence utility** (guards, mine/grav/cloak/proximity infrastructure,
  replacements).

Track it by hand for a night and you'll notice you overinvest in the fun part
(offence) and lose battles to the boring two.

### A7. Politics in 3-8 player battles

- Pick a **primary enemy early** (the CPU does this at battle start and refocuses
  automatically when one dies; humans drift).
- The CPU forms alliances with bias (the "CPU hates humans" setting shifts CPU-CPU
  vs CPU-human cooperation). Human lesson: **two CPUs that aren't allied to each
  other are each other's problem** - pick your attack windows off *their* politics,
  not just yours.
- Never attack an ally (you can't; the game blocks it). The CPU never does either -
  allied protection is an absolute shield. In free-for-alls, allying a CPU stops its
  hostility toward you stone-cold; that's a legitimate diplomatic tool.

---

## Part B - Beating the CPU

### B1. Know what your opponent is allowed to do

| | Beginner | Intermediate | Advanced |
| --- | --- | --- | --- |
| Thinks every (frames) | 63 (≈2.1s) | 31 (≈1s) | 15 (≈0.5s) |
| Fleet push sizes | small/guard/big | small/guard/big | + **heavy cruiser** |
| Takeout plays | ✔ / bodyguard variant | ✔ / bodyguard | ✔ / bodyguard |
| Fighter/corvette/frigate strikes | ✔ (fighter strike rare) | ✔ | ✔ |
| Harass runs | big only | big only | big only |
| Capture ops | ✔ | ✔ | ✔ |
| **Mines / cloak / gravwell** | ✘ | ✘ | ✔ |
| **Kamikaze** | ✘ | ✘ | 25% draw, per battle |
| Armada teams | 0 | 1 | 2 |
| Research delay | slowest | faster | instant |
| Reaction cadence | slow (time to punish) | ok | fast - don't fight its reactions |

Read the bottom three rows as your scouting goals: the exact moment you see
**minelayers, a cloak generator or a gravwell** near their base, you are playing an
advanced CPU regardless of the settings - expect ramming, and expect better play
after that.

### B2. Where the CPU's model leaks (its exploitable seams)

1. **It fights what it knows.** Its target lists are "ships I have sensor contact
   with". Keep your command centre and main force out of its patrols' sight when
   planning a strike; what it doesn't see, it doesn't fear. (Corollary: your
   recon being shot *is* information for it.)
2. **The 1.4× attack trigger cuts both ways.** It attacks a blob it rates at
   ≥~1.4× its strength. Counter: keep one bait group of rated-weaker composition
   between their staging area and your real force, and let the fast/slow roving
   wings - sorry, *their* reaction wings - walk into your 2×. Against your own
   plans: attack where you have locally ≥1.4× or expect to be disengaged.
3. **Budgets are fixed, not reactive.** 30/30/40 means its economy fleet is a
   hard-protected line item: harassment runs at its collectors force it to buy
   replacements on the economy budget and slow its offensive tempo. The CPU
   harass play (A3) is also the best anti-CPU play: 6-10 light interceptors
   killing collectors every time its patrol looks away pays triple.
4. **No difficulty escalation in a match.** The CPU does not learn you within a
   battle: same menu every minute of the game. If you identify its dominant plan
   (e.g. constant takeout-targets on your research ships), pre-empt it for the
   rest of the game with a standing guard on those ships - it will keep coming
   because nothing tells it to stop.
5. **Command centre first.** Its whole sphere-of-influence, recall, and build
   system is anchored to its command centre. A takeout-target on its command
   centre degrades even a healthy CPU force's coordination; hardcore recall
   *commits everything* to the defence - a deliberate feint at its command centre
   can be the cleanest way to spring an ambush elsewhere.
6. **Allies are absolute.** Allied CPU protection is a hard stop; in team games,
   the fastest way to cut the incoming pressure in half is to force an alliance
   (or to make sure the hates-humans bias puts CPU guns on humans who are *not*
   on your flank).
7. **Cloak beats knowledge.** Cloaked operations are near-invisible in its
   knowledge model - its cloak countermeasures are only proximity sensors built
   after contact. An advanced CPU will build them once it sees you; beginners and
   intermediates never will.
8. **Kamikaze psychology (advanced only).** Damaged CPU strike craft *will* ram
   capital targets. Don't let wounded heavies sit in its strike-craft range;
   intercept at distance.

### B3. Per-difficulty game plans

- **vs Beginner**: out-economy, then a single big fleet with guard wing. It reacts
  slowly (≈2s think, only one big harassment pattern) - pick any two pressure
  points and alternate.
- **vs Intermediate**: it will armada-run (1 team) after scout confirmation and
  run capture ops. Keep salvage corvettes away from your capitals' flanks, watch
  for the recon probe (kill it - it blunts their target selection), and expect the
  scout-confirmed main pushes; counter them the same way, with better timing.
- **vs Advanced**: expect mines/cloak/gravwell/kamikaze and 2-team armadas with
  fast reaction. Fight its information, not its fleet: deny patrolled space, use
  cloak, break its collectors from standoff distance (25,000+, the same harassment
  standoff rule it uses), and bait its 1.4× trigger. Never brawl it head-on with
  local parity.

### B4. The two-minute CPU opening you should survive by default

On skirmish starts, a beginner/intermediate CPU's first 2-3 minutes are: collectors
out, harvest, first strike group assembling, one probe toward your side. If nothing
from you is visible, its first raid tends to be a cheap frigate push. A small guard
wing (2-4 fighters, Sphere formation) over your collectors converts its opening
into wasted budget - and its budget *is* its strategy.

---

## Quick reference card

| Number | Meaning |
| --- | --- |
| 30 / 30 / 40 | build budget split: attack / defence / economy |
| ×1.4 | strength ratio before attacking ("overkill" factor) |
| 63 / 31 / 15 | CPU think cadence (frames) by difficulty |
| 24 / 12 / 12 / 5 | CPU max attack / support / guard / recon teams |
| 25,000 / 15,000 | harass / recon standoff distances |
| 15 → 12 | light interceptor group: start 15, replace at 12 |
| 0 / 1 / 2 | armada team-count by difficulty |
| 25% | per-battle kamikaze unlock draw (advanced only) |

## Sources & tuning note

All behaviours and numbers here come from the game's own computer player, as
extracted in `specs/001-cpu-player-intelligence/spec.md` (behaviour contracts) and
`specs/001-cpu-player-intelligence/baseline-reference.md` (full data inventory with
file provenance). Every constant is game data that scenario/game templates can
retune, so treat the numbers as "current defaults", not laws: re-check the baseline
reference after major balance changes.