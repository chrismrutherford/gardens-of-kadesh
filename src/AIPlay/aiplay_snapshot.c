//
// AI Play layer — snapshot builder (user story 2).
// Serialises ships, resources and match status from the engine's own lists at
// a tick boundary, exactly per specs/002.../data-model.md "Snapshot".
//
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_internal.h"

#include <stdlib.h>
#include <string.h>

struct shipFilter
{
    const aiplayJsonDoc *doc;
    int32_t              params;
    int32_t              ownerFilter;   // -1 = all
    bool                 wantShips;
    bool                 wantResources;
};

// parse {"sections":["ships","resources"],"owner":N}
static void filterParse(const aiplayJsonDoc *doc, int32_t params, struct shipFilter *f)
{
    f->doc           = doc;
    f->params        = params;
    f->ownerFilter   = -1;
    f->wantShips     = true;
    f->wantResources = true;
    if (params < 0)
    {
        return;
    }
    int32_t owner = aiplayJsonGetInt(doc, params, "owner");
    if (owner >= 0)
    {
        f->ownerFilter = owner;
    }
    int32_t sectionsNode = aiplayJsonGet(doc, params, "sections");
    if (sectionsNode >= 0)
    {
        f->wantShips = f->wantResources = false;
        int32_t child = aiplayJsonChild(doc, sectionsNode);
        while (child >= 0)
        {
            const aiplayJsonNode *n = &doc->nodes[child];
            if (n->type == aiplayJson_string)
            {
                if (strcmp(n->str, "ships") == 0)
                {
                    f->wantShips = true;
                }
                if (strcmp(n->str, "resources") == 0)
                {
                    f->wantResources = true;
                }
            }
            child = aiplayJsonNextSibling(doc, child);
        }
    }
}

static void writeVec(aiplayJsonBuf *out, const float v[3])
{
    aiplayJsonBufAdd(out, "[", 1);
    aiplayJsonBufPrintf(out, "%.3f,%.3f,%.3f", v[0], v[1], v[2]);
    aiplayJsonBufAdd(out, "]", 1);
}

static struct shipFilter filter;

static bool writeObjectEntry(const aiplayIdIterItem *item, void *ctx)
{
    aiplayJsonBuf *out = (aiplayJsonBuf *)ctx;
    const aiplayEngineBackend *be = aiplayBackendGet();
    if (be == NULL || be->shipInfo == NULL)
    {
        return true;
    }
    aiplayShipInfo info;
    if (!be->shipInfo(item->ship, &info))
    {
        return true;
    }
    if (info.isResource)
    {
        if (!filter.wantResources || (filter.ownerFilter >= 0 && item->owner != filter.ownerFilter))
        {
            return true;
        }
        aiplayJsonBufAdd(out, "{", 1);
    }
    else
    {
        if (!filter.wantShips || (filter.ownerFilter >= 0 && item->owner != filter.ownerFilter))
        {
            return true;
        }
        aiplayJsonBufAdd(out, "{", 1);
    }
    aiplayJsonInt(out, "id", (int64_t)item->id);
    aiplayJsonInt(out, "owner", item->owner);
    aiplayJsonString(out, "type", info.typeName != NULL ? info.typeName : "Unknown");
    aiplayJsonBufAddStr(out, "\"pos\":");
    writeVec(out, info.pos);
    aiplayJsonBufAdd(out, ",", 1);
    aiplayJsonBufAddStr(out, "\"vel\":");
    writeVec(out, info.vel);
    aiplayJsonBufAdd(out, ",", 1);
    if (info.isResource)
    {
        aiplayJsonInt(out, "value", info.resourceValue);
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
        return true;
    }
    aiplayJsonDouble(out, "health", info.health);
    aiplayJsonDouble(out, "healthMax", info.healthMax);
    if (info.moving)
    {
        aiplayJsonBufAddStr(out, "\"order\":{\"verb\":\"move\",\"target\":");
        writeVec(out, info.moveTo);
        aiplayJsonBufAddStr(out, "},");
    }
    else
    {
        aiplayJsonNull(out, "order");
    }
    aiplayJsonTrimComma(out);
    aiplayJsonBufAdd(out, "}", 1);
    return true;
}

void aiplaySnapshotWrite(aiplayJsonBuf *out, const aiplayJsonDoc *doc, int32_t params)
{
    const aiplayEngineBackend *be = aiplayBackendGet();
    filterParse(doc, params, &filter);

    aiplayJsonBufAdd(out, "{", 1);
    aiplayJsonInt(out, "tick", (int64_t)aiplay.lastTick);
    aiplayJsonDouble(out, "timeElapsed", be != NULL ? be->timeElapsed() : 0.0);
    aiplayJsonBool(out, "paused", aiplayIsPaused());

    aiplayJsonBufAddStr(out, "\"players\":[");
    int nPlayers = be != NULL ? be->numPlayers() : 0;
    for (int i = 0; i < nPlayers; i++)
    {
        if (i > 0)
        {
            aiplayJsonBufAdd(out, ",", 1);
        }
        aiplayJsonBufAdd(out, "{", 1);
        aiplayJsonInt(out, "index", i);
        aiplayJsonBool(out, "isAI", be->playerIsAI(i));
        aiplayJsonInt(out, "resourceUnits", be->playerResourceUnits(i));
        aiplayJsonBool(out, "alive", be->playerAlive(i));
        aiplayJsonBool(out, "lost", !be->playerAlive(i));
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
    }
    aiplayJsonBufAdd(out, "],", 2);

    aiplayJsonBufAddStr(out, "\"ships\":[");
    filter.wantShips     = true;
    filter.wantResources = false;
    aiplayIdEach(writeObjectEntry, out);
    aiplayJsonBufAdd(out, "],", 2);

    aiplayJsonBufAddStr(out, "\"resources\":[");
    filter.wantResources = true;
    aiplayIdEach(writeObjectEntry, out);
    aiplayJsonBufAdd(out, "],", 2);

    aiplayJsonRaw(out, "buildQueue", "[]");
    aiplayJsonRaw(out, "research", "[]");

    // match status: winner is known when the simulation has stopped
    bool running = be != NULL && be->gameRunning();
    aiplayJsonBufAddStr(out, "\"matchStatus\":{");
    aiplayJsonBool(out, "running", running);
    int  winner     = -1;
    bool anyAlive   = false;
    bool multiAlive = false;
    for (int i = 0; i < nPlayers; i++)
    {
        if (be->playerAlive(i))
        {
            if (anyAlive)
            {
                multiAlive = true;
            }
            anyAlive = true;
            winner   = i;
        }
    }
    if (!running && anyAlive && !multiAlive)
    {
        aiplayJsonInt(out, "winner", winner);
    }
    else
    {
        aiplayJsonNull(out, "winner");
    }
    aiplayJsonBufAddStr(out, "\"perPlayer\":[");
    for (int i = 0; i < nPlayers; i++)
    {
        if (i > 0)
        {
            aiplayJsonBufAdd(out, ",", 1);
        }
        aiplayJsonBufAdd(out, "{", 1);
        aiplayJsonInt(out, "index", i);
        aiplayJsonBool(out, "alive", be->playerAlive(i));
        aiplayJsonBool(out, "lost", !be->playerAlive(i));
        aiplayJsonTrimComma(out);
        aiplayJsonBufAdd(out, "}", 1);
    }
    aiplayJsonBufAdd(out, "]", 1);
    aiplayJsonTrimComma(out);
    aiplayJsonBufAdd(out, "}", 1);
    aiplayJsonTrimComma(out);
    aiplayJsonBufAdd(out, "}", 1);
}