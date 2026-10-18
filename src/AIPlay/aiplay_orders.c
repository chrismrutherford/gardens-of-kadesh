//
// AI Play layer — order parsing, validation, queueing and dispatch.
//
// This translation unit is the only place in the layer that issues gameplay
// commands; the engine build's backend (aiplay_engine.c) forwards them to the
// very same clWrap* entry points the human UI uses (constitution I, R-2).
//
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_internal.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static aiplayVerb verbFromString(const char *s)
{
    static const struct
    {
        const char *n;
        aiplayVerb  v;
    } table[] = {
        {"move",      aiplayVerb_move},
        {"attack",    aiplayVerb_attack},
        {"guard",     aiplayVerb_guard},
        {"dock",      aiplayVerb_dock},
        {"harvest",   aiplayVerb_harvest},
        {"special",   aiplayVerb_special},
        {"halt",      aiplayVerb_halt},
        {"build",     aiplayVerb_build},
        {"research",  aiplayVerb_research},
        {"formation", aiplayVerb_formation},
        {"select",    aiplayVerb_select},
    };
    for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); i++)
    {
        if (strcmp(table[i].n, s) == 0)
        {
            return table[i].v;
        }
    }
    return aiplayVerb_none;
}

static aiplayError parseOrder(
    const aiplayJsonDoc *doc, int32_t params, const char *verbName, aiplayOrderRequest *out)
{
    memset(out, 0, sizeof(*out));
    out->id   = (uint64_t)aiplayJsonGetInt64(doc, params, "id");
    out->verb = verbFromString(verbName);
    if (out->verb == aiplayVerb_none)
    {
        return aiplayError_badRequest;
    }
    out->quantity = 1;

    int32_t shipsNode = aiplayJsonGet(doc, params, "ships");
    if (shipsNode >= 0)
    {
        int32_t child = aiplayJsonChild(doc, shipsNode);
        while (child >= 0 && out->numShips < AIPLAY_MAX_SHIPS_PER_ORDER)
        {
            if (doc->nodes[child].type == aiplayJson_number)
            {
                out->shipIds[out->numShips++] = doc->nodes[child].uintVal;
            }
            child = aiplayJsonNextSibling(doc, child);
        }
    }

    int32_t targetNode = aiplayJsonGet(doc, params, "target");
    if (targetNode >= 0)
    {
        const aiplayJsonNode target = doc->nodes[targetNode];
        if (target.type == aiplayJson_array)
        {
            int32_t c = aiplayJsonChild(doc, targetNode);
            int   i = 0;
            while (c >= 0 && i < 3)
            {
                out->coord[i++] = (float)doc->nodes[c].number;
                c = aiplayJsonNextSibling(doc, c);
            }
            out->targetKind = aiplayTarget_coord;
        }
        else if (target.type == aiplayJson_number)
        {
            out->targetKind = aiplayTarget_ship;
            out->targetId   = target.uintVal;
        }
        else if (target.type == aiplayJson_string)
        {
            strncpy(out->name, target.str, AIPLAY_MAX_NAME - 1);
            out->name[AIPLAY_MAX_NAME - 1] = '\0';
            out->targetKind                = aiplayTarget_name;
        }
    }

    if (aiplayJsonGet(doc, params, "dockWith") >= 0)
    {
        out->targetId   = (uint64_t)aiplayJsonGetInt64(doc, params, "dockWith");
        out->targetKind = aiplayTarget_ship;
    }
    if (aiplayJsonGet(doc, params, "quantity") >= 0)
    {
        int32_t q = aiplayJsonGetInt(doc, params, "quantity");
        if (q >= 1)
        {
            out->quantity = q;
        }
    }
    return aiplayError_none;
}

// Synchronous (submit-time) validation: cheap checks only; gameplay-accurate
// validation happens engine-side when the order is applied (research R-2).
static aiplayError validateOrder(const aiplayOrderRequest *o)
{
    if (!aiplaySessionReady())
    {
        return aiplayError_noSession;
    }
    if (!aiplayMatchRunning())
    {
        return aiplayError_matchOver;
    }
    if (o->verb == aiplayVerb_none)
    {
        return aiplayError_badRequest;
    }
    switch (o->verb)
    {
    case aiplayVerb_research:
    case aiplayVerb_select:
        break;  // fleet-wide commands do not need ship ids
    default:
        if (o->numShips == 0)
        {
            return aiplayError_badRequest;
        }
        break;
    }
    for (size_t i = 0; i < o->numShips; i++)
    {
        int32_t owner = -1;
        if (!aiplayIdResolve(o->shipIds[i], NULL, &owner) || owner != aiplayBoundPlayer())
        {
            return aiplayError_badId;
        }
    }
    switch (o->verb)
    {
    case aiplayVerb_move:
        if (o->targetKind != aiplayTarget_coord)
        {
            return aiplayError_badRequest;
        }
        break;
    case aiplayVerb_attack:
    case aiplayVerb_guard:
    case aiplayVerb_dock:
    case aiplayVerb_harvest:
        if (o->targetKind == aiplayTarget_none)
        {
            return aiplayError_badRequest;
        }
        break;
    case aiplayVerb_research:
        if (o->name[0] == '\0')
        {
            return aiplayError_badRequest;
        }
        break;
    case aiplayVerb_build:
        if (o->name[0] == '\0')
        {
            return aiplayError_badRequest;
        }
        break;
    case aiplayVerb_formation:
        if (o->name[0] == '\0')
        {
            return aiplayError_badRequest;
        }
        break;
    default:
        break;
    }
    return aiplayError_none;
}

// queue ---------------------------------------------------------------

static aiplayOrderRequest orderQueue[AIPLAY_ORDER_QUEUE_CAP];
static size_t             queueHead  = 0;
static size_t             queueCount = 0;

aiplayError aiplayOrderSubmit(const aiplayOrderRequest *order)
{
    if (queueCount >= AIPLAY_ORDER_QUEUE_CAP)
    {
        return aiplayError_queueFull;
    }
    orderQueue[(queueHead + queueCount) % AIPLAY_ORDER_QUEUE_CAP] = *order;
    queueCount++;
    return aiplayError_none;
}

bool aiplayOrderPopNext(aiplayOrderRequest *out)
{
    if (queueCount == 0)
    {
        return false;
    }
    *out      = orderQueue[queueHead];
    queueHead = (queueHead + 1) % AIPLAY_ORDER_QUEUE_CAP;
    queueCount--;
    return true;
}

aiplayError aiplayOrderDispatch(const aiplayOrderRequest *order)
{
    // the engine backend applies the order through the human command funnel
    extern const aiplayEngineBackend *aiplayBackendGet(void);
    const aiplayEngineBackend *be = aiplayBackendGet();
    if (be == NULL)
    {
        return aiplayError_noSession;
    }
    aiplayError err = be->dispatchOrder(order);
    // engine-side rejections surface as orderRejected events at the tick boundary
    return err;
}

void aiplayOrdersProcessQueue(void)
{
    // drains in submission order (FR-007). Called once per tick from aiplayTickBegin.
    aiplayOrderRequest o;
    while (aiplayOrderPopNext(&o))
    {
        aiplayError err = validateOrder(&o);
        if (err == aiplayError_none)
        {
            err = aiplayOrderDispatch(&o);
        }
        if (err == aiplayError_none)
        {
            aiplayEventPush(aiplayEvent_orderApplied, o.id, 0, aiplayBoundPlayer());
        }
        else
        {
            aiplayEventPush(aiplayEvent_orderRejected, o.id, (uint64_t)err, aiplayBoundPlayer());
        }
    }
}

// ------------------------------------------------- submit (called from aiplay.c)

void aiplayOrderSubmitJson(
    const aiplayJsonDoc *  doc,
    int32_t                params,
    const char *           verbName,
    const char *           idRaw,
    aiplayJsonBuf *        out)
{
    aiplayOrderRequest req;
    aiplayError        err;
    if (params < 0)
    {
        err = aiplayError_badRequest;
    }
    else
    {
        err = parseOrder(doc, params, verbName, &req);
        if (err == aiplayError_none)
        {
            err = validateOrder(&req);
            if (err == aiplayError_none)
            {
                err = aiplayOrderSubmit(&req);
            }
        }
    }
    if (err == aiplayError_none)
    {
        aiplayRespOpen(out, idRaw);
        aiplayJsonBufAddStr(out, "{");
        aiplayJsonBool(out, "queued", true);
        aiplayJsonInt(out, "orderId", (int64_t)req.id);
        aiplayRespClose(out);
    }
    else
    {
        aiplayRespError(out, idRaw, aiplayErrorString(err), "order rejected");
    }
}