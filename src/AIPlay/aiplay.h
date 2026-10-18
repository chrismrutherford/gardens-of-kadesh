//
// AI Play layer — public header
// Feature 002: an external AI plays one side of a skirmish while the user
// spectates the normal, rendered game session.
//
// Layering (constitution II): this module is a pure-C core that never includes
// engine headers. The engine build registers an "engine backend" (see
// aiplayEngineBackend); the unit-test build registers a stub backend and is
// compiled with -DAIPLAY_UNIT_TEST.
//
// Contract: specs/002-ai-plays-the-game-via-a-spectating-ui/contracts/ai-play.md
//
// SPDX-License-Identifier: GPL-2.0-or-later (matches repo COPYING)
#ifndef AIPLAY_H
#define AIPLAY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AIPLAY_PROTOCOL_VERSION  1
#define AIPLAY_MAX_SHIPS_PER_ORDER 64
#define AIPLAY_MAX_NAME          48
#define AIPLAY_ORDER_QUEUE_CAP   128
#define AIPLAY_EVENT_LOG_CAP     256
#define AIPLAY_ID_MAP_CAP        4096
#define AIPLAY_DEFAULT_SOCKET    "/tmp/homeworld-aiplay.sock"

// --------------------------------------------------------------- error codes

typedef enum aiplaySessionState
{
    aiplaySession_disabled = 0,
    aiplaySession_lobby,
    aiplaySession_playing,
    aiplaySession_matchOver,
} aiplaySessionState;

typedef enum aiplayError
{
    aiplayError_none = 0,
    aiplayError_noSession,
    aiplayError_badId,
    aiplayError_unaffordable,
    aiplayError_noBuilder,
    aiplayError_noLabs,
    aiplayError_matchOver,
    aiplayError_queueFull,
    aiplayError_badRequest,
    aiplayError_internal,
} aiplayError;

const char *aiplayErrorString(aiplayError e);

// --------------------------------------------------------------- order model

typedef enum aiplayVerb
{
    aiplayVerb_none = 0,
    aiplayVerb_move,
    aiplayVerb_attack,
    aiplayVerb_guard,
    aiplayVerb_dock,
    aiplayVerb_harvest,
    aiplayVerb_special,
    aiplayVerb_halt,
    aiplayVerb_build,
    aiplayVerb_research,
    aiplayVerb_formation,
    aiplayVerb_select,
    aiplayVerb_tactics,
    aiplayVerb_launch,
    aiplayVerb_autolaunch,
    aiplayVerb_scuttle,
} aiplayVerb;

typedef enum aiplayTargetKind
{
    aiplayTarget_none = 0,
    aiplayTarget_coord,
    aiplayTarget_ship,
    aiplayTarget_resource,
    aiplayTarget_name,
} aiplayTargetKind;

typedef struct aiplayOrderRequest
{
    uint64_t    id;       // echoed in orderApplied/orderRejected events
    aiplayVerb  verb;
    uint64_t    shipIds[AIPLAY_MAX_SHIPS_PER_ORDER];
    size_t      numShips;
    aiplayTargetKind targetKind;
    float       coord[3];
    uint64_t    targetId;
    char        name[AIPLAY_MAX_NAME];  // build type / research tech / formation
    int32_t     quantity;               // build quantity
    aiplayError validation;             // filled by the validator when rejected
} aiplayOrderRequest;

// ------------------------------------------------------------------ events

typedef enum aiplayEventType
{
    aiplayEvent_matchStarted = 0,
    aiplayEvent_matchOver,
    aiplayEvent_orderApplied,
    aiplayEvent_orderRejected,
    aiplayEvent_shipDestroyed,
    aiplayEvent_bindChanged,
    aiplayEvent_matchContinued,
} aiplayEventType;

typedef struct aiplayEvent
{
    aiplayEventType type;
    uint32_t        tick;
    uint64_t        id;              // order id / ship id
    uint64_t        aux;             // e.g. winner index; killer owner
    int32_t         owner;           // owner of event subject
} aiplayEvent;

// ------------------------------------------------------------------ session

typedef struct aiplaySession
{
    bool     enabled;      // --ai-play given; the whole layer is a no-op otherwise
    aiplaySessionState state;
    int32_t  boundPlayer;  // -1 = none
    bool     aiNeutralized;
    uint32_t lastTick;
    uint64_t nextOrderId;
    uint64_t nextShipId;
} aiplaySession;

extern aiplaySession aiplay;

// ------------------------------------------------------- engine backend vtable
//
// Pure core only talks to the engine through this vtable (stubs in unit tests).
struct aiplayShipInfo;

struct aiplayJsonBuf;
typedef struct aiplayJsonBuf aiplayJsonBuf;

typedef struct aiplayEngineBackend
{
    // world / match introspection
    int      (*numPlayers)(void);
    bool     (*playerAlive)(int playerIndex);
    bool     (*playerIsAI)(int playerIndex);
    int64_t  (*playerResourceUnits)(int playerIndex);
    const char *(*mapName)(void);
    bool     (*gameRunning)(void);
    uint32_t (*tick)(void);
    double   (*timeElapsed)(void);
    bool     (*isPaused)(void);
    // player binding: neutralize(true) detaches the built-in CPU for the bound
    // player, neutralize(false) restores it
    void     (*setPlayerNeutralized)(int playerIndex, bool neutralized);
    // orders: apply the request through the engine's own command funnel
    aiplayError (*dispatchOrder)(const aiplayOrderRequest *order);
    // ship/resource introspection (snapshot building; US2)
    bool     (*shipInfo)(void *shipOpaque, struct aiplayShipInfo *out);
    // time control (US4): pause/resume the simulation, step N ticks forward
    void     (*setPaused)(bool paused);
    bool     (*step)(int ticks);
    // build queue + research serialization (FR-001 completeness)
    void     (*buildQueueJSON)(struct aiplayJsonBuf *out, int playerIndex);
    void     (*researchJSON)(struct aiplayJsonBuf *out, int playerIndex);
    // save the current match to a named savegame (agent T040 convenience)
    bool     (*saveGame)(const char *name);
    // diagnostics sink (engine: dbgMessagef; tests: stdout); may be NULL
    void     (*traceLog)(const char *line);
    // exact research/build tree from the player's loaded TechStatics
    void     (*techTreeJSON)(struct aiplayJsonBuf *out, int playerIndex);
} aiplayEngineBackend;

typedef struct aiplayShipInfo
{
    void  *ship;          // engine handle (opaque to the core)
    int32_t owner;
    bool    isResource;   // true: object is a harvestable, not a ship
    const char *typeName; // engine-local name (e.g. "Interceptor", "Asteroid")
    float   pos[3];
    float   vel[3];
    const char  *orderVerb;     // NULL = idle; else "move"/"attack"/"harvest"...
    uint64_t     orderTargetId; // for attack/dock/harvest target refs
    float        orderCoord[3]; // for move/attack coord destinations
    bool         moving;
    float   health;
    float   healthMax;
    int32_t resourceValue;    // resources only
} aiplayShipInfo;

// ------------------------------------------------------------------ lifecycle

// Enable and start the layer (--ai-play on the command line). socketPath may be
// NULL for the default. Returns false if the transport could not be created.
bool aiplayInit(bool enabled, const char *socketPath, const aiplayEngineBackend *backend);
void aiplayShutdown(void);

// Frame hook: pump the transport socket (accept/read/write). Never blocks.
void aiplayFramePoll(void);

// Tick hooks: drain the order queue at the deterministic tick boundary.
void aiplayTickBegin(void);
void aiplayTickEnd(void);

// ------------------------------------------------------------- id map / glue
//
// The engine build touches one ship at the tick end; unseen ships are evicted.
uint64_t aiplayIdTouch(void *shipOpaque, int owner);            // returns id
void     aiplayIdEvictUnseen(void);                             // end of tick sync
void     aiplayNotifyShipDestroyed(void *shipOpaque, int killerOwner);

// ------------------------------------------------------------------- methods

// Handle one incoming JSON request line; appends a JSON response line to out
// (a growable buffer owned by the caller). Never calls the engine outside the
// backend; stateful dispatch is queue-only.
struct aiplayJsonBuf;
typedef struct aiplayJsonBuf aiplayJsonBuf;
void aiplayHandleLine(const char *line, struct aiplayJsonBuf *out);

// JSON writer primitives (deterministic key order; used by snapshot/events)
void aiplayBufJsonInt(struct aiplayJsonBuf *out, const char *key, int64_t v);
void aiplayBufJsonRaw(struct aiplayJsonBuf *out, const char *key, const char *raw);

#endif // AIPLAY_H