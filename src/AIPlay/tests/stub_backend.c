//
// AI Play layer — unit test support: stub engine backend + tiny test runner.
// Compiled with -DAIPLAY_UNIT_TEST; core never includes engine headers.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_internal.h"

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

// ------------------------------------------------------------------ stub backend

static int      stubNumPlayers = 2;
static bool     stubAlive[]    = {true, true};
static int64_t  stubRUs[]      = {8000, 8000};
static bool     stubAI[]       = {true, false};
static bool     stubRunning    = false;
static bool     stubPaused     = false;
static uint32_t stubTick       = 0;
static double   stubElapsed    = 0.0;
static int      stubNeutralized = -1;
static char     stubMap[64]    = "skirmish-test";

// last dispatched order, for assertions
static aiplayOrderRequest lastDispatch;

void stubBackendReset(void)
{
    stubNumPlayers = 2;
    stubAlive[0] = stubAlive[1] = true;
    stubRUs[0] = stubRUs[1] = 8000;
    stubAI[0] = true; stubAI[1] = false;
    stubRunning = false;
    stubPaused  = false;
    stubTick    = 0;
    stubElapsed = 0.0;
    stubNeutralized = -1;
    memset(&lastDispatch, 0, sizeof(lastDispatch));
    strcpy(stubMap, "skirmish-test");
}

aiplayOrderRequest *stubLastDispatch(void)
{
    return &lastDispatch;
}

int stubDispatchCount = 0;
void stubSetRunning(bool running)   { stubRunning = running; }
void stubSetPaused(bool paused)     { stubPaused = paused; }
void stubSetNumPlayers(int n)       { stubNumPlayers = n; }
void stubSetAlive(int i, bool a)    { stubAlive[i] = a; }
void stubSetAI(int i, bool ai)      { stubAI[i] = ai; }
void stubSetMap(const char *m)      { strncpy(stubMap, m, sizeof(stubMap) - 1); stubMap[sizeof(stubMap)-1] = 0; }
uint32_t stubAdvanceTick(void)      { stubTick += 1; stubElapsed += 0.0625; return stubTick; }

static int sbNumPlayers(void)               { return stubNumPlayers; }
static bool sbPlayerAlive(int i)            { return i >= 0 && i < 8 ? stubAlive[i] : false; }
static bool sbPlayerIsAI(int i)             { return i >= 0 && i < 8 ? stubAI[i] : false; }
static int64_t sbResourceUnits(int i)       { return i >= 0 && i < 8 ? stubRUs[i] : 0; }
static const char *sbMapName(void)          { return stubMap; }
static bool sbGameRunning(void)             { return stubRunning; }
static uint32_t sbTick(void)                { return stubTick; }
static double sbTimeElapsed(void)           { return stubElapsed; }
static bool sbIsPaused(void)                { return stubPaused; }
static void sbSetNeutralized(int i, bool n) { stubNeutralized = n ? i : -1; }
static aiplayError sbDispatch(const aiplayOrderRequest *o)
{
    lastDispatch = *o;
    stubDispatchCount++;
    return aiplayError_none;
}

static const aiplayEngineBackend stubBackend = {
    sbNumPlayers, sbPlayerAlive, sbPlayerIsAI, sbResourceUnits, sbMapName,
    sbGameRunning, sbTick, sbTimeElapsed, sbIsPaused, sbSetNeutralized, sbDispatch, NULL,
};

const aiplayEngineBackend *stubBackendGet(void)
{
    return &stubBackend;
}

int stubNeutrealizedIndex(void)
{
    return stubNeutralized;
}