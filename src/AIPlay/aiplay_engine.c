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
#include "ResearchAPI.h"
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
    dbgAssertOrIgnore(aiplaySavePath[0] == 0);
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

// backend shipInfo: fill from SpaceObj/Ship/Resource internals
static bool backendShipInfo(void *opaque, aiplayShipInfo *out)
{
    SpaceObj *obj = (SpaceObj *)opaque;
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
        out->moving     = (ship->obj.posinfo.isMoving & MOVING_LINEARLY_BIT) != 0;
        out->moveTo[0]  = ship->moveTo.x;
        out->moveTo[1]  = ship->moveTo.y;
        out->moveTo[2]  = ship->moveTo.z;
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

static void backendBuildQueueJSON(aiplayJsonBuf *out, int playerIndex)
{
    // build queues live in the command layer as COMMAND_BUILDING_SHIP orders
    Node *node = universe.mainCommandLayer.todolist.head;
    bool first = true;
    while (node != NULL)
    {
        CommandToDo *todo = (CommandToDo *)listGetStructOfNode(node);
        node = node->next;
        if (todo->ordertype.order != COMMAND_BUILDING_SHIP ||
            todo->buildingship.playerIndex != (udword)playerIndex)
        {
            continue;
        }
        if (!first)
        {
            aiplayJsonBufAdd(out, ",", 1);
        }
        first = false;
        aiplayJsonBufAdd(out, "{", 1);
        aiplayJsonString(out, "type", shipTypeName(todo->buildingship.shipType));
        sdword progressTicks = (sdword)todo->buildingship.frameAtWhichToCreate -
                               (sdword)universe.univUpdateCounter;
        if (progressTicks < 0)
        {
            progressTicks = 0;
        }
        aiplayJsonInt(out, "ticksRemaining", progressTicks);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
    }
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
        if (!aiplayIdResolve(o->shipIds[i], &ship, &owner) || owner != aiplayBoundPlayer())
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
    aiplaySelectAny sel;
    aiplayError     err = buildSelection(o, &sel);
    if (err != aiplayError_none)
    {
        return err;
    }

    // Local selection command for clWrap* (these wrappers send through the same
    // network/command pipeline a human's click generates).
    SelectCommand select;
    select.numShips      = sel.numTargets;
    select.timeLastStatus = 0.0f;
    for (int i = 0; i < (int)sel.numTargets; i++)
    {
        select.ShipPtr[i] = sel.targets[i];
    }

    switch ((int)o->verb)
    {
    case aiplayVerb_move:
    {
        vector to;
        to.x = o->coord[0];
        to.y = o->coord[1];
        to.z = o->coord[2];
        clWrapMove(&universe.mainCommandLayer, &select, select.ShipPtr[0]->obj.posinfo.position, to);
        return aiplayError_none;
    }
    case aiplayVerb_attack:
    {
        void *target = NULL;
        if (o->targetKind != aiplayTarget_ship || !aiplayIdResolve(o->targetId, &target, NULL))
        {
            return aiplayError_badId;
        }
        aiplaySelectAny atk;
        atk.numTargets       = 1;
        atk.timeLastStatus   = 0.0f;
        atk.targets[0]       = (ShipPtr)target;
        clWrapAttack(&universe.mainCommandLayer, &select, (AttackCommand *)&atk);
        return aiplayError_none;
    }
    case aiplayVerb_guard:
    {
        void *target = NULL;
        if (o->targetKind != aiplayTarget_ship || !aiplayIdResolve(o->targetId, &target, NULL))
        {
            return aiplayError_badId;
        }
        SelectCommand protect;
        protect.numShips       = 1;
        protect.timeLastStatus = 0.0f;
        protect.ShipPtr[0]     = (Ship *)target;
        clWrapProtect(&universe.mainCommandLayer, &select, &protect);
        return aiplayError_none;
    }
    case aiplayVerb_dock:
    {
        ShipPtr dockWith = NULL;
        if (o->targetKind == aiplayTarget_ship &&
            (!aiplayIdResolve(o->targetId, (void **)&dockWith, NULL) ||
             dockWith->shipsInsideMe == NULL))
        {
            return aiplayError_badRequest;   // target cannot dock anything
        }
        if (o->targetKind == aiplayTarget_ship && !aiplayIdResolve(o->targetId, (void **)&dockWith, NULL))
        {
            return aiplayError_badId;
        }
        clWrapDock(&universe.mainCommandLayer, &select, DOCK_FOR_BOTHREFUELANDREPAIR, dockWith);
        return aiplayError_none;
    }
    case aiplayVerb_harvest:
    {
        void *target = NULL;
        if (o->targetKind != aiplayTarget_ship ||
            !aiplayIdResolve(o->targetId, &target, NULL) ||
            !aiplayIdIsResource(o->targetId))
        {
            return aiplayError_badId;   // harvest targets must be resources
        }
        clWrapCollectResource(&universe.mainCommandLayer, &select, (ResourcePtr)target);
        return aiplayError_none;
    }
    case aiplayVerb_halt:
    {
        clWrapHalt(&universe.mainCommandLayer, &select);
        return aiplayError_none;
    }
    case aiplayVerb_special:
    {
        aiplaySelectAny spc;
        if (o->targetKind == aiplayTarget_ship)
        {
            void *target = NULL;
            if (!aiplayIdResolve(o->targetId, &target, NULL))
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
        clWrapSpecial(&universe.mainCommandLayer, &select, (SpecialCommand *)&spc);
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
                    aiplay.boundPlayer, (void*)select.ShipPtr[0]);
        ShipType type = shipTypeFromName(o->name);
        if (type <= ShipType_Uninitialized)
        {
            return aiplayError_badRequest;
        }
        // afford pre-check (FR-006): static info must be loaded in this mission
        ShipStaticInfo *si = GetShipStaticInfoSafe(type, universe.players[aiplay.boundPlayer].race);
        if (si == NULL || !bitTest(si->common.infoFlags, IF_InfoLoaded))
        {
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
        ShipPtr builder = (o->numShips > 0) ? select.ShipPtr[0] : p->PlayerMothership;
        clWrapBuildShip(
            &universe.mainCommandLayer, type, p->race,
            (uword)aiplay.boundPlayer, builder);
        return aiplayError_none;
    }
    case aiplayVerb_research:
    {
        TechnologyType tech = StrToTechType((char *)o->name);
        if (tech < 0)
        {
            return aiplayError_badRequest;
        }
        clWrapResearch(
            (udword)RESEARCH_SUBCOMMAND_START, (udword)aiplay.boundPlayer,
            (udword)-1, (udword)tech);
        return aiplayError_none;
    }
    case aiplayVerb_formation:
    {
        TypeOfFormation f = formationFromName(o->name);
        if (f == (TypeOfFormation)-1)
        {
            return aiplayError_badRequest;
        }
        clWrapFormation(&universe.mainCommandLayer, &select, f);
        return aiplayError_none;
    }
    }
    return aiplayError_badRequest;
}

// --------------------------------------------------------------- backend table

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
    backendSetPaused,
    backendStep,
    backendBuildQueueJSON,
    backendResearchJSON,
    backendSaveGame,      // defined below (GamePick.c plumbing)
};

const aiplayEngineBackend *aiplayEngineBackendGet(void)
{
    return &engineBackend;
}

// called from utyGameSystemsInit (src/SDL/utility.c) once systems are up
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
void aiplayEngineShutdown(void)
{
    aiplayShutdown();
}