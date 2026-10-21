# Homeworld AI Playbook (extracted from GoK's Homeworld.big — authoritative)

## Build costs (Kushan/R1)
```
LightDefender                   25 RU
Probe                           30 RU
LightInterceptor                35 RU
HeavyInterceptor                55 RU
HeavyDefender                   65 RU
CloakedFighter                  85 RU
DefenseFighter                  85 RU
AttackBomber                    85 RU
LightCorvette                  135 RU
RepairCorvette                 150 RU
SalCapCorvette                 220 RU
MultiGunCorvette               225 RU
HeavyCorvette                  250 RU
MinelayerCorvette              295 RU
AdvanceSupportFrigate          425 RU
CloakGenerator                 500 RU
StandardFrigate                575 RU
IonCannonFrigate               650 RU
ResourceCollector              650 RU
ResourceController             680 RU
ResearchShip                   700 RU
DDDFrigate                     800 RU
GravWellGenerator              800 RU
SensorArray                    800 RU
StandardDestroyer             1350 RU
MissileDestroyer              1500 RU
Carrier                       2000 RU
HeavyCruiser                  3700 RU
Mothership                    8000 RU
```

## Fleet: ship -> research prerequisites (MP, R1 Kushan)
```
LightInterceptor             ;MPR1ShipDepend,LightDefender
Probe                        ;MPR1ShipDepend,Mothership
ResearchShip                 ;MPR1ShipDepend,ResourceCollector
LightInterceptor             MassDrive1Kt
HeavyInterceptor             MassDrive1Kt,Chassis1
HeavyDefender                MassDrive1Kt,,FireControl
CloakedFighter               MassDrive1Kt,,Chassis1,,CloakDefenseFighter
DefenseFighter               MassDrive1Kt,,Chassis1,,CloakDefenseFighter
AttackBomber                 MassDrive1Kt,,Chassis1,,PlasmaWeapons
RepairCorvette               MassDrive10Kt
LightCorvette                MassDrive10Kt
SalCapCorvette               MassDrive10Kt,,Chassis2
MultiGunCorvette             MassDrive10Kt,,Chassis2,,TargetingSystems
HeavyCorvette                MassDrive10Kt,,Chassis2,,MediumGuns
MinelayerCorvette            MassDrive10Kt,,Chassis2,,MineLayerTech
AdvanceSupportFrigate        MassDrive100Kt,
StandardFrigate              MassDrive100Kt,,Chassis3
IonCannonFrigate             MassDrive100Kt,,Chassis3,,IonWeapons
DDDFrigate                   MassDrive100Kt,,Chassis3,,DDDFDFGFTech
DFGFrigate                   MassDrive100Kt,,Chassis3,,DDDFDFGFTech
StandardDestroyer            MassDrive1Mt,,IonWeapons
MissileDestroyer             MassDrive1Mt,,MissileWeapons
Carrier                      MassDrive1Mt,,RepairTech
HeavyCruiser                 MassDrive1Mt,,RepairTech,,HeavyGuns,,IonWeapons
SensorArray                  ProximityDetector,,SensorsArrayTech
GravWellGenerator            MassDrive100Kt,,GravityWellGeneratorTech
CloakGenerator               MassDrive100Kt,,CloakGeneratorTech
ResourceController           MassDrive100Kt
ProximitySensor              ProximityDetector
```

## Always-free ships (no research):
  ResourceCollector (650 RU!), SalCapCorvette, Probe, LightInterceptor,
  LightDefender, Mothership, ResearchShip (700 RU) — needed for ANY research (labs 1-6)

## Research chains (research a tech needs its prereqs first):
```
Chassis1      <- MassDrive1Kt
Chassis2      <- MassDrive10Kt
Chassis3      <- MassDrive100Kt
MassDrive1Mt  <- Chassis3
Destroyers    <- Chassis3 (MassDrive1Mt) + MissileWeapons
HeavyCruiser  <- MassDrive1Mt + RepairTech(MassDrive100Kt+Chassis3) + HeavyGuns(RepairTech)
IonFr         <- Chassis3 + IonWeapons
```

## Opening playbook (tick 0):
1. Build 2x ResourceCollector (650 RU each) — queue both; RU starts 1000,
   collector pays ~100+ RU/min once harvesting (verify via `order: harvest`
   — build-spends make the income stat swing, see dictionary).
2. Build ResearchShip next (700) — labs make research possible.
3. Research MassDrive1Kt -> Chassis1 while collectors build.
4. Keep 2-3 interceptors on CAPSLOCK-visible... fleet: order_tactics aggressive.
5. Attack groups of 3-4 vs single target; retreat (guard mothership) when hp<50%.
6. Once Chassis3 + IonWeapons: 2x IonCannonFrigate = the mid-game win pair.

## Notes
- Fighters BUILD DOCKED: order_launch the Mothership after builds finish
  (or autolaunch on once at game start).
- Snapshot 'income' = NET RU-bank delta per minute (own side), NOT a harvest
  rate: build spends show as negative, engine/map RU injections show as
  spikes. Do not read "high income" as "my collectors are working" — verify
  harvesting by collector `order: harvest` verbs instead.
- paused snapshots are byte-identical; use pause only when re-planning.
- Order ids are echoed in events (orderApplied/orderRejected) — read them.