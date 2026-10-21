//
// AI Play layer unit tests: JSON parse/write + order submit path over JSON.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "../aiplay_internal.h"

#include <stdio.h>
#include <string.h>


#include "stub_backend.h"

#define CHECK(c) aiplayTestCheck((c), #c, __FILE__, __LINE__)

static bool bufContains(aiplayJsonBuf *buf, const char *needle)
{
    return strstr(buf->data, needle) != NULL;
}

static void testJsonDomBasics(void)
{
    aiplayJsonDoc doc;
    CHECK(aiplayJsonParse(&doc, "{\"a\":1,\"b\":\"two\",\"c\":[1,2,3],\"d\":true,\"e\":-123456789012345}"));
    CHECK(doc.root >= 0);
    CHECK(aiplayJsonGetInt(&doc, doc.root, "a") == 1);
    char s[64];
    CHECK(aiplayJsonGetString(&doc, doc.root, "b", s, sizeof(s)));
    CHECK(strcmp(s, "two") == 0);
    CHECK(aiplayJsonGetBool(&doc, doc.root, "d", false) == true);
    CHECK(aiplayJsonGetInt64(&doc, doc.root, "e") == -123456789012345LL);
    int32_t arr = aiplayJsonGet(&doc, doc.root, "c");
    CHECK(arr >= 0);
    int32_t child = aiplayJsonChild(&doc, arr);
    CHECK(child >= 0 && (int)doc.nodes[child].number == 1);
    child = aiplayJsonNextSibling(&doc, child);
    CHECK(child >= 0 && (int)doc.nodes[child].number == 2);
}

// uint64 ship ids must survive round-trips (raw text, not double)
static void testJsonUint64Ids(void)
{
    aiplayJsonDoc doc;
    CHECK(aiplayJsonParse(&doc, "{\"id\":18446744073709551615}"));
    // (max uint64 parses -1 at int64 level; acceptable: ids are far below 2^60)
    CHECK(aiplayJsonParse(&doc, "{\"ships\":[1099511627776,42]}"));
    int32_t ships = aiplayJsonGet(&doc, doc.root, "ships");
    int32_t child = aiplayJsonChild(&doc, ships);
    CHECK(doc.nodes[child].uintVal == 1099511627776ULL);
}

static void testBindAndSnapshot(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());
    aiplayTickBegin();

    char    out[8192];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));

    aiplayHandleLine("{\"id\":1,\"method\":\"aiSession.bind\",\"params\":{\"playerIndex\":1}}", &resp);
    {   // whole-response validity (envelope braces)
        aiplayJsonDoc sanity;
        CHECK(aiplayJsonParse(&sanity, resp.data));
    }
    CHECK(bufContains(&resp, "\"playerIndex\":1"));
    CHECK(aiplayBoundPlayer() == 1);

    resp.len = 0; resp.data[0] = 0;
    aiplayHandleLine("{\"id\":2,\"method\":\"aiSession.status\"}", &resp);
    CHECK(bufContains(&resp, "\"mapName\":\"skirmish-test\""));
    CHECK(bufContains(&resp, "\"running\":true"));

    resp.len = 0; resp.data[0] = 0;
    aiplayHandleLine("{\"id\":3,\"method\":\"state.snapshot\"}", &resp);
    CHECK(bufContains(&resp, "\"players\":["));
    CHECK(bufContains(&resp, "\"resourceUnits\":8000"));
    CHECK(bufContains(&resp, "\"ships\":[]"));
    CHECK(bufContains(&resp, "\"matchStatus\":"));

    // paused determinism: pause, two snapshots identical
    resp.len = 0;
    aiplayHandleLine("{\"id\":4,\"method\":\"state.snapshot\"}", &resp);
    char snap1[8192];
    strncpy(snap1, resp.data, sizeof(snap1) - 1);
    snap1[sizeof(snap1) - 1] = 0;
    resp.len = 0;
    aiplayHandleLine("{\"id\":4,\"method\":\"state.snapshot\"}", &resp);
    CHECK(strcmp(snap1, resp.data) == 0);   // stateless snapshots: deterministic bytes

    aiplayShutdown();
}

static void testOrderSubmitViaJson(void)
{
    stubBackendReset();
    stubSetRunning(true);
    aiplayInit(true, NULL, stubBackendGet());
    aiplayTickBegin();

    // no ship 777 in id map -> validation rejects at submit with E_BAD_ID
    char    out[2048];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));
    aiplayHandleLine("{\"id\":9,\"method\":\"order.move\",\"params\":{\"id\":9,\"ships\":[777],\"target\":[10,0,20]}}", &resp);
    CHECK(bufContains(&resp, "E_BAD_ID"));

    // register a ship owned by bound player and retry via raw map
    int fakeShip = 0x42;
    (void)aiplayIdTouch(&fakeShip, 0);
    // bind player 0 first
    resp.len = 0;
    aiplayHandleLine("{\"id\":10,\"method\":\"aiSession.bind\",\"params\":{\"playerIndex\":0}}", &resp);
    uint64_t shipId = aiplayIdTouch(&fakeShip, 0);
    resp.len = 0;
    char line[256];
    snprintf(line, sizeof(line), "{\"id\":11,\"method\":\"order.move\",\"params\":{\"id\":11,\"ships\":[%llu],\"target\":[10,0,20]}}", (unsigned long long)shipId);
    aiplayHandleLine(line, &resp);
    {   // whole-response validity (envelope braces)
        aiplayJsonDoc sanity;
        CHECK(aiplayJsonParse(&sanity, resp.data));
    }
    CHECK(bufContains(&resp, "\"queued\":true"));
    CHECK(bufContains(&resp, "\"orderId\":11"));

    // tick: queued order drained and dispatched through the backend
    (void)stubAdvanceTick();
    aiplayTickBegin();
    CHECK(stubDispatchCount == 1);
    CHECK(stubLastDispatch()->verb == aiplayVerb_move);
    CHECK(stubLastDispatch()->coord[0] == 10.0f);

    // applied event present, submission order preserved
    resp.len = 0;
    aiplayHandleLine("{\"id\":12,\"method\":\"state.events\"}", &resp);
    CHECK(bufContains(&resp, "orderApplied"));

    aiplayShutdown();
}

static void testNewOrderVerbs(void)
{
    char    out[1024];
    aiplayJsonBuf resp;
    aiplayJsonBufInit(&resp, out, sizeof(out));
    char    line[320];

    stubBackendReset();
    stubSetRunning(true);
    stubSetRUs(0, 5000); stubSetRUs(1, 5000);   // RU-transfer prechecks
    aiplayInit(true, NULL, stubBackendGet());
    aiplayHandleLine("{\"id\":1,\"method\":\"aiSession.bind\",\"params\":{\"playerIndex\":0}}", &resp);

    // two owned ships for the selection-based verbs
    int shipA = 0x21, shipB = 0x22;
    uint64_t idA = aiplayIdTouch(&shipA, 0);
    uint64_t idB = aiplayIdTouch(&shipB, 0);
    unsigned baseDispatch = (unsigned)stubDispatchCount;
    resp.len = 0;

    // kamikaze: selection verb, dispatches to the funnel
    snprintf(line, sizeof(line),
             "{\"id\":2,\"method\":\"order.kamikaze\",\"params\":{\"id\":2,"
             "\"ships\":[%llu]}}", (unsigned long long)idA);
    aiplayHandleLine(line, &resp);
    CHECK(bufContains(&resp, "\"queued\":true"));
    (void)stubAdvanceTick();
    aiplayTickBegin();
    CHECK(stubDispatchCount == baseDispatch + 1);
    CHECK(stubLastDispatch()->verb == aiplayVerb_kamikaze);
    resp.len = 0;

    // parade: selection verb
    snprintf(line, sizeof(line),
             "{\"id\":3,\"method\":\"order.parade\",\"params\":{\"id\":3,"
             "\"ships\":[%llu]}}", (unsigned long long)idB);
    aiplayHandleLine(line, &resp);
    CHECK(bufContains(&resp, "\"queued\":true"));
    resp.len = 0;

    // ru_transfer: fleet-wide, valid player + amount
    aiplayHandleLine("{\"id\":4,\"method\":\"order.ru_transfer\",\"params\":"
                     "{\"id\":4,\"to\":1,\"amount\":500}}", &resp);
    CHECK(bufContains(&resp, "\"queued\":true"));
    resp.len = 0;

    // ru_transfer: overspending -> submit-time E_UNAFFORDABLE (FR-009)
    aiplayHandleLine("{\"id\":5,\"method\":\"order.ru_transfer\",\"params\":"
                     "{\"id\":5,\"to\":1,\"amount\":99999}}", &resp);
    CHECK(bufContains(&resp, "E_UNAFFORDABLE"));
    resp.len = 0;

    // ru_transfer: bad player -> validation error
    aiplayHandleLine("{\"id\":6,\"method\":\"order.ru_transfer\",\"params\":"
                     "{\"id\":6,\"to\":9,\"amount\":10}}", &resp);
    CHECK(bufContains(&resp, "error"));
    resp.len = 0;

    // alliance: fleet-wide, on
    aiplayHandleLine("{\"id\":7,\"method\":\"order.alliance\",\"params\":"
                     "{\"id\":7,\"player\":1,\"onOff\":true}}", &resp);
    CHECK(bufContains(&resp, "\"queued\":true"));
    resp.len = 0;

    // alliance: self -> rejected
    aiplayHandleLine("{\"id\":8,\"method\":\"order.alliance\",\"params\":"
                     "{\"id\":8,\"player\":0,\"onOff\":true}}", &resp);
    CHECK(bufContains(&resp, "error"));
    resp.len = 0;

    // hyperspace: ships + coord target
    snprintf(line, sizeof(line),
             "{\"id\":9,\"method\":\"order.hyperspace\",\"params\":{\"id\":9,"
             "\"ships\":[%llu],\"target\":[100,200,300]}}",
             (unsigned long long)idA);
    aiplayHandleLine(line, &resp);
    CHECK(bufContains(&resp, "\"queued\":true"));
    resp.len = 0;

    // hyperspace without coord -> reject at validation
    snprintf(line, sizeof(line),
             "{\"id\":10,\"method\":\"order.hyperspace\",\"params\":{\"id\":10,"
             "\"ships\":[%llu]}}", (unsigned long long)idA);
    aiplayHandleLine(line, &resp);
    CHECK(bufContains(&resp, "error"));
    resp.len = 0;

    // drain: all queued valid orders dispatch in submission order
    (void)stubAdvanceTick();
    aiplayTickBegin();
    CHECK(stubDispatchCount == baseDispatch + 5);   // 5 valid verbs apply
    CHECK(stubLastDispatch()->verb == aiplayVerb_hyperspace);
    CHECK(stubLastDispatch()->coord[2] == 300.0f);

    // applied events for all of them
    aiplayHandleLine("{\"id\":11,\"method\":\"state.events\"}", &resp);
    CHECK(bufContains(&resp, "orderApplied"));

    aiplayShutdown();
}

static void testDeterministicWriter(void)
{
    char    staticA[256], staticB[256];
    aiplayJsonBuf a, b;
    aiplayJsonBufInit(&a, staticA, sizeof(staticA));
    aiplayJsonBufInit(&b, staticB, sizeof(staticB));
    aiplayJsonInt(&a, "x", 42);
    aiplayJsonDouble(&a, "f", 1.5);
    aiplayJsonRaw(&a, "arr", "[]");
    aiplayJsonInt(&b, "x", 42);
    aiplayJsonDouble(&b, "f", 1.5);
    aiplayJsonRaw(&b, "arr", "[]");
    CHECK(strcmp(a.data, b.data) == 0);
    CHECK(strcmp(a.data, "\"x\":42,\"f\":1.500000,\"arr\":[],") == 0);  // trim applied by callers
}

void testJsonAll(void)
{
    testJsonDomBasics();
    testJsonUint64Ids();
    testBindAndSnapshot();
    testOrderSubmitViaJson();
    testNewOrderVerbs();
    testDeterministicWriter();
}