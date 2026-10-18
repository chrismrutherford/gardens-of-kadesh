//
// AI Play layer — engine-side hook declarations (included by main.c,
// utility.c and UnivUpdate.c only; never by the pure core or unit tests).
//
// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef AIPLAY_ENGINE_HOOKS_H
#define AIPLAY_ENGINE_HOOKS_H

#include "Types.h"

extern bool32 aiplayRequested;   // set by the /aiPlay command-line option

void aiplayEngineInit(void);     // call once after utyGameSystemsInit succeeded
bool32 aiplayCmdlineSave(char *string);    // /aiPlayLoad <name> handler (COF_NextToken)
bool32 aiplayCmdlineSaveMP(char *string);  // /aiPlayLoadMP <name> handler (COF_NextToken)
void aiplayEngineShutdown(void); // call inside utyGameSystemsShutdown
void aiplayEngineSyncShips(void);// call at the end of univUpdate, before aiplayTickEnd
void aiplayEngineDrawHUD(void);  // call each render frame (rndDrawOnScreenDebugInfo)

// core hooks (see aiplay.h)
void aiplayFramePoll(void);
void aiplayTickBegin(void);
void aiplayTickEnd(void);

#endif // AIPLAY_ENGINE_HOOKS_H