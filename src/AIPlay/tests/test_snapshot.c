//
// AI Play layer unit tests: battlefield snapshots + time control (US2/US4).
// SPDX-License-Identifier: GPL-2.0-or-later
#include "../aiplay_internal.h"
#include "stub_backend.h"

#include <stdio.h>
#include <string.h>

#define CHECK(c) aiplayTestCheck((c), #c, __FILE__, __LINE__)

static void handleLine(const char *line, aiplayJsonBuf *resp)
{
    resp->len = 0;
    resp->data[0] = '\0';
    aiplayHandleLine(line, resp);
}

static bool contains(const aiplayJsonBuf *buf, const char *needle)
{
    return strstr(buf->data, needle) != NULL;
}

static void bindPlayer0(aiplayJsonBuf *resp)
{
    handleLine("{\"id\":1,\"method\":\"aiSession.bind\",\"params\":{\"playerIndex\":0}}", resp);
    CHECK(contains(resp, "\"playerIndex\":0"));
    CHECK(aiplayBoundPlayer() == 0);
}

// ---------------------------------------------------------------- US2 snapshots

// ---------------------------------------------------------------- US2 full pass

static void testSnapshotContents(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());
    aiplayTickBegin();

    char out[16384];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));
    bindPlayer0(&resp);

    void *a = stubAddShip("Interceptor", 0, 10, 0, 20);
    stubAddShip("HeavyCruiser", 1, 40, 0, 40);
    stubAddResource(1500, 5, 3, 8);
    stubSetMoving(a, 11, 0, 21);

    // emulate a tick: begin (marks clear) -> engine sync -> end (evict)
    aiplayTickBegin();
    testSyncShipsHook();
    aiplayTickEnd();

    handleLine("{\"id\":2,\"method\":\"state.snapshot\"}", &resp);
    CHECK(contains(&resp, "\"Interceptor\""));
    CHECK(contains(&resp, "\"HeavyCruiser\""));
    CHECK(contains(&resp, "\"Asteroid\""));
    CHECK(contains(&resp, "\"resourceUnits\":8000"));
    CHECK(contains(&resp, "\"order\":{\"verb\":\"move\",\"target\":"));
    CHECK(contains(&resp, "\"owner\":1"));        // full battlefield: enemy visible

    // filters: sections+owner
    handleLine("{\"id\":3,\"method\":\"state.snapshot\",\"params\":{\"sections\":[\"ships\"],\"owner\":0}}", &resp);
    CHECK(contains(&resp, "\"Interceptor\""));
    CHECK(!contains(&resp, "\"Asteroid\""));
    CHECK(!contains(&resp, "\"HeavyCruiser\""));

    // paused determinism: two snapshots byte-identical
    stubSetPaused(true);
    handleLine("{\"id\":4,\"method\":\"state.snapshot\"}", &resp);
    char snap1[16384];
    strncpy(snap1, resp.data, sizeof(snap1) - 1);
    snap1[sizeof(snap1) - 1] = '\0';
    handleLine("{\"id\":4,\"method\":\"state.snapshot\"}", &resp);
    CHECK(strcmp(snap1, resp.data) == 0);
    aiplayShutdown();
}

// ---------------------------------------------------------------- US2 events

static void testShipDestroyedEvent(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());
    aiplayTickBegin();

    char out[8192];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));
    bindPlayer0(&resp);

    void *victim = stubAddShip("Interceptor", 0, 1, 2, 3);
    aiplayTickBegin();
    testSyncShipsHook();
    aiplayTickEnd();

    stubKill(victim);                 // engine removed the ship
    aiplayTickBegin();
    testSyncShipsHook();
    aiplayTickEnd();                  // eviction emits shipDestroyed

    handleLine("{\"id\":2,\"method\":\"state.events\"}", &resp);
    CHECK(contains(&resp, "shipDestroyed"));
    CHECK(contains(&resp, "\"owner\":0"));
    aiplayShutdown();
}

// ---------------------------------------------------------------- US4 time

static void testPauseAndStep(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());
    aiplayTickBegin();

    char out[1024];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));

    handleLine("{\"id\":1,\"method\":\"time.pause\"}", &resp);
    CHECK(contains(&resp, "\"paused\":true"));
    handleLine("{\"id\":2,\"method\":\"aiSession.status\"}", &resp);
    CHECK(contains(&resp, "\"paused\":true"));

    handleLine("{\"id\":3,\"method\":\"time.step\",\"params\":{\"ticks\":3}}", &resp);
    CHECK(contains(&resp, "\"stepped\":3"));
    CHECK(contains(&resp, "\"tick\":3"));
    CHECK(stubStepsRequested() == 3);

    handleLine("{\"id\":4,\"method\":\"time.resume\"}", &resp);
    CHECK(contains(&resp, "\"paused\":false"));

    // stepping while running must be refused
    handleLine("{\"id\":5,\"method\":\"time.step\",\"params\":{\"ticks\":1}}", &resp);
    CHECK(contains(&resp, "E_BAD_REQUEST"));
    aiplayShutdown();
}

void testSnapshotAll(void)
{
    testSnapshotContents();
    testShipDestroyedEvent();
    testPauseAndStep();

}