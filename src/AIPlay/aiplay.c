//
// AI Play layer — session core (pure C, no engine includes).
// The engine build supplies the backend (aiplay_engine.c); unit tests supply a
// stub backend. All transport I/O lives in aiplay_transport.c; all order
// validation/dispatch lives in aiplay_orders.c.
//
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

aiplaySession aiplay = {
    .enabled       = false,
    .state         = aiplaySession_disabled,
    .boundPlayer   = -1,
    .aiNeutralized = false,
    .lastTick      = 0,
    .nextOrderId   = 1,
    .nextShipId    = 1,
};

static const aiplayEngineBackend *backend = NULL;

const char *aiplayErrorString(aiplayError e)
{
    switch (e)
    {
    case aiplayError_noSession:    return "E_NO_SESSION";
    case aiplayError_badId:        return "E_BAD_ID";
    case aiplayError_unaffordable: return "E_UNAFFORDABLE";
    case aiplayError_noBuilder:    return "E_NO_BUILDER";
    case aiplayError_matchOver:    return "E_MATCH_OVER";
    case aiplayError_queueFull:    return "E_QUEUE_FULL";
    case aiplayError_badRequest:   return "E_BAD_REQUEST";
    case aiplayError_internal:     return "E_INTERNAL";
    case aiplayError_none:         return "OK";
    default:                       return "E_INTERNAL";
    }
}

const aiplayEngineBackend *aiplayBackendGet(void)
{
    return backend;
}

bool aiplaySessionReady(void)
{
    return aiplay.enabled && backend != NULL;
}

int32_t aiplayBoundPlayer(void)
{
    return aiplay.boundPlayer;
}

bool aiplayMatchRunning(void)
{
    return backend != NULL && backend->gameRunning();
}

bool aiplayIsPaused(void)
{
    return backend != NULL && backend->isPaused();
}

void aiplaySetPaused(bool paused)
{
    if (backend == NULL)
    {
        return;
    }
    backend->setPaused(paused);
}

bool aiplayStep(int ticks)
{
    if (backend == NULL || !aiplayIsPaused())
    {
        return false;   // stepping only from a paused state (FR-011)
    }
    for (int i = 0; i < ticks; i++)
    {
        if (!backend->step(1))
        {
            return false;
        }
    }
    return true;
}

// ------------------------------------------------------------- id map

typedef struct aiplayIdEntry
{
    void   *ship;
    uint64_t id;
    int32_t  owner;
    bool     isResource;
    bool     seen;
    bool     used;
} aiplayIdEntry;

static aiplayIdEntry idMap[AIPLAY_ID_MAP_CAP];

void aiplayIdMapReset(void)
{
    memset(idMap, 0, sizeof(idMap));
    aiplay.nextShipId = 1;
}

static aiplayIdEntry *idFindByShip(void *ship)
{
    if (ship == NULL)
    {
        return NULL;
    }
    for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
    {
        if (idMap[i].used && idMap[i].ship == ship)
        {
            return &idMap[i];
        }
    }
    return NULL;
}

uint64_t aiplayIdTouchEx(void *ship, int owner, bool isResource)
{
    aiplayIdEntry *e = idFindByShip(ship);
    if (e == NULL)
    {
        for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
        {
            if (!idMap[i].used)
            {
                e             = &idMap[i];
                e->used       = true;
                e->ship       = ship;
                e->id         = aiplay.nextShipId++;
                e->owner      = owner;
                e->isResource = isResource;
                break;
            }
        }
        if (e == NULL)
        {
            return 0;  // map exhausted
        }
    }
    e->seen       = true;
    e->owner      = owner;
    e->isResource = isResource;
    return e->id;
}

uint64_t aiplayIdTouch(void *ship, int owner)
{
    return aiplayIdTouchEx(ship, owner, false);
}

static bool idEntryIsResource(void *ship)
{
    aiplayIdEntry *e = idFindByShip(ship);
    return e != NULL && e->isResource;
}

bool aiplayIdEach(bool (*cb)(const aiplayIdIterItem *item, void *ctx), void *ctx)
{
    for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
    {
        if (!idMap[i].used)
        {
            continue;
        }
        aiplayIdIterItem item = {
            .ship       = idMap[i].ship,
            .id         = idMap[i].id,
            .owner      = idMap[i].owner,
            .isResource = idMap[i].isResource,
        };
        if (!cb(&item, ctx))
        {
            return false;
        }
    }
    return true;
}

bool aiplayIdIsResource(uint64_t id)
{
    for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
    {
        if (idMap[i].used && idMap[i].id == id)
        {
            return idMap[i].isResource;
        }
    }
    return false;
}

bool aiplayIdResolve(uint64_t id, void **ship, int32_t *owner)
{
    if (id == 0)
    {
        return false;
    }
    for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
    {
        if (idMap[i].used && idMap[i].id == id)
        {
            if (ship != NULL)
            {
                *ship = idMap[i].ship;
            }
            if (owner != NULL)
            {
                *owner = idMap[i].owner;
            }
            return true;
        }
    }
    return false;
}

void aiplayIdEvictUnseen(void)
{
    for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
    {
        if (idMap[i].used && !idMap[i].seen)
        {
            if (!idMap[i].isResource)   // ship deaths are reported (FR-003)
            {
                aiplayEventPush(aiplayEvent_shipDestroyed, idMap[i].id, 0, idMap[i].owner);
            }
            idMap[i].used = false;
            idMap[i].ship = NULL;
        }
    }
}

void aiplayNotifyShipDestroyed(void *shipOpaque, int killerOwner)
{
    aiplayIdEntry *e = idFindByShip(shipOpaque);
    if (e != NULL)
    {
        aiplayEventPush(aiplayEvent_shipDestroyed, e->id, (uint64_t)(killerOwner + 1), e->owner);
    }
}

static void marksClear(void)
{
    for (int i = 0; i < AIPLAY_ID_MAP_CAP; i++)
    {
        idMap[i].seen = false;
    }
}

// -------------------------------------------------------- event log

typedef struct aiplayEventLog
{
    aiplayEvent entries[AIPLAY_EVENT_LOG_CAP];
    size_t      head;
    size_t      count;
} aiplayEventLog;

static aiplayEventLog eventLog;

void aiplayEventPush(aiplayEventType type, uint64_t id, uint64_t aux, int32_t owner)
{
    if (eventLog.count >= AIPLAY_EVENT_LOG_CAP)
    {
        eventLog.head = (eventLog.head + 1) % AIPLAY_EVENT_LOG_CAP;
        eventLog.count--;
    }
    aiplayEvent *slot =
        &eventLog.entries[(eventLog.head + eventLog.count) % AIPLAY_EVENT_LOG_CAP];
    slot->type  = type;
    slot->tick  = aiplay.lastTick;
    slot->id    = id;
    slot->aux   = aux;
    slot->owner = owner;
    eventLog.count++;
}

static aiplayEventType eventPending;

static const char *eventTypeName(aiplayEventType t)
{
    switch (t)
    {
    case aiplayEvent_matchStarted:   return "matchStarted";
    case aiplayEvent_matchOver:      return "matchOver";
    case aiplayEvent_orderApplied:   return "orderApplied";
    case aiplayEvent_orderRejected:  return "orderRejected";
    case aiplayEvent_shipDestroyed:  return "shipDestroyed";
    case aiplayEvent_bindChanged:    return "bindChanged";
    case aiplayEvent_matchContinued: return "matchContinued";
    default:                         return "unknown";
    }
}

void aiplayEventsWrite(aiplayJsonBuf *out)
{
    aiplayJsonBufAdd(out, "{\"events\":[", 11);
    for (size_t i = 0; i < eventLog.count; i++)
    {
        if (i > 0)
        {
            aiplayJsonBufAdd(out, ",", 1);
        }
        const aiplayEvent *e =
            &eventLog.entries[(eventLog.head + eventLog.count - 1 - i) % AIPLAY_EVENT_LOG_CAP];
        aiplayJsonBufAddStr(out, "{\"type\":\"");
        const char *tn = eventTypeName(e->type);
        aiplayJsonBufAdd(out, tn, strlen(tn));
        aiplayJsonBufAdd(out, "\",", 2);
        aiplayJsonInt(out, "tick", (int64_t)e->tick);
        aiplayJsonInt(out, "id", (int64_t)e->id);
        aiplayJsonInt(out, "aux", (int64_t)e->aux);
        aiplayJsonInt(out, "owner", e->owner);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
    }
    aiplayJsonBufAdd(out, "]}", 2);
}

// ------------------------------------------------------------- lifecycle

bool aiplayInit(bool enabled, const char *socketPath, const aiplayEngineBackend *be)
{
    aiplayIdMapReset();
    memset(&eventLog, 0, sizeof(eventLog));
    aiplay.enabled       = enabled;
    aiplay.state         = enabled ? aiplaySession_lobby : aiplaySession_disabled;
    aiplay.boundPlayer   = -1;
    aiplay.aiNeutralized = false;
    aiplay.nextOrderId   = 1;
    backend              = be;
    aiplayTransportReset();

    if (!enabled)
    {
        return true;
    }
    if (backend == NULL)
    {
        aiplay.enabled = false;
        aiplay.state   = aiplaySession_disabled;
        return false;
    }
    return aiplayTransportStart(socketPath != NULL ? socketPath : AIPLAY_DEFAULT_SOCKET);
}

void aiplayShutdown(void)
{
    if (aiplay.enabled && aiplay.aiNeutralized && backend != NULL && aiplay.boundPlayer >= 0)
    {
        backend->setPlayerNeutralized(aiplay.boundPlayer, false);
    }
    aiplayTransportStop();
    aiplay.enabled       = false;
    aiplay.state         = aiplaySession_disabled;
    aiplay.boundPlayer   = -1;
    aiplay.aiNeutralized = false;
    backend              = NULL;
}

// --------------------------------------------------------------- binding

static aiplayError bindPlayer(int32_t playerIndex)
{
    if (!aiplaySessionReady())
    {
        return aiplayError_noSession;
    }
    if (aiplay.state == aiplaySession_matchOver)
    {
        return aiplayError_matchOver;
    }
    if (playerIndex < 0 || playerIndex >= backend->numPlayers())
    {
        return aiplayError_badRequest;
    }
    if (aiplay.boundPlayer == playerIndex)
    {
        aiplayEventPush(aiplayEvent_matchContinued, 0, 0, playerIndex);
        return aiplayError_none;  // reconnect is a no-op (FR-010)
    }
    if (aiplay.boundPlayer >= 0)
    {
        return aiplayError_badRequest;  // exactly one binding (FR-004)
    }
    aiplay.boundPlayer   = playerIndex;
    aiplay.aiNeutralized = true;
    backend->setPlayerNeutralized(playerIndex, true);
    aiplayEventPush(aiplayEvent_bindChanged, 0, 0, playerIndex);
    return aiplayError_none;
}

// --------------------------------------------------------------- snapshots

static void writeStatus(aiplayJsonBuf *out)
{
    static const char *stateNames[] = {"disabled", "lobby", "playing", "matchOver"};
    aiplayJsonBufAdd(out, "{", 1);
    aiplayJsonString(out, "state", stateNames[aiplay.state]);
    aiplayJsonInt(out, "boundPlayerIndex", aiplay.boundPlayer);
    aiplayJsonInt(out, "sessionId", (int64_t)AIPLAY_PROTOCOL_VERSION);
    aiplayJsonString(out, "mapName", backend != NULL ? backend->mapName() : "");
    aiplayJsonBufAddStr(out, "\"matchStatus\":{");
    aiplayJsonBool(out, "running", backend != NULL && backend->gameRunning());
    aiplayJsonBool(out, "paused", backend != NULL && backend->isPaused());
    aiplayJsonInt(out, "tick", (int64_t)aiplay.lastTick);
    aiplayJsonTrimComma(out);
    aiplayJsonBufAdd(out, "}", 1);
    aiplayJsonTrimComma(out);
    aiplayJsonBufAdd(out, "}", 1);
}

// --------------------------------------------------------------- responses

void aiplayRespOpen(aiplayJsonBuf *out, const char *idRaw)
{
    aiplayJsonBufAdd(out, "{\"id\":", 6);
    aiplayJsonBufAdd(out, idRaw != NULL ? idRaw : "null", strlen(idRaw != NULL ? idRaw : "null"));
    aiplayJsonBufAdd(out, ",\"result\":", 10);
}

void aiplayRespClose(aiplayJsonBuf *out)
{
    aiplayJsonTrimComma(out);
    aiplayJsonBufAddStr(out, "}\n");
}

void aiplayRespError(aiplayJsonBuf *out, const char *idRaw, const char *code, const char *msg)
{
    aiplayJsonBufAdd(out, "{\"id\":", 6);
    aiplayJsonBufAdd(out, idRaw != NULL ? idRaw : "null", strlen(idRaw != NULL ? idRaw : "null"));
    aiplayJsonBufAdd(out, ",\"error\":{\"code\":\"", 18);
    aiplayJsonBufAdd(out, code, strlen(code));
    aiplayJsonBufAdd(out, "\",\"message\":\"", 13);
    aiplayJsonBufAdd(out, msg, strlen(msg));
    aiplayJsonBufAddStr(out, "\"}}\n");
}

// ------------------------------------------------------------- tick hooks

void aiplayTickBegin(void)
{
    if (!aiplaySessionReady())
    {
        return;
    }
    if (backend->gameRunning())
    {
        if (aiplay.state < aiplaySession_playing)
        {
            aiplay.state = aiplaySession_playing;
            aiplayEventPush(aiplayEvent_matchStarted, 0, 0, aiplay.boundPlayer);
        }
    }
    else if (aiplay.state == aiplaySession_playing || aiplay.state == aiplaySession_lobby)
    {
        aiplay.state = aiplaySession_matchOver;
        // winner = the only surviving side (single winner per skirmish; ties null)
        int  winner = -1;
        bool anyAlive = false, multi = false;
        for (int i = 0; i < backend->numPlayers(); i++)
        {
            if (backend->playerAlive(i))
            {
                if (anyAlive)
                {
                    multi = true;
                }
                anyAlive = true;
                winner   = i;
            }
        }
        aiplayEventPush(aiplayEvent_matchOver, 0, (uint64_t)(anyAlive && !multi ? winner + 1 : 0), aiplay.boundPlayer);
    }
    aiplay.lastTick = backend->tick();
    marksClear();                     // ship id sync starts (glue touches at tick end)
    aiplayOrdersProcessQueue();
}

void aiplayTickEnd(void)
{
    if (!aiplay.enabled)
    {
        return;
    }
    aiplayIdEvictUnseen();
}

// --------------------------------------------------------------- dispatch

void aiplayHandleLine(const char *line, aiplayJsonBuf *out)
{
    if (!aiplaySessionReady())
    {
        aiplayRespError(out, "null", "E_NO_SESSION", "ai-play disabled");
        return;
    }
    aiplayJsonDoc doc;
    if (!aiplayJsonParse(&doc, line) || doc.root < 0)
    {
        aiplayRespError(out, "null", "E_BAD_REQUEST", "unparseable JSON");
        return;
    }
    char idRaw[32] = "null";
    int32_t idNode = aiplayJsonGet(&doc, doc.root, "id");
    if (idNode >= 0)
    {
        const aiplayJsonNode *n = &doc.nodes[idNode];
        if (n->type == aiplayJson_number)
        {
            if (memchr(n->raw, '.', n->raw[0] ? strlen(n->raw) : 0) == NULL)
            {
                snprintf(idRaw, sizeof(idRaw), "%lld", (long long)n->intVal);
            }
            else
            {
                snprintf(idRaw, sizeof(idRaw), "%s", n->raw);
            }
        }
        else if (n->type == aiplayJson_string)
        {
            snprintf(idRaw, sizeof(idRaw), "\"%s\"", n->str);
        }
    }
    char    method[64] = {0};
    bool    haveMethod = aiplayJsonGetString(&doc, doc.root, "method", method, sizeof(method));
    int32_t params     = aiplayJsonGet(&doc, doc.root, "params");

    if (!haveMethod)
    {
        aiplayRespError(out, idRaw, "E_BAD_REQUEST", "missing method");
        return;
    }
    if (strcmp(method, "aiSession.bind") == 0)
    {
        int32_t idx = params >= 0 ? aiplayJsonGetInt(&doc, params, "playerIndex") : 0;
        if (idx < 0)
        {
            idx = 0;  // default binding per contract
        }
        aiplayError err = bindPlayer(idx);
        if (err == aiplayError_none)
        {
            aiplayRespOpen(out, idRaw);
            aiplayJsonBufAdd(out, "{", 1);
            aiplayJsonInt(out, "playerIndex", aiplay.boundPlayer);
            aiplayJsonInt(out, "sessionId", (int64_t)AIPLAY_PROTOCOL_VERSION);
            aiplayJsonString(out, "mapName", backend->mapName());
            aiplayJsonTrimComma(out);
            aiplayJsonBufAddStr(out, "}");     // close result object
            aiplayRespClose(out);              // close envelope
            return;
        }
        else
        {
            aiplayRespError(out, idRaw, aiplayErrorString(err), "bind failed");
        }
        return;
    }
    if (strcmp(method, "aiSession.status") == 0)
    {
        aiplayRespOpen(out, idRaw);
        writeStatus(out);
        aiplayRespClose(out);
        return;
    }
    if (strcmp(method, "state.snapshot") == 0)
    {
        aiplayRespOpen(out, idRaw);
        aiplaySnapshotWrite(out, &doc, params);
        aiplayRespClose(out);
        return;
    }
    if (strcmp(method, "state.events") == 0)
    {
        aiplayRespOpen(out, idRaw);
        aiplayEventsWrite(out);
        aiplayRespClose(out);
        return;
    }
    if (strncmp(method, "order.", 6) == 0)
    {
        // writes its own response (queued result or error)
        aiplayOrderSubmitJson(&doc, params, method + 6, idRaw, out);
        return;
    }
    if (strcmp(method, "time.pause") == 0)
    {
        aiplaySetPaused(true);
        aiplayRespOpen(out, idRaw);
        aiplayJsonBufAddStr(out, "{");
        aiplayJsonBool(out, "paused", true);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAddStr(out, "}");     // close result object
        aiplayRespClose(out);
        return;
    }
    if (strcmp(method, "time.resume") == 0)
    {
        aiplaySetPaused(false);
        aiplayRespOpen(out, idRaw);
        aiplayJsonBufAddStr(out, "{");
        aiplayJsonBool(out, "paused", false);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAddStr(out, "}");     // close result object
        aiplayRespClose(out);
        return;
    }
    if (strcmp(method, "time.step") == 0)
    {
        int n = params >= 0 ? aiplayJsonGetInt(&doc, params, "ticks") : 1;
        if (n < 1)
        {
            n = 1;
        }
        bool ok = aiplayStep(n);
        if (!ok)
        {
            aiplayRespError(out, idRaw, "E_BAD_REQUEST", "step requires paused state");
            return;
        }
        aiplayRespOpen(out, idRaw);
        aiplayJsonBufAddStr(out, "{");
        aiplayJsonInt(out, "stepped", n);
        aiplayJsonInt(out, "tick", (int64_t)backend->tick());
        aiplayJsonTrimComma(out);
        aiplayJsonBufAddStr(out, "}");     // close result object
        aiplayRespClose(out);
        return;
    }
    aiplayRespError(out, idRaw, "E_BAD_REQUEST", "unknown method");
}

// transport callback: parse + handle one line, response appended to out
void aiplayTransportDeliver(const char *line, aiplayJsonBuf *out)
{
    aiplayHandleLine(line, out);
}