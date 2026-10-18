//
// AI Play layer — engine backend (compiled into the game executable only).
// Translates the pure core's backend vtable into engine calls. All gameplay
// commands go through clWrap* — the same funnel the human UI and the built-in
// CPU player use (see specs/002-ai-plays-the-game-via-a-spectating-ui/research.md).
//
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay.h"
#include "aiplay_internal.h"

#include "AIPlayer.h"
#include "Globals.h"
#include "Universe.h"
#include "CommandWrap.h"
#include "CommandNetwork.h"
#include "SpaceObj.h"
#include "ObjTypes.h"
#include "Options.h"
#include "CommandDefs.h"
#include "ConsMgr.h"
#include "ShipSelect.h"
#include "ResearchAPI.h"
#include "Attributes.h"
#include "Debug.h"
#include "utility.h"    // utyLoadSinglePlayerGameGivenFilename
#include "FormationDefs.h"
#include "CommandLayer.h"
#include <strings.h>

// set by the /aiPlay command-line option (src/SDL/main.c)
bool32 aiplayRequested = FALSE;

// set by /aiPlayLoad <savegame name> — auto-load the saved game at startup so
// test rounds don't re-watch the intro (user convenience, feature 002)
char aiplayAutoSaveName[128]  = "";
bool32  aiplayAutoSaveIsMP    = FALSE;

extern char SinglePlayerSavedGamesPath[];   // src/Game/GamePick.c

// /aiPlayLoad <name>     -> single-player savegame
// /aiPlayLoadMP <name>   -> multi-player savegame
bool32 aiplayCmdlineSave(char *string)
{
    strncpy(aiplayAutoSaveName, string, sizeof(aiplayAutoSaveName) - 1);
    aiplayAutoSaveName[sizeof(aiplayAutoSaveName) - 1] = '\0';
    return TRUE;
}

bool32 aiplayCmdlineSaveMP(char *string)
{
    aiplayAutoSaveIsMP = TRUE;
    return aiplayCmdlineSave(string);
}

static char aiplaySavePath[512];    // must outlive gameStart's use

void aiplayEngineAutoLoad(void)
{
    if (!aiplayRequested || aiplayAutoSaveName[0] == '\0' || aiplayRequested == FALSE)
    {
        return;
    }
    static bool32 aiplayAutoLoadDone = FALSE;
    if (aiplayAutoLoadDone)
    {   // universe rebuilds (mission transitions) must NOT reload the savegame:
        // they would pin us to the first mission forever
        return;
    }
    aiplayAutoLoadDone = TRUE;
    strcpy(aiplaySavePath, aiplayAutoSaveIsMP ? "SavedGames\\MultiPlayer\\"
                                              : "SavedGames\\SinglePlayer\\");
    strcat(aiplaySavePath, aiplayAutoSaveName);
    dbgMessagef("AI play: auto-loading %s savegame '%s'",
                aiplayAutoSaveIsMP ? "multiplayer" : "singleplayer", aiplaySavePath);
    if (aiplayAutoSaveIsMP)
    {
        utyLoadMultiPlayerGameGivenFilename(aiplaySavePath);
    }
    else
    {
        utyLoadSinglePlayerGameGivenFilename(aiplaySavePath);
    }
}

// generated from Ships/ShipDefs.h ShipType_enum (skipping index -1)
static const char *aiplayShipTypeNames[] = {
#include "shiptype_names.inc"
};

static ShipType shipTypeFromName(const char *name)
{
    for (unsigned long i = 0; i < sizeof(aiplayShipTypeNames) / sizeof(aiplayShipTypeNames[0]); i++)
    {
        if (strcasecmp(aiplayShipTypeNames[i], name) == 0)
        {
            return (ShipType)(sdword)i;   // table[0] == ShipType 0 (AdvanceSupportFrigate)
        }
    }
    return ShipType_Uninitialized;
}

static TypeOfFormation formationFromName(const char *name)
{
    struct { const char *n; TypeOfFormation f; } table[] = {
        {"delta", DELTA_FORMATION}, {"broad", BROAD_FORMATION},
        {"delta3d", DELTA3D_FORMATION}, {"claw", CLAW_FORMATION},
        {"wall", WALL_FORMATION}, {"sphere", SPHERE_FORMATION},
    };
    for (unsigned long i = 0; i < sizeof(table)/sizeof(table[0]); i++)
    {
        if (strcasecmp(table[i].n, name) == 0)
        {
            return table[i].f;
        }
    }
    return (TypeOfFormation)-1;
}

static const char *shipTypeName(ShipType t)
{
    sdword i = (sdword)t;
    if (i < 0 || (unsigned long)i >= sizeof(aiplayShipTypeNames) / sizeof(aiplayShipTypeNames[0]))
    {
        return "Unknown";
    }
    return aiplayShipTypeNames[i];
}

static bool32 neutralizeSaved[6];
static bool32 neutralizeSavedUsed[6];

// --------------------------------------------------------------- introspection

static int backendNumPlayers(void)
{
    return (int)universe.numPlayers;
}

static bool backendPlayerAlive(int playerIndex)
{
    return universe.players[playerIndex].playerState == PLAYER_ALIVE;
}

static bool backendPlayerIsAI(int playerIndex)
{
    if (playerIndex == aiplayBoundPlayer())
    {
        return false;  // the layer-neutralized player reads as agent-controlled
    }
    return universe.players[playerIndex].aiPlayer != NULL;
}

static int64_t backendPlayerResourceUnits(int playerIndex)
{
    return (int64_t)universe.players[playerIndex].resourceUnits;
}

static const char *backendMapName(void)
{
    return "skirmish";  // refined in user story 2
}

static bool backendGameRunning(void)
{
    return gameIsRunning != 0;
}

static uint32_t backendTick(void)
{
    return universe.univUpdateCounter;
}

static double backendTimeElapsed(void)
{
    return (double)universe.totaltimeelapsed;
}

static bool backendIsPaused(void)
{
    return universePause != 0;
}

static void backendSetPlayerNeutralized(int playerIndex, bool neutralized)
{
    if (playerIndex < 0 || playerIndex >= (int)universe.numPlayers ||
        playerIndex >= (int)(sizeof(neutralizeSaved) / sizeof(neutralizeSaved[0])))
    {
        return;
    }
    if (neutralized && !neutralizeSavedUsed[playerIndex])
    {
        neutralizeSaved[playerIndex]    = universe.players[playerIndex].aiPlayer != NULL;
        neutralizeSavedUsed[playerIndex] = true;
        universe.players[playerIndex].aiPlayer = NULL;
    }
    else if (!neutralized && neutralizeSavedUsed[playerIndex])
    {
        if (neutralizeSaved[playerIndex])
        {
            // the saved pointer is not preserved; a fresh CPU AI is only started
            // on init, so restoring "had AI" is not possible mid-match. Unbinding
            // therefore leaves the side unattended (documented FR-010 behavior).
        }
        universe.players[playerIndex].aiPlayer   = NULL;
        neutralizeSavedUsed[playerIndex]          = false;
    }
}

// --------------------------------------------------------- ship id sync (tick end)

void aiplayEngineSyncShips(void)
{
    Node *node = universe.ShipList.head;
    while (node != NULL)
    {
        Ship *ship = (Ship *)listGetStructOfNode(node);
        aiplayIdTouch((void *)ship, (int)ship->playerowner->playerIndex);
        node = node->next;
    }
    node = universe.ResourceList.head;
    while (node != NULL)
    {
        aiplayIdTouchEx(listGetStructOfNode(node), -1, true);
        node = node->next;
    }
}

// per-ship current order from the command layer's CommandToDo list (T020)
static void fillShipOrder(Ship *ship, aiplayShipInfo *out)
{
    out->orderVerb = NULL;
    Node *node = universe.mainCommandLayer.todolist.head;
    while (node != NULL)
    {
        CommandToDo *todo = (CommandToDo *)listGetStructOfNode(node);
        node = node->next;
        SelectCommand *sel = todo->selection;
        bool mine = false;
        if (sel != NULL && todo->ordertype.order != COMMAND_BUILDING_SHIP)
        {
            for (sdword i = 0; i < sel->numShips; i++)
            {
                if (sel->ShipPtr[i] == (ShipPtr)ship)
                {
                    mine = true;
                    break;
                }
            }
        }
        if (!mine)
        {
            continue;
        }
        switch (todo->ordertype.order)
        {
        case COMMAND_MOVE:
            out->orderVerb = "move";
            out->orderCoord[0] = todo->move.destination.x;
            out->orderCoord[1] = todo->move.destination.y;
            out->orderCoord[2] = todo->move.destination.z;
            break;
        case COMMAND_ATTACK:
            out->orderVerb = "attack";
            if (todo->attack->numTargets > 0)
            {
                out->orderTargetId = (uint64_t)(uintptr_t)todo->attack->TargetPtr[0];
            }
            break;
        case COMMAND_COLLECT_RESOURCES:
            out->orderVerb = "harvest";
            if (todo->collect.resource != NULL)
            {   // CollectResourceCommand is a VALUE member carrying one ResourcePtr
                out->orderTargetId = (uint64_t)(uintptr_t)todo->collect.resource;
            }
            break;
        case COMMAND_DOCK:
            out->orderVerb = "dock";
            break;
        case COMMAND_LAUNCH_SHIP:
            out->orderVerb = "launch";
            break;
        case COMMAND_SPECIAL:
            out->orderVerb = "special";
            break;
        case COMMAND_HALT:
            out->orderVerb = "halt";
            break;
        case COMMAND_MILITARY_PARADE:
            out->orderVerb = "parade";
            break;
        default:
            out->orderVerb = "order?";
            break;
        }
        if ((todo->ordertype.attributes & COMMAND_MASK_PASSIVE_ATTACKING) &&
            out->orderVerb == NULL)
        {
            out->orderVerb = "passive-attack";
        }
        if (out->orderVerb != NULL)
        {
            return;    // first matching command wins (later ones are additive)
        }
    }
}

// id-map entries can outlive engine objects between ticks (e.g. ship death
// hooks missed by a transition); never dereference a stale handle
static bool backendObjectLive(void *opaque)
{
    SpaceObj *obj = (SpaceObj *)opaque;
    switch (obj->header.objtype)
    {
    case OBJ_ShipType:
    {
        for (Node *node = universe.ShipList.head; node != NULL; node = node->next)
        {
            if (listGetStructOfNode(node) == opaque)
            {
                return true;
            }
        }
        return false;
    }
    case OBJ_AsteroidType:
    {
        for (Node *node = universe.ResourceList.head; node != NULL; node = node->next)
        {
            if (listGetStructOfNode(node) == opaque)
            {
                return true;
            }
        }
        return false;
    }
    default:
        return false;
    }
}

// backend shipInfo: fill from SpaceObj/Ship/Resource internals
static bool backendShipInfo(void *opaque, aiplayShipInfo *out)
{
    SpaceObj *obj = (SpaceObj *)opaque;
    if (!backendObjectLive(opaque))
    {
        return false;
    }
    memset(out, 0, sizeof(*out));
    if (obj->header.objtype != OBJ_ShipType && obj->header.objtype != OBJ_AsteroidType)
    {
        return false;
    }
    out->ship = opaque;
    out->pos[0] = obj->posinfo.position.x;
    out->pos[1] = obj->posinfo.position.y;
    out->pos[2] = obj->posinfo.position.z;
    out->vel[0] = obj->posinfo.velocity.x;
    out->vel[1] = obj->posinfo.velocity.y;
    out->vel[2] = obj->posinfo.velocity.z;

    if (obj->header.objtype == OBJ_ShipType)
    {
        Ship *ship = (Ship *)opaque;
        out->owner      = (int)ship->playerowner->playerIndex;
        out->isResource = false;
        out->typeName   = shipTypeName(ship->shiptype);
        out->health     = ship->targetable.health;
        out->healthMax  = ship->staticinfo->health.maxhealth;
        {
            GunStaticInfo *gi = ship->staticinfo->gunStaticInfo;
            if (gi != NULL)
            {
                out->numGuns = gi->numGuns;
                for (sdword g = 0; g < gi->numGuns; g++)
                {
                    GunStatic *gs = &gi->gunstatics[g];
                    real32 dmg = gs->gunDamageHi[Aggressive];
                    if (dmg > 0.0f && gs->firetime > 0.0f)
                    {
                        out->dps += dmg / gs->firetime;
                    }
                    if (gs->bulletrange > out->range)
                    {
                        out->range = gs->bulletrange;
                    }
                }
            }
        }
        out->moving     = (ship->obj.posinfo.isMoving & MOVING_LINEARLY_BIT) != 0;
        fillShipOrder(ship, out);
        if (out->orderVerb == NULL && out->moving)
        {   // no command but physically moving (e.g. flight momentum)
            out->orderVerb = "moving";
        }
        return true;
    }
    if (obj->header.objtype == OBJ_AsteroidType)
    {
        Resource *res = (Resource *)opaque;
        out->owner      = -1;
        out->isResource = true;
        out->typeName   = "Asteroid";
        out->health     = res->targetable.health;
        out->healthMax  = res->staticinfo->_health.maxhealth;
        out->resourceValue = res->resourcevalue;
        return true;
    }
    return false;
}

// ---- time control (US4): the engine's own pause + the opPauseOrders seam ----

static bool32 aiplayPrevOpPauseOrders = 0;

static void backendSetPaused(bool paused)
{
    if (paused)
    {
        aiplayPrevOpPauseOrders = (bool32)opPauseOrders;
        opPauseOrders  = TRUE;     // AI orders queue while paused (research R-3)
        universePause  = TRUE;
    }
    else
    {
        universePause = FALSE;
        opPauseOrders = aiplayPrevOpPauseOrders;
        aiplayPrevOpPauseOrders = 0;
    }
}

// builds go through the deterministic-build funnel (ConsMgr cmDetermProgress),
// the same queue the human UI displays
typedef struct
{
    aiplayJsonBuf *out;
    bool first;
} bqJSONCtx;

static void bqJSONCB(void *userData, ShipType shipType, ShipRace shipRace,
                     sdword numShips, sdword ticksLeft, sdword costPerShip)
{
    bqJSONCtx *ctx = userData;
    (void)shipRace;
    (void)costPerShip;
    if (!ctx->first)
    {
        aiplayJsonBufAdd(ctx->out, ",", 1);
    }
    ctx->first = false;
    aiplayJsonBufAdd(ctx->out, "{", 1);
    aiplayJsonString(ctx->out, "type", shipTypeName(shipType));
    aiplayJsonInt(ctx->out, "quantity", numShips);
    aiplayJsonInt(ctx->out, "ticksRemaining", ticksLeft > 0 ? ticksLeft : 0);
    aiplayJsonTrimComma(ctx->out);
    aiplayJsonBufAdd(ctx->out, "}", 1);
}

static void backendBuildQueueJSON(aiplayJsonBuf *out, int playerIndex)
{
    bqJSONCtx ctx = {out, true};
    cmDetermBuildQueueIterate(playerIndex, &ctx, bqJSONCB);
    aiplayJsonTrimComma(out);
}

static void backendResearchJSON(aiplayJsonBuf *out, int playerIndex)
{
    // array entries: {tech, labs, timeLeft} per research topic in progress
    PlayerResearchInfo *ri = &universe.players[playerIndex].researchinfo;
    if (!ri->CanDoResearch)
    {
        return;
    }
    bool first = true;
    Node *node = ri->listoftopics.head;
    while (node != NULL)
    {
        ResearchTopic *topic = (ResearchTopic *)listGetStructOfNode(node);
        node = node->next;
        if (!first)
        {
            aiplayJsonBufAdd(out, ",", 1);
        }
        first = false;
        aiplayJsonBufAdd(out, "{", 1);
        aiplayJsonString(out, "tech", TechTypeToString(topic->techresearch));
        aiplayJsonInt(out, "labs", topic->numlabsresearching);
        aiplayJsonDouble(out, "timeLeft", topic->timeleft);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
    }
    aiplayJsonTrimComma(out);
}

static bool backendStep(int ticks)
{
    if (universePause == FALSE)
    {
        return false;
    }
    for (int i = 0; i < ticks; i++)
    {
        // temporary step: run one manual update; the universe task itself is
        // paused, so no double-ticking (research R-1)
        universePause = FALSE;
        (void)univUpdate(UNIVERSE_UPDATE_PERIOD);
        universePause = TRUE;
    }
    return true;
}

// --------------------------------------------------------------- order dispatch

// resolve ship ids (owned by the bound player) into a SelectCommand-ish build
static sdword resolveShips(const aiplayOrderRequest *o, Ship **ships, sdword maxShips)
{
    sdword n = 0;
    for (size_t i = 0; i < o->numShips && n < maxShips; i++)
    {
        void *  ship  = NULL;
        int32_t owner = -1;
        if (!aiplayIdResolve(o->shipIds[i], &ship, &owner) ||
            !backendObjectLive(ship) ||
            owner != aiplayBoundPlayer())
        {
            return -1;
        }
        ships[n++] = (Ship *)ship;
    }
    return n;
}

typedef struct
{
    sdword  numTargets;
    real32  timeLastStatus;
    ShipPtr targets[AIPLAY_MAX_SHIPS_PER_ORDER];
} aiplaySelectAny;

static aiplayError buildSelection(const aiplayOrderRequest *o, aiplaySelectAny *sel)
{
    sdword n = resolveShips(o, (Ship **)sel->targets, AIPLAY_MAX_SHIPS_PER_ORDER);
    if (n <= 0)
    {
        return aiplayError_badId;
    }
    sel->numTargets     = n;
    sel->timeLastStatus = 0.0f;
    return aiplayError_none;
}

static aiplayError backendDispatchOrder(const aiplayOrderRequest *o)
{
    // verbs that take no ship selection (contract: order.research takes only
    // "tech", order.autolaunch only "onOff")
    bool shipless = (o->verb == aiplayVerb_research || o->verb == aiplayVerb_autolaunch) ||
                    (o->verb == aiplayVerb_build && o->numShips == 0);

    aiplaySelectAny sel;
    memset(&sel, 0, sizeof(sel));
    aiplayError err = aiplayError_none;
    if (!shipless)
    {
        err = buildSelection(o, &sel);
        if (err != aiplayError_none)
        {
            return err;
        }
    }

    // Local selection command for clWrap* (these wrappers send through the same
    // network/command pipeline a human's click generates). SelectCommand has a
    // trailing ShipPtr[1] array meant for over-allocation by the caller —
    // a plain stack object overflows on multi-ship orders.
    struct
    {
        SelectCommand cmd;
        ShipPtr extra[AIPLAY_MAX_SHIPS_PER_ORDER];
    } selectBuf;
    SelectCommand *select = &selectBuf.cmd;
    select->numShips      = sel.numTargets;
    select->timeLastStatus = 0.0f;
    for (int i = 0; i < (int)sel.numTargets; i++)
    {
        select->ShipPtr[i] = sel.targets[i];
    }

    switch ((int)o->verb)
    {
    case aiplayVerb_move:
    {
        vector to;
        to.x = o->coord[0];
        to.y = o->coord[1];
        to.z = o->coord[2];
        clWrapMove(&universe.mainCommandLayer, select, select->ShipPtr[0]->obj.posinfo.position, to);
        return aiplayError_none;
    }
    case aiplayVerb_attack:
    {
        void *target = NULL;
        if (o->targetKind != aiplayTarget_ship || !aiplayIdResolve(o->targetId, &target, NULL) || !backendObjectLive(target))
        {
            return aiplayError_badId;
        }
        aiplaySelectAny atk;
        atk.numTargets       = 1;
        atk.timeLastStatus   = 0.0f;
        atk.targets[0]       = (ShipPtr)target;
        clWrapAttack(&universe.mainCommandLayer, select, (AttackCommand *)&atk);
        return aiplayError_none;
    }
    case aiplayVerb_guard:
    {
        void *target = NULL;
        if (o->targetKind != aiplayTarget_ship || !aiplayIdResolve(o->targetId, &target, NULL) || !backendObjectLive(target))
        {
            return aiplayError_badId;
        }
        SelectCommand protect;
        protect.numShips       = 1;
        protect.timeLastStatus = 0.0f;
        protect.ShipPtr[0]     = (Ship *)target;
        clWrapProtect(&universe.mainCommandLayer, select, &protect);
        return aiplayError_none;
    }
    case aiplayVerb_dock:
    {
        ShipPtr dockWith = NULL;
        if (o->targetKind == aiplayTarget_ship &&
            (!aiplayIdResolve(o->targetId, (void **)&dockWith, NULL) ||
             !backendObjectLive(dockWith) || dockWith->shipsInsideMe == NULL))
        {
            return aiplayError_badRequest;   // target cannot dock anything
        }
        if (o->targetKind == aiplayTarget_ship &&
            (!aiplayIdResolve(o->targetId, (void **)&dockWith, NULL) ||
             !backendObjectLive(dockWith)))
        {
            return aiplayError_badId;
        }
        clWrapDock(&universe.mainCommandLayer, select, DOCK_FOR_BOTHREFUELANDREPAIR, dockWith);
        return aiplayError_none;
    }
    case aiplayVerb_harvest:
    {
        void *target = NULL;
        if (o->targetKind != aiplayTarget_ship ||
            !aiplayIdResolve(o->targetId, &target, NULL) ||
            !backendObjectLive(target) ||
            !aiplayIdIsResource(o->targetId))
        {
            return aiplayError_badId;   // harvest targets must be resources
        }
        clWrapCollectResource(&universe.mainCommandLayer, select, (ResourcePtr)target);
        return aiplayError_none;
    }
    case aiplayVerb_halt:
    {
        clWrapHalt(&universe.mainCommandLayer, select);
        return aiplayError_none;
    }
    case aiplayVerb_special:
    {
        aiplaySelectAny spc;
        if (o->targetKind == aiplayTarget_ship)
        {
            void *target = NULL;
            if (!aiplayIdResolve(o->targetId, &target, NULL) || !backendObjectLive(target))
            {
                return aiplayError_badId;
            }
            spc.numTargets     = 1;
            spc.timeLastStatus = 0.0f;
            spc.targets[0]     = (ShipPtr)target;
        }
        else
        {
            spc.numTargets = 0;
        }
        clWrapSpecial(&universe.mainCommandLayer, select, (SpecialCommand *)&spc);
        return aiplayError_none;
    }
    case aiplayVerb_select:
    {
        // selection is layer-side state; the human's selSelected must not change
        return aiplayError_none;
    }
    case aiplayVerb_build:
    {
        dbgMessagef("AIPLAY-BUILD: type=%s race=%d player=%d creator=%p",
                    o->name, (int)universe.players[aiplay.boundPlayer].race,
                    aiplay.boundPlayer, (void*)select->ShipPtr[0]);
        ShipType type = shipTypeFromName(o->name);
        if (type <= ShipType_Uninitialized)
        {
            return aiplayError_badRequest;
        }
        // afford pre-check (FR-006): static info must be loaded in this mission
        ShipStaticInfo *si = GetShipStaticInfoSafe(type, universe.players[aiplay.boundPlayer].race);
        if (si == NULL || !bitTest(si->common.infoFlags, IF_InfoLoaded))
        {
            dbgMessagef("AIPLAY-REJECT: %s statics %s", o->name,
                        si == NULL ? "NULL" : "not IF_InfoLoaded");
            return aiplayError_badRequest;      // e.g. "not loaded properly - stubbing out"
        }
        {
            sdword cost    = si->buildCost;
            sdword quantity = o->quantity > 1 ? o->quantity : 1;
            if (universe.players[aiplay.boundPlayer].resourceUnits < (sdword)(cost * quantity))
            {
                return aiplayError_unaffordable;
            }
        }
        Player *p = &universe.players[aiplay.boundPlayer];
        ShipPtr builder = (o->numShips > 0) ? select->ShipPtr[0] : p->PlayerMothership;
        // same funnel the human build screen uses (clWrapDeterministicBuild):
        // keeps the UI build queue (ConsMgr cmDetermProgress) in sync with
        // AI-placed builds, and routes RUs through the shared drain code
        clWrapDeterministicBuild(
            CMD_START, &universe.mainCommandLayer, o->quantity,
            type, p->race, (uword)aiplay.boundPlayer, builder);
        return aiplayError_none;
    }
    case aiplayVerb_research:
    {
        TechnologyType tech = StrToTechType((char *)o->name);
        if (tech < 0)
        {
            return aiplayError_badRequest;
        }
        Player *rp = &universe.players[aiplay.boundPlayer];
        sdword lab = rmFindFreeLab(rp);
        if (rp->researchinfo.CanDoResearch == FALSE || lab == -1)
        {
            return aiplayError_noLabs;
        }
        clWrapResearch(
            (udword)RESEARCH_SUBCOMMAND_START, (udword)aiplay.boundPlayer,
            (udword)lab, (udword)tech);
        return aiplayError_none;
    }
    case aiplayVerb_tactics:
    {
        static const struct { const char *n; TacticsType t; } tac[] = {
            {"evasive", Evasive}, {"neutral", Neutral}, {"aggressive", Aggressive},
        };
        TacticsType want = (TacticsType)-1;
        for (unsigned long i = 0; i < sizeof(tac)/sizeof(tac[0]); i++)
        {
            if (strcasecmp(tac[i].n, o->name) == 0) { want = tac[i].t; break; }
        }
        if (want == (TacticsType)-1)
        {
            return aiplayError_badRequest;
        }
        clWrapSetTactics(&universe.mainCommandLayer, select, want);
        return aiplayError_none;
    }
    case aiplayVerb_launch:
    {
        // launch docked fighters from the first dock-capable ship given
        ShipPtr from = select->ShipPtr[0];
        if (from == NULL || from->shipsInsideMe == NULL)
        {
            return aiplayError_badRequest;
        }
        SelectCommand docked;
        docked.numShips = 0;
        docked.timeLastStatus = 0.0f;
        InsideShip *inside = (InsideShip *)from->shipsInsideMe->insideList.head;
        while (inside != NULL && docked.numShips < COMMAND_MAX_SHIPS)
        {
            docked.ShipPtr[docked.numShips++] = inside->ship;
            inside = (InsideShip *)((Node *)inside->node.next);
        }
        if (docked.numShips == 0)
        {
            return aiplayError_noBuilder;   // nothing docked to launch
        }
        clWrapLaunchMultipleShips(&universe.mainCommandLayer, &docked, from);
        return aiplayError_none;
    }
    case aiplayVerb_autolaunch:
    {
        udword onOff = strcasecmp(o->name, "on") == 0 ? 1 : 0;
        clWrapAutoLaunch(onOff, (udword)aiplay.boundPlayer);
        return aiplayError_none;
    }
    case aiplayVerb_scuttle:
    {
        clWrapScuttle(&universe.mainCommandLayer, select);
        return aiplayError_none;
    }
    case aiplayVerb_formation:
    {
        TypeOfFormation f = formationFromName(o->name);
        if (f == (TypeOfFormation)-1)
        {
            return aiplayError_badRequest;
        }
        clWrapFormation(&universe.mainCommandLayer, select, f);
        return aiplayError_none;
    }
    }
    return aiplayError_badRequest;
}

// --------------------------------------------------------------- backend table

// exact research/build tree from the engine (rmCanBuildShip + tech masks)
static void backendTechTreeJSON(aiplayJsonBuf *out, int playerIndex)
{
    Player *p = &universe.players[playerIndex];
    // labs = ResearchShips owned by this player (each is one lab)
    udword labs = 0;
    Node *node = universe.ShipList.head;
    while (node != NULL)
    {
        Ship *ship = (Ship *)listGetStructOfNode(node);
        node = node->next;
        if (ship->playerowner == p && ship->shiptype == ResearchShip)
        {
            labs++;
        }
    }

    aiplayJsonBufAddStr(out, "{");
    aiplayJsonInt(out, "labs", (int64_t)labs);
    aiplayJsonBool(out, "canResearch", p->researchinfo.CanDoResearch != FALSE);

    aiplayJsonBufAddStr(out, "\"locked\":[");
    bool first = true;
    for (ShipType type = 0; type < (ShipType)TRADERS_LAST_SHIP; type = (ShipType)((sdword)type + 1))
    {
        if (p->researchinfo.techstat == NULL)
        {
            break;
        }
        if (rmCanBuildShip(p, type))
        {
            continue;    // buildable now: not interesting for planning
        }
        if (!first)
        {
            aiplayJsonBufAdd(out, ",", 1);
        }
        first = false;
        aiplayJsonBufAdd(out, "{", 1);
        aiplayJsonString(out, "type", shipTypeName(type));
        udword need = p->researchinfo.techstat->TechNeededToBuildShip[(sdword)type];
        aiplayJsonBool(out, "disabled", bitTest(need, RM_Disabled) != 0);
        aiplayJsonBufAddStr(out, "\"missing\":[");
        bool mfirst = true;
        for (TechnologyType tech = 0; tech < NumTechnologies; tech = (TechnologyType)((sdword)tech + 1))
        {
            if (bitTest(need, TechToBit(tech)) &&
                !bitTest(p->researchinfo.HasTechnology, TechToBit(tech)))
            {
                if (!mfirst)
                {
                    aiplayJsonBufAdd(out, ",", 1);
                }
                mfirst = false;
                aiplayJsonBufPrintf(out, "\"%s\",", TechTypeToString(tech));
                aiplayJsonTrimComma(out);
            }
        }
        aiplayJsonBufAdd(out, "],", 2);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
    }
    aiplayJsonBufAdd(out, "],", 2);
    aiplayJsonTrimComma(out);
    aiplayJsonBufAdd(out, "}", 1);
}

static void backendTraceLog(const char *line)
{
    dbgMessagef("%s", line);
}

static bool backendSaveGame(const char *name);

static const aiplayEngineBackend engineBackend = {
    backendNumPlayers,
    backendPlayerAlive,
    backendPlayerIsAI,
    backendPlayerResourceUnits,
    backendMapName,
    backendGameRunning,
    backendTick,
    backendTimeElapsed,
    backendIsPaused,
    backendSetPlayerNeutralized,
    backendDispatchOrder,
    backendShipInfo,
    backendObjectLive,
    backendSetPaused,
    backendStep,
    backendBuildQueueJSON,
    backendResearchJSON,
    backendSaveGame,      // defined below (GamePick.c plumbing)
    backendTraceLog,
    backendTechTreeJSON,
};

const aiplayEngineBackend *aiplayEngineBackendGet(void)
{
    return &engineBackend;
}

// called from utyGameSystemsInit (src/SDL/utility.c) once systems are up
static bool32 aiplayAutoLoadDone = FALSE;
extern bool32 enableAVI;    // src/SDL/main.c (movies/animatics master switch)
extern bool32 nisEnabled;   // src/Game/NIS.c (scripted in-engine cinematics)

static bool backendSaveGame(const char *name)
{
    // reuse the front-end's SaveGame plumbing (pause-safe, MP/SP aware)
    extern void gpSaveGivenGame(char *gamename);      // src/Game/GamePick.c
    static char path[512];
    strcpy(path, aiplayAutoSaveIsMP ? "SavedGames/MultiPlayer/ai-"
                                    : "SavedGames/SinglePlayer/ai-");
    strcat(path, name);
    dbgMessagef("AIPLAY-SAVE: calling gpSaveGivenGame('%s')", path);
    extern bool32 SaveGame(char *filename);
    bool32 direct = SaveGame(path);   // diagnostic: direct call, no FE plumbing
    dbgMessagef("AIPLAY-SAVE: SaveGame returned %d", direct);
    return direct != FALSE;
}

void aiplayEngineInit(void)
{
    aiplayInit(aiplayRequested != FALSE, NULL, aiplayEngineBackendGet());
    if (aiplayRequested)
    {
        // spectating/debug mode: skip ALL cinematics (movies, animatics, NIS)
        enableAVI  = FALSE;
        nisEnabled = FALSE;
    }
    aiplayEngineAutoLoad();     // /aiPlayLoad: straight into the saved game
}

// called from utyGameSystemsShutdown
// universe rebuilds (mission transitions/hyperspace) run gameEnd->gameStart:
// the transport dies with the shutdown hook and must come back here
void aiplayEngineRestart(void)
{
    if (!aiplayRequested)
    {
        return;
    }
    aiplayShutdown();
    aiplayEngineInit();
}

void aiplayEngineShutdown(void)
{
    aiplayShutdown();
}