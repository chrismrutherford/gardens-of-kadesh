//
// Stub backend declarations shared by the AI-play unit tests.
// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef AIPLAY_TEST_STUB_H
#define AIPLAY_TEST_STUB_H

#include "../aiplay_internal.h"

void aiplayTestCheck(int cond, const char *name, const char *file, int line);

void                      stubBackendReset(void);
const aiplayEngineBackend *stubBackendGet(void);
aiplayOrderRequest *      stubLastDispatch(void);
extern int      stubDispatchCount;
void            stubSetRunning(bool running);
void            stubSetPaused(bool paused);
void            stubSetNumPlayers(int n);
void            stubSetAlive(int i, bool a);
void            stubSetAI(int i, bool ai);
void            stubSetMap(const char *m);
uint32_t        stubAdvanceTick(void);
int             stubNeutralizedIndex(void);
void            testSyncShipsHook(void);   // emulate engine tick-end ship sync
void *          stubAddShip(const char *type, int owner, float x, float y, float z);
void *          stubAddResource(int value, float x, float y, float z);
void            stubSetMoving(void *handle, float x, float y, float z);
void            stubKill(void *handle);

#endif // AIPLAY_TEST_STUB_H