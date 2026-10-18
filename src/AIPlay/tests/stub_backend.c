//
// AI Play layer — unit test backend: stub engine with a controllable world.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_internal.h"
#include "stub_backend.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int aiplayTestsRun;
extern int aiplayTestsFailed;

void aiplayTestCheck(int cond, const char *name, const char *file, int line)
{
    aiplayTestsRun++;
    if (!cond)
    {
        aiplayTestsFailed++;
        fprintf(stderr, "FAIL %s (%s:%d)\n", name, file, line);
    }
}

// ------------------------------------------------------------------ stub world

#define STUB_MAX_SHIPS 64

typedef struct stubShip
{
    void   *handle;
    int32_t owner;
    bool    isResource;
    char    type[32];
    float   pos[3];
    float   vel[3];
    float   moveTo[3];
    bool    moving;
    float   health, healthMax;
    int32_t resValue;
} stubShip;

static stubShip ships[STUB_MAX_SHIPS];
static int      shipCount = 0;

static int      stubNumPlayers = 2;
static bool     stubAlive[]    = {true, true};
static int64_t  stubRUs[]      = {8000, 8000};
static bool     stubAI[]       = {true, false};
static bool     stubRunning    = false;
static bool     stubPaused     = false;
static bool     stubAllowPauseOrders;
static uint32_t stubTick       = 0;
static double   stubElapsed    = 0.0;
static int      stubNeutralized = -1;
static char     stubMap[64]    = "skirmish-test";
static int      stepsRequested = 0;

// last dispatched order, for assertions
static aiplayOrderRequest lastDispatch;
int        stubDispatchCount = 0;

void stubBackendReset(void)
{
    memset(ships, 0, sizeof(ships));
    shipCount = 0;
    stubNumPlayers = 2;
    stubAlive[0] = stubAlive[1] = true;
    stubRUs[0] = stubRUs[1] = 8000;
    stubAI[0] = true; stubAI[1] = false;
    stubRunning = false;
    stubPaused  = false;
    stubTick    = 0;
    stubElapsed = 0.0;
    stubNeutralized = -1;
    stepsRequested = 0;
    memset(&lastDispatch, 0, sizeof(lastDispatch));
    strcpy(stubMap, "skirmish-test");
}

aiplayOrderRequest *stubLastDispatch(void) { return &lastDispatch; }
int                 stubNeutrealizedIndex(void) { return stubNeutralized; }
void stubSetRunning(bool running)   { stubRunning = running; }
void stubSetPaused(bool paused)     { stubPaused = paused; }
void stubSetNumPlayers(int n)       { stubNumPlayers = n; }
void stubSetAlive(int i, bool a)    { stubAlive[i] = a; }
void stubSetAI(int i, bool ai)      { stubAI[i] = ai; }
void stubSetMap(const char *m)      { strncpy(stubMap, m, sizeof(stubMap) - 1); stubMap[sizeof(stubMap)-1] = 0; }
uint32_t stubAdvanceTick(void)      { stubTick += 1; stubElapsed += 0.0625; return stubTick; }
int      stubStepsRequested(void)   { return stepsRequested; }
int      stubShipCount(void)        { return shipCount; }

// register a controllable object; returns stable opaque handle
void *stubAddShip(const char *type, int owner, float x, float y, float z)
{
    stubShip *s = &ships[shipCount++];
    memset(s, 0, sizeof(*s));
    s->handle    = (void *)(long)(0x1000 + shipCount);
    s->owner     = owner;
    strncpy(s->type, type, sizeof(s->type) - 1);
    s->pos[0] = x; s->pos[1] = y; s->pos[2] = z;
    s->health = s->healthMax = 100.0f;
    return s->handle;
}

void *stubAddResource(int value, float x, float y, float z)
{
    stubShip *s = &ships[shipCount++];
    memset(s, 0, sizeof(*s));
    s->handle   = (void *)(long)(0x4000 + shipCount);
    s->owner    = -1;
    s->isResource = true;
    strcpy(s->type, "Asteroid");
    s->pos[0] = x; s->pos[1] = y; s->pos[2] = z;
    s->resValue = value;
    return s->handle;
}

void stubSetMoving(void *handle, float x, float y, float z)
{
    for (int i = 0; i < shipCount; i++)
    {
        if (ships[i].handle == handle)
        {
            ships[i].moving    = true;
            ships[i].moveTo[0] = x;
            ships[i].moveTo[1] = y;
            ships[i].moveTo[2] = z;
            ships[i].vel[0]    = 10.0f;
            return;
        }
    }
}

void stubKill(void *handle)
{
    for (int i = 0; i < shipCount; i++)
    {
        if (ships[i].handle == handle)
        {
            memmove(&ships[i], &ships[i + 1], sizeof(ships[0]) * (size_t)(shipCount - i - 1));
            shipCount--;
            return;
        }
    }
}

static bool sbShipInfo(void *opaque, aiplayShipInfo *out)
{
    for (int i = 0; i < shipCount; i++)
    {
        if (ships[i].handle == opaque)
        {
            memset(out, 0, sizeof(*out));
            out->ship         = opaque;
            out->owner        = ships[i].owner;
            out->isResource   = ships[i].isResource;
            out->typeName     = ships[i].type;
            memcpy(out->pos,    ships[i].pos,    sizeof(out->pos));
            memcpy(out->vel,    ships[i].vel,    sizeof(out->vel));

            out->moving       = ships[i].moving;
            out->orderVerb    = "move";     // stub: ships report their move order
            out->orderCoord[0] = ships[i].moveTo[0];
            out->orderCoord[1] = ships[i].moveTo[1];
            out->orderCoord[2] = ships[i].moveTo[2];
            out->health       = ships[i].health;
            out->healthMax    = ships[i].healthMax;
            out->resourceValue = ships[i].resValue;
            return true;
        }
    }
    return false;
}

static void sbSetPaused(bool paused)
{
    stubPaused = paused;
    // mirrors the engine semantics: paused sims accept queued orders via
    // opPauseOrders (research R-3)
    stubAllowPauseOrders = paused;
}

static bool sbStep(int ticks)
{
    if (!stubPaused)
    {
        return false;
    }
    stepsRequested += ticks;
    for (int i = 0; i < ticks; i++)
    {
        stubAdvanceTick();
    }
    return true;
}

// ------------------------------------------------------------------- backend

static aiplayError sbDispatch(const aiplayOrderRequest *o)
{
    static const aiplayOrderRequest *store = NULL;
    store = o;
    lastDispatch = *o;
    stubDispatchCount++;
    return aiplayError_none;
}

static int sbNumPlayers(void)                { return stubNumPlayers; }
static bool sbPlayerAlive(int i)             { return i >= 0 && i < 8 ? stubAlive[i] : false; }
static bool sbPlayerIsAI(int i)              { return i >= 0 && i < 8 ? stubAI[i] : false; }
static int64_t sbResourceUnits(int i)        { return i >= 0 && i < 8 ? stubRUs[i] : 0; }
static const char *sbMapName(void)           { return stubMap; }
static bool sbGameRunning(void)              { return stubRunning; }
static uint32_t sbTick(void)                 { return stubTick; }
static double sbTimeElapsed(void)            { return stubElapsed; }
static bool sbIsPaused(void)                 { return stubPaused; }
static void sbSetNeutralized(int i, bool n)  { stubNeutralized = n ? i : -1; }

static void stTraceLog(const char *line)
{
    printf("STUB-LOG: %s\n", line);
}

static const aiplayEngineBackend stubBackend = {
    sbNumPlayers, sbPlayerAlive, sbPlayerIsAI, sbResourceUnits, sbMapName,
    sbGameRunning, sbTick, sbTimeElapsed, sbIsPaused, sbSetNeutralized, sbDispatch,
    sbShipInfo, sbSetPaused, sbStep,
    stTraceLog,
};

void testSyncShipsHook(void)
{
    for (int i = 0; i < shipCount; i++)
    {
        if (ships[i].isResource)
        {
            aiplayIdTouchEx(ships[i].handle, ships[i].owner, true);
        }
        else
        {
            aiplayIdTouch(ships[i].handle, ships[i].owner);
        }
    }
}

const aiplayEngineBackend *stubBackendGet(void)
{
    return &stubBackend;
}