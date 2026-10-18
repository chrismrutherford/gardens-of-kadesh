//
// AI Play layer — internal declarations shared by the layer's translation units.
// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef AIPLAY_INTERNAL_H
#define AIPLAY_INTERNAL_H

#include "aiplay.h"
#include "aiplay_json.h"

// event log (implemented in aiplay.c, used by orders/snapshot)
void aiplayEventPush(aiplayEventType type, uint64_t id, uint64_t aux, int32_t owner);
void aiplayEventsWrite(aiplayJsonBuf *out);

typedef struct
{
    void   *ship;
    uint64_t id;
    int32_t  owner;
    bool     isResource;
} aiplayIdIterItem;

// id map (aiplay.c)
bool aiplayIdResolve(uint64_t id, void **ship, int32_t *owner);
void aiplayIdMapReset(void);
uint64_t aiplayIdTouchEx(void *ship, int owner, bool isResource);
bool aiplayIdEach(bool (*cb)(const aiplayIdIterItem *item, void *ctx), void *ctx);
const aiplayEngineBackend *aiplayBackendGet(void);

// snapshot writing (aiplay_snapshot.c): params subset of request
void aiplaySnapshotWrite(aiplayJsonBuf *out, const struct aiplayJsonDoc *doc, int32_t params);

// session extras (aiplay.c)
bool aiplayIsPaused(void);
void aiplaySetPaused(bool paused);      // sets engine pause + order allowance
bool aiplayStep(int ticks);             // step N ticks while paused

// session helpers (aiplay.c)
bool aiplaySessionReady(void);           // enabled && backend set
int32_t aiplayBoundPlayer(void);
bool aiplayMatchRunning(void);

// queue processing (aiplay_orders.c): drains applied/rejected events at tick begin
void aiplayOrdersProcessQueue(void);

// order queue (owned by aiplay_orders.c)
void        aiplayOrderSubmitJson(const aiplayJsonDoc *doc, int32_t params,
                                  const char *verbName, const char *idRaw, aiplayJsonBuf *out);
bool        aiplayOrderPopNext(aiplayOrderRequest *out);
aiplayError aiplayOrderSubmit(const aiplayOrderRequest *order);
aiplayError aiplayOrderDispatch(const aiplayOrderRequest *order);

// transport (aiplay_transport.c)
void aiplayTransportReset(void);
bool aiplayTransportStart(const char *path);
void aiplayTransportStop(void);
void aiplayFramePoll(void);   // declared in aiplay.h; re-declared for tests
void aiplayTransportDeliver(const char *line, aiplayJsonBuf *out);

// response envelope helpers (aiplay.c)
void aiplayRespOpen(aiplayJsonBuf *out, const char *idRaw);
void aiplayRespClose(aiplayJsonBuf *out);
void aiplayRespError(aiplayJsonBuf *out, const char *idRaw, const char *code, const char *msg);

#endif // AIPLAY_INTERNAL_H