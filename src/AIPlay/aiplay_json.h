//
// Minimal JSON support for the AI-play layer.
//
// Parser: builds a small DOM (nodes in a fixed arena; no malloc per value).
// Writer: aiplayJsonBuf is a growable output buffer with deterministic helpers.
//
// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef AIPLAY_JSON_H
#define AIPLAY_JSON_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "aiplay.h"

// --------------------------------------------------------------- output buffer

typedef struct aiplayJsonBuf
{
    char *  data;
    size_t  len;
    size_t  cap;
    bool    oom;
    bool    heap;   // data was malloc'ed (grown from the initial buffer)
} aiplayJsonBuf;

void aiplayJsonBufInit(aiplayJsonBuf *buf, char *staticBuf, size_t cap);
void aiplayJsonBufFree(aiplayJsonBuf *buf);
void aiplayJsonBufAdd(aiplayJsonBuf *buf, const char *bytes, size_t n);
void aiplayJsonBufAddStr(aiplayJsonBuf *buf, const char *s);
void aiplayJsonBufPrintf(aiplayJsonBuf *buf, const char *fmt, ...);

void aiplayJsonTrimComma(aiplayJsonBuf *buf);   // remove trailing ',' before '}'

// {"key": value,} helpers — a trailing comma is stripped by aiplayJsonTrimComma
// before an object/array is closed.
void aiplayJsonInt(aiplayJsonBuf *buf, const char *key, int64_t v);
void aiplayJsonBool(aiplayJsonBuf *buf, const char *key, bool v);
void aiplayJsonNull(aiplayJsonBuf *buf, const char *key);
void aiplayJsonString(aiplayJsonBuf *buf, const char *key, const char *s);
void aiplayJsonDouble(aiplayJsonBuf *buf, const char *key, double v);
void aiplayJsonRaw(aiplayJsonBuf *buf, const char *key, const char *rawJson);

// ------------------------------------------------------------------ dom parser

typedef enum aiplayJsonType
{
    aiplayJson_null = 0,
    aiplayJson_bool,
    aiplayJson_number,
    aiplayJson_string,
    aiplayJson_array,
    aiplayJson_object,
} aiplayJsonType;

#define AIPLAY_JSON_MAX_NODES  512
#define AIPLAY_JSON_MAX_STRING 512

typedef struct aiplayJsonNode
{
    aiplayJsonType type;
    // value
    bool     b;
    double     number;
    int64_t    intVal;                 // valid when the raw text is integral
    uint64_t   uintVal;                // valid when the raw text is integral
    char       raw[32];                // raw number text
    char     str[AIPLAY_JSON_MAX_STRING];
    // object/array members
    int32_t  child;                    // index of first child, -1 if none
    int32_t  nextSibling;              // index of next child, -1 if last
    char     key[AIPLAY_JSON_MAX_STRING]; // member key (objects only)
} aiplayJsonNode;

typedef struct aiplayJsonDoc
{
    aiplayJsonNode nodes[AIPLAY_JSON_MAX_NODES];
    int32_t        root;                 // index, -1 on parse error
    int32_t        count;
} aiplayJsonDoc;

bool aiplayJsonParse(aiplayJsonDoc *doc, const char *text);

// Lookups (object scope): returns node index or -1
int32_t aiplayJsonGet(const aiplayJsonDoc *doc, int32_t objectNode, const char *key);
bool    aiplayJsonGetString(const aiplayJsonDoc *doc, int32_t node, const char *key, char *out, size_t cap);
int32_t aiplayJsonGetInt(const aiplayJsonDoc *doc, int32_t node, const char *key);      // -1 missing
int64_t aiplayJsonGetInt64(const aiplayJsonDoc *doc, int32_t node, const char *key);    // -1 missing
uint64_t aiplayJsonGetUint64(const aiplayJsonDoc *doc, int32_t node, const char *key);  // 0 missing
bool    aiplayJsonGetBool(const aiplayJsonDoc *doc, int32_t node, const char *key, bool def);
double  aiplayJsonGetDouble(const aiplayJsonDoc *doc, int32_t node, const char *key);   // NAN missing

// Iterate an array's children
int32_t aiplayJsonChild(const aiplayJsonDoc *doc, int32_t arrayNode);
int32_t aiplayJsonNextSibling(const aiplayJsonDoc *doc, int32_t node);

#endif // AIPLAY_JSON_H