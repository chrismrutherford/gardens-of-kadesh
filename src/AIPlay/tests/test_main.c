//
// AI Play layer unit tests — entry point.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "../aiplay_internal.h"

#include <stdio.h>

int  aiplayTestsRun    = 0;
int  aiplayTestsFailed = 0;
void aiplayTestCheck(int cond, const char *name, const char *file, int line);

extern void testJsonAll(void);
extern void testSessionAll(void);
extern void testTransportAll(void);

int main(void)
{
    testJsonAll();
    testSessionAll();
    testTransportAll();

    printf("aiplay-tests: %d checks, %d failures\n", aiplayTestsRun, aiplayTestsFailed);
    return aiplayTestsFailed == 0 ? 0 : 1;
}