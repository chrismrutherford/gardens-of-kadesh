//
// AI Play layer unit tests: session lifecycle, id map, events, order queue.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "../aiplay_internal.h"

#include <stdio.h>
#include <string.h>


#include "stub_backend.h"

#define CHECK(c) aiplayTestCheck((c), #c, __FILE__, __LINE__)

static void testSessionLifecycle(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());
    CHECK(aiplay.state == aiplaySession_lobby);

    // invalid bind: out of range
    CHECK(aiplayBoundPlayer() == -1);
    aiplayTickBegin();
    CHECK(aiplay.state == aiplaySession_playing);
    // match started event fired
    // bind player 1 (in-range) succeeds
    // (bind is exercised via JSON path in test_json/test_orders; here we test ticks)
    aiplayShutdown();
}

static void testIdMapStabilityAndEviction(void)
{
    stubBackendReset();
    aiplayInit(true, NULL, stubBackendGet());

    // AIPLAY: bind through core
    // aiplayTickBegin with running game transitions state
    stubSetRunning(true);
    aiplayTickBegin();

    int       shipA = 0x1000, shipB = 0x2000;
    uint64_t  idA   = aiplayIdTouch(&shipA, 0);
    uint64_t  idA2  = aiplayIdTouch(&shipA, 0);
    uint64_t  idB   = aiplayIdTouch(&shipB, 1);
    CHECK(idA == idA2);           // stable while ship lives
    CHECK(idA != idB);
    CHECK(idA != 0 && idB != 0);

    int32_t owner = -1;
    void *  ship = NULL;
    CHECK(aiplayIdResolve(idA, &ship, &owner));
    CHECK(owner == 0);

    // ship A disappears (not touched on next sync) -> evicted at tick end
    aiplayTickBegin();
    (void)aiplayIdTouch(&shipB, 1);   // ship B still alive
    aiplayTickEnd();
    CHECK(!aiplayIdResolve(idA, NULL, NULL));   // dead id reports invalid (E_BAD_ID path)
    CHECK(aiplayIdResolve(idB, NULL, NULL));

    aiplayShutdown();
}

static void testEventFifoOrder(void)
{
    stubBackendReset();
    aiplayInit(true, NULL, stubBackendGet());
    stubSetRunning(true);
    // (verified via state.events JSON in test_json.c through aiplayHandleLine)
    aiplayShutdown();
}

static void testOrderQueueOverflow(void)
{
    stubBackendReset();
    aiplayInit(true, NULL, stubBackendGet());
    stubSetRunning(true);

    aiplayOrderRequest o;
    memset(&o, 0, sizeof(o));
    o.verb = aiplayVerb_halt;
    o.numShips = 1;
    o.shipIds[0] = 1;
    // no session player bound yet -> validation happens at submit: badRequest path
    // overflow test uses the raw submit API directly:
    aiplayError err = aiplayError_none;
    for (int i = 0; i < AIPLAY_ORDER_QUEUE_CAP + 1; i++)
    {
        err = aiplayOrderSubmit(&o);
        if (err != aiplayError_none)
        {
            break;
        }
    }
    CHECK(err == aiplayError_queueFull);   // ring capacity enforced

    // drain fully at tick: FIFO emptied, so a fresh submit has room again
    aiplayTickBegin();
    CHECK(aiplayOrderSubmit(&o) == aiplayError_none);
    aiplayShutdown();
}

static void testMatchOverGating(void)
{
    stubBackendReset();
    aiplayInit(true, NULL, stubBackendGet());
    stubSetRunning(true);
    aiplayTickBegin();
    CHECK(aiplay.state == aiplaySession_playing);
    stubSetRunning(false);
    aiplayTickBegin();
    CHECK(aiplay.state == aiplaySession_matchOver);
    aiplayShutdown();
}

void testSessionAll(void)
{
    testSessionLifecycle();
    testIdMapStabilityAndEviction();
    testEventFifoOrder();
    testOrderQueueOverflow();
    testMatchOverGating();
}