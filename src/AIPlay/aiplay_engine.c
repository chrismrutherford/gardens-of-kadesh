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

// set by the /aiPlay command-line option (src/SDL/main.c)
bool32 aiplayRequested = FALSE;

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
    Ship *ship = (Ship *)universe.ShipList.head;
    while (ship != NULL)
    {
        aiplayIdTouch((void *)ship, (int)ship->playerowner->playerIndex);
        ship = (Ship *)((Node *)ship->shiplink.next);
    }
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

    switch (o->verb)
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
        if (o->targetKind != aiplayTarget_ship || !aiplayIdResolve(o->targetId, &target, NULL))
        {
            return aiplayError_badId;
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
    default:
        // build/research/formation land in user story 3 (T025)
        return aiplayError_badRequest;
    }
}

// --------------------------------------------------------------- backend table

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
    NULL,  // shipInfo: filled in US2
};

const aiplayEngineBackend *aiplayEngineBackendGet(void)
{
    return &engineBackend;
}

// called from utyGameSystemsInit (src/SDL/utility.c) once systems are up
void aiplayEngineInit(void)
{
    aiplayInit(aiplayRequested != FALSE, NULL, aiplayEngineBackendGet());
}

// called from utyGameSystemsShutdown
void aiplayEngineShutdown(void)
{
    aiplayShutdown();
}