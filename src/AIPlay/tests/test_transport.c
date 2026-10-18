//
// AI Play layer unit tests: transport NDJSON framing logic.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "../aiplay_internal.h"

#include <stdio.h>
#include <string.h>

#include "stub_backend.h"

#define CHECK(c) aiplayTestCheck((c), #c, __FILE__, __LINE__)

// The engine transport (aiplay_transport.c without AIPLAY_UNIT_TEST) consumes
// complete '\n'-delimited lines; here we verify the core's line-handler behavior
// across concatenated requests and that responses are newline terminated.
static void testLineFraming(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());

    char    out[8192];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));

    // two requests in one call stream (simulates a buffered frame split)
    aiplayHandleLine("{\"id\":1,\"method\":\"aiSession.status\"}", &resp);
    aiplayHandleLine("{\"id\":2,\"method\":\"state.snapshot\"}", &resp);
    CHECK(resp.len > 2 && resp.data[resp.len - 1] == '\n');
    // exactly two newline-delimited response objects
    int newlines = 0;
    for (size_t i = 0; i < resp.len; i++)
    {
        if (resp.data[i] == '\n')
        {
            newlines++;
        }
    }
    CHECK(newlines == 2);

    // malformed line -> structured error, still newline-terminated
    resp.len = 0; resp.data[0] = 0;
    aiplayHandleLine("{ b o r k e n", &resp);
    CHECK(strstr(resp.data, "E_BAD_REQUEST") != NULL);
    CHECK(resp.data[resp.len - 1] == '\n');

    aiplayShutdown();
}

static void testDisabledLayer(void)
{
    stubBackendReset();
    aiplayInit(false, NULL, stubBackendGet());
    char    out[512];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));
    aiplayHandleLine("{\"id\":1,\"method\":\"aiSession.status\"}", &resp);
    CHECK(strstr(resp.data, "E_NO_SESSION") != NULL);
    // tick hooks are safe no-ops when disabled
    aiplayTickBegin();
    aiplayTickEnd();
    aiplayShutdown();
}

void testTransportAll(void)
{
    testLineFraming();
    testDisabledLayer();
}