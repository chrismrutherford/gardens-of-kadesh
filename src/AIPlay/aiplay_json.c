//
// Minimal JSON parser + writer for the AI-play layer.
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_json.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ----------------------------------------------------------------- buffer

void aiplayJsonBufInit(aiplayJsonBuf *buf, char *staticBuf, size_t cap)
{
    buf->data = staticBuf;
    buf->len  = 0;
    buf->cap  = cap;
    buf->oom  = false;
    buf->heap = false;
    if (cap > 0)
    {
        buf->data[0] = '\0';
    }
}

void aiplayJsonBufFree(aiplayJsonBuf *buf)
{
    if (buf->heap)
    {
        free(buf->data);
    }
    buf->data = NULL;
    buf->cap  = buf->len = 0;
    buf->heap = false;
}

static void bufGrow(aiplayJsonBuf *buf, size_t need)
{
    if (buf->len + need + 1 <= buf->cap)
    {
        return;
    }
    size_t newCap = buf->cap ? buf->cap * 2 : 4096;
    while (newCap < buf->len + need + 1)
    {
        newCap *= 2;
    }
    char *grown = NULL;
    if (buf->heap)
    {
        grown = realloc(buf->data, newCap);
    }
    else
    {
        grown = malloc(newCap);
        if (grown != NULL)
        {
            memcpy(grown, buf->data, buf->len + 1);
            buf->heap = true;
        }
    }
    if (grown == NULL)
    {
        buf->oom = true;
        return;
    }
    buf->data = grown;
    buf->cap  = newCap;
}

void aiplayJsonBufAdd(aiplayJsonBuf *buf, const char *bytes, size_t n)
{
    if (buf->oom)
    {
        return;
    }
    bufGrow(buf, n);
    if (buf->oom)
    {
        return;
    }
    memcpy(buf->data + buf->len, bytes, n);
    buf->len += n;
    buf->data[buf->len] = '\0';
}

void aiplayJsonBufAddStr(aiplayJsonBuf *buf, const char *s)
{
    aiplayJsonBufAdd(buf, s, strlen(s));
}

void aiplayJsonBufPrintf(aiplayJsonBuf *buf, const char *fmt, ...)
{
    char    tmp[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0)
    {
        return;
    }
    if ((size_t)n >= sizeof(tmp))
    {
        n = (int)sizeof(tmp) - 1;
    }
    aiplayJsonBufAdd(buf, tmp, (size_t)n);
}

static void jsonEscape(aiplayJsonBuf *buf, const char *s)
{
    aiplayJsonBufAdd(buf, "\"", 1);
    for (const char *p = s; *p != '\0'; p++)
    {
        switch (*p)
        {
        case '"': aiplayJsonBufAdd(buf, "\\\"", 2); break;
        case '\\': aiplayJsonBufAdd(buf, "\\\\", 2); break;
        case '\n': aiplayJsonBufAdd(buf, "\\n", 2); break;
        case '\r': aiplayJsonBufAdd(buf, "\\r", 2); break;
        case '\t': aiplayJsonBufAdd(buf, "\\t", 2); break;
        default:
            if ((unsigned char)*p < 0x20)
            {
                aiplayJsonBufPrintf(buf, "\\u%04x", *p);
            }
            else
            {
                aiplayJsonBufAdd(buf, p, 1);
            }
            break;
        }
    }
    aiplayJsonBufAdd(buf, "\"", 1);
}

void aiplayJsonTrimComma(aiplayJsonBuf *buf)
{
    while (buf->len > 0 && buf->data[buf->len - 1] == ',')
    {
        buf->len--;
        buf->data[buf->len] = '\0';
    }
}

void aiplayJsonInt(aiplayJsonBuf *buf, const char *key, int64_t v)
{
    aiplayJsonBufPrintf(buf, "\"%s\":%lld,", key, (long long)v);
}

void aiplayJsonBool(aiplayJsonBuf *buf, const char *key, bool v)
{
    aiplayJsonBufPrintf(buf, "\"%s\":%s,", key, v ? "true" : "false");
}

void aiplayJsonNull(aiplayJsonBuf *buf, const char *key)
{
    aiplayJsonBufPrintf(buf, "\"%s\":null,", key);
}

void aiplayJsonString(aiplayJsonBuf *buf, const char *key, const char *s)
{
    aiplayJsonBufPrintf(buf, "\"%s\":", key);
    jsonEscape(buf, s);
    aiplayJsonBufAdd(buf, ",", 1);
}

void aiplayJsonDouble(aiplayJsonBuf *buf, const char *key, double v)
{
    aiplayJsonBufPrintf(buf, "\"%s\":%.6f,", key, v);
}

void aiplayJsonRaw(aiplayJsonBuf *buf, const char *key, const char *rawJson)
{
    aiplayJsonBufPrintf(buf, "\"%s\":%s,", key, rawJson);
}

// ----------------------------------------------------------------- parser

typedef struct aiplayJsonParseCtx
{
    const char *text;
    size_t      pos;
    aiplayJsonDoc *doc;
    bool        failed;
} aiplayJsonParseCtx;

static void skipWs(aiplayJsonParseCtx *ctx)
{
    while (ctx->text[ctx->pos] == ' ' || ctx->text[ctx->pos] == '\t' ||
           ctx->text[ctx->pos] == '\n' || ctx->text[ctx->pos] == '\r')
    {
        ctx->pos++;
    }
}

static bool peek(aiplayJsonParseCtx *ctx, char c)
{
    skipWs(ctx);
    return ctx->text[ctx->pos] == c;
}

static void fail(aiplayJsonParseCtx *ctx)
{
    ctx->failed = true;
}

static int32_t allocNode(aiplayJsonParseCtx *ctx)
{
    if (ctx->doc->count >= AIPLAY_JSON_MAX_NODES)
    {
        fail(ctx);
        return -1;
    }
    int32_t idx = ctx->doc->count++;
    aiplayJsonNode *n = &ctx->doc->nodes[idx];
    n->type        = aiplayJson_null;
    n->child       = -1;
    n->nextSibling = -1;
    n->key[0]      = '\0';
    return idx;
}

// forward
static int32_t parseValue(aiplayJsonParseCtx *ctx);

static bool parseStringRaw(aiplayJsonParseCtx *ctx, char *out, size_t cap)
{
    if (!peek(ctx, '"'))
    {
        fail(ctx);
        return false;
    }
    ctx->pos++;
    size_t o = 0;
    while (true)
    {
        char c = ctx->text[ctx->pos];
        if (c == '\0' || c == '"')
        {
            break;
        }
        if (c == '\\')
        {
            ctx->pos++;
            char e = ctx->text[ctx->pos];
            if (e == 'n') { c = '\n'; } else if (e == 't') { c = '\t'; }
            else if (e == 'r') { c = '\r'; } else if (e == '"') { c = '"'; }
            else if (e == '\\') { c = '\\'; } else if (e == '/') { c = '/'; }
            else if (e == '\0') { fail(ctx); return false; }
            else if (e == 'u')
            {
                // decode a single BMP escape to UTF-8 (best-effort)
                if (ctx->pos + 4 >= strlen(ctx->text) + 1)
                {
                    fail(ctx);
                    return false;
                }
                char    hex[5] = {0};
                memcpy(hex, ctx->text + ctx->pos + 1, 4);
                long    cp = strtol(hex, NULL, 16);
                ctx->pos += 4;
                if (cp < 0x80)
                {
                    c = (char)cp;
                }
                else if (cp < 0x800)
                {
                    if (o + 2 < cap)
                    {
                        out[o++] = (char)(0xC0 | (cp >> 6));
                        out[o++] = (char)(0x80 | (cp & 0x3F));
                    }
                    ctx->pos++;
                    continue;
                }
                else
                {
                    if (o + 3 < cap)
                    {
                        out[o++] = (char)(0xE0 | (cp >> 12));
                        out[o++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                        out[o++] = (char)(0x80 | (cp & 0x3F));
                    }
                    ctx->pos++;
                    continue;
                }
            }
            else { c = e; }
        }
        if (o + 1 >= cap)
        {
            fail(ctx);
            return false;
        }
        out[o++] = c;
        ctx->pos++;
    }
    if (!peek(ctx, '"'))
    {
        fail(ctx);
        return false;
    }
    ctx->pos++;
    out[o] = '\0';
    return true;
}

static int32_t parseObject(aiplayJsonParseCtx *ctx)
{
    int32_t obj = allocNode(ctx);
    if (obj < 0)
    {
        return -1;
    }
    ctx->doc->nodes[obj].type = aiplayJson_object;
    ctx->pos++;  // '{'
    int32_t lastChild = -1;
    if (!peek(ctx, '}'))
    {
        while (!ctx->failed)
        {
            char key[AIPLAY_JSON_MAX_STRING];
            if (!parseStringRaw(ctx, key, sizeof(key)))
            {
                fail(ctx);
                break;
            }
            if (!peek(ctx, ':'))
            {
                fail(ctx);
                break;
            }
            ctx->pos++;
            int32_t child = parseValue(ctx);
            if (ctx->failed || child < 0)
            {
                break;
            }
            memcpy(ctx->doc->nodes[child].key, key, sizeof(key));
            if (lastChild < 0)
            {
                ctx->doc->nodes[obj].child = child;
            }
            else
            {
                ctx->doc->nodes[lastChild].nextSibling = child;
            }
            lastChild = child;
            if (peek(ctx, ','))
            {
                ctx->pos++;
                continue;
            }
            break;
        }
    }
    if (!peek(ctx, '}'))
    {
        fail(ctx);
        return -1;
    }
    ctx->pos++;
    return obj;
}

static int32_t parseArray(aiplayJsonParseCtx *ctx)
{
    int32_t arr = allocNode(ctx);
    if (arr < 0)
    {
        return -1;
    }
    ctx->doc->nodes[arr].type = aiplayJson_array;
    ctx->pos++;  // '['
    int32_t lastChild = -1;
    if (!peek(ctx, ']'))
    {
        while (!ctx->failed)
        {
            int32_t child = parseValue(ctx);
            if (ctx->failed || child < 0)
            {
                break;
            }
            if (lastChild < 0)
            {
                ctx->doc->nodes[arr].child = child;
            }
            else
            {
                ctx->doc->nodes[lastChild].nextSibling = child;
            }
            lastChild = child;
            if (peek(ctx, ','))
            {
                ctx->pos++;
                continue;
            }
            break;
        }
    }
    if (!peek(ctx, ']'))
    {
        fail(ctx);
        return -1;
    }
    ctx->pos++;
    return arr;
}

static int32_t parseNumber(aiplayJsonParseCtx *ctx)
{
    int32_t node = allocNode(ctx);
    if (node < 0)
    {
        return -1;
    }
    const char *start = ctx->text + ctx->pos;
    char *      end;
    double      v    = strtod(start, &end);
    if (end == start)
    {
        fail(ctx);
        return -1;
    }
    size_t rawLen = (size_t)(end - start);
    if (rawLen >= sizeof(ctx->doc->nodes[node].raw))
    {
        rawLen = sizeof(ctx->doc->nodes[node].raw) - 1;
    }
    memcpy(ctx->doc->nodes[node].raw, start, rawLen);
    ctx->doc->nodes[node].raw[rawLen] = '\0';
    ctx->doc->nodes[node].type        = aiplayJson_number;
    ctx->doc->nodes[node].number      = v;
    if (strchr(ctx->doc->nodes[node].raw, '.') == NULL &&
        strchr(ctx->doc->nodes[node].raw, 'e') == NULL &&
        strchr(ctx->doc->nodes[node].raw, 'E') == NULL)
    {
        ctx->doc->nodes[node].intVal  = (int64_t)strtoll(ctx->doc->nodes[node].raw, NULL, 10);
        ctx->doc->nodes[node].uintVal = (uint64_t)strtoull(ctx->doc->nodes[node].raw, NULL, 10);
    }
    else
    {
        ctx->doc->nodes[node].intVal  = (int64_t)v;
        ctx->doc->nodes[node].uintVal = (uint64_t)v;
    }
    ctx->pos += rawLen;
    return node;
}

static int32_t parseValue(aiplayJsonParseCtx *ctx)
{
    skipWs(ctx);
    char c = ctx->text[ctx->pos];
    if (c == '{')
    {
        return parseObject(ctx);
    }
    if (c == '[')
    {
        return parseArray(ctx);
    }
    if (c == '"')
    {
        int32_t node = allocNode(ctx);
        if (node < 0)
        {
            return -1;
        }
        ctx->doc->nodes[node].type = aiplayJson_string;
        if (!parseStringRaw(ctx, ctx->doc->nodes[node].str, sizeof(ctx->doc->nodes[node].str)))
        {
            fail(ctx);
            return -1;
        }
        return node;
    }
    if (strncmp(ctx->text + ctx->pos, "true", 4) == 0)
    {
        int32_t node = allocNode(ctx);
        if (node < 0) { return -1; }
        ctx->doc->nodes[node].type  = aiplayJson_bool;
        ctx->doc->nodes[node].b     = true;
        ctx->pos += 4;
        return node;
    }
    if (strncmp(ctx->text + ctx->pos, "false", 5) == 0)
    {
        int32_t node = allocNode(ctx);
        if (node < 0) { return -1; }
        ctx->doc->nodes[node].type  = aiplayJson_bool;
        ctx->doc->nodes[node].b     = false;
        ctx->pos += 5;
        return node;
    }
    if (strncmp(ctx->text + ctx->pos, "null", 4) == 0)
    {
        int32_t node = allocNode(ctx);
        if (node < 0) { return -1; }
        ctx->doc->nodes[node].type = aiplayJson_null;
        ctx->pos += 4;
        return node;
    }
    if (c == '-' || (c >= '0' && c <= '9'))
    {
        return parseNumber(ctx);
    }
    fail(ctx);
    return -1;
}

bool aiplayJsonParse(aiplayJsonDoc *doc, const char *text)
{
    memset(doc, 0, sizeof(*doc));
    doc->root = -1;
    aiplayJsonParseCtx ctx = {.text = text, .pos = 0, .doc = doc, .failed = false};
    skipWs(&ctx);
    if (ctx.text[ctx.pos] == '\0')
    {
        return false;
    }
    doc->root = parseValue(&ctx);
    return !ctx.failed && doc->root >= 0;
}

// ----------------------------------------------------------------- lookups

int32_t aiplayJsonGet(const aiplayJsonDoc *doc, int32_t objectNode, const char *key)
{
    if (objectNode < 0 || doc->nodes[objectNode].type != aiplayJson_object)
    {
        return -1;
    }
    for (int32_t i = doc->nodes[objectNode].child; i >= 0; i = doc->nodes[i].nextSibling)
    {
        if (strcmp(doc->nodes[i].key, key) == 0)
        {
            return i;
        }
    }
    return -1;
}

bool aiplayJsonGetString(const aiplayJsonDoc *doc, int32_t node, const char *key, char *out, size_t cap)
{
    int32_t n = aiplayJsonGet(doc, node, key);
    if (n < 0 || doc->nodes[n].type != aiplayJson_string)
    {
        return false;
    }
    strncpy(out, doc->nodes[n].str, cap - 1);
    out[cap - 1] = '\0';
    return true;
}

int32_t aiplayJsonGetInt(const aiplayJsonDoc *doc, int32_t node, const char *key)
{
    int64_t v = aiplayJsonGetInt64(doc, node, key);
    if (v == -1)
    {
        return -1;
    }
    if (v < INT32_MIN || v > INT32_MAX)
    {
        return -1;
    }
    return (int32_t)v;
}

int64_t aiplayJsonGetInt64(const aiplayJsonDoc *doc, int32_t node, const char *key)
{
    int32_t n = aiplayJsonGet(doc, node, key);
    if (n < 0 || doc->nodes[n].type != aiplayJson_number)
    {
        return -1;
    }
    return doc->nodes[n].intVal;
}

uint64_t aiplayJsonGetUint64(const aiplayJsonDoc *doc, int32_t node, const char *key)
{
    int32_t n = aiplayJsonGet(doc, node, key);
    if (n < 0 || doc->nodes[n].type != aiplayJson_number)
    {
        return 0;
    }
    return doc->nodes[n].uintVal;
}

bool aiplayJsonGetBool(const aiplayJsonDoc *doc, int32_t node, const char *key, bool def)
{
    int32_t n = aiplayJsonGet(doc, node, key);
    if (n < 0 || doc->nodes[n].type != aiplayJson_bool)
    {
        return def;
    }
    return doc->nodes[n].b;
}
int32_t aiplayJsonChild(const aiplayJsonDoc *doc, int32_t arrayNode)
{
    if (arrayNode < 0)
    {
        return -1;
    }
    return doc->nodes[arrayNode].child;
}

int32_t aiplayJsonNextSibling(const aiplayJsonDoc *doc, int32_t node)
{
    if (node < 0)
    {
        return -1;
    }
    return doc->nodes[node].nextSibling;
}
