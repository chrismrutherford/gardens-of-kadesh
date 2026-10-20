"""Tool schemas for the Homeworld agent — single source of truth.

Sent to the LLM as native `tools` (OpenAI function calling) and routed by
name to the game's aiplay MCP sidecar. Mirrors specs/002 contracts/ai-play.md.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "bind_session",
        "description": "Bind the AI to one player side of the running match",
        "parameters": {"type": "object", "properties": {
            "playerIndex": {"type": "integer", "description": "player to control (default 0)"}}}}},
    {"type": "function", "function": {
        "name": "game_status",
        "description": "Session + match status (running/paused/tick/map)",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "observe",
        "description": "Full battlefield snapshot: ships, resources, players, buildQueue, match status",
        "parameters": {"type": "object", "properties": {
            "sections": {"type": "array", "description": "optional subset, e.g. ['ships','resources']"},
            "owner": {"type": "integer", "description": "optional owner filter"}}}}},
    {"type": "function", "function": {
        "name": "get_events",
        "description": "Lifecycle + order events (matchOver, shipDestroyed, orderApplied, orderRejected)",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "order_move",
        "description": "Move ships to a 3-D coordinate",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}, "description": "ship ids to move"},
            "target": {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] destination"},
            "id": {"type": "integer", "description": "your request id, echoed in events"}}}}},
    {"type": "function", "function": {
        "name": "order_attack",
        "description": "Attack one enemy ship id",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "target": {"type": "integer", "description": "enemy ship id to attack"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_guard",
        "description": "Guard/protect a target ship",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "target": {"type": "integer", "description": "ship id to guard"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_dock",
        "description": "Dock at a dock-capable ship (refuel/repair/deposit)",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "dockWith": {"type": "integer", "description": "dock-capable ship id"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_harvest",
        "description": "Harvest an asteroid (ResourceCollectors)",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}, "description": "collector ids"},
            "target": {"type": "integer", "description": "resource (asteroid) id"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_special",
        "description": "Trigger ships' special abilities",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "target": {"type": "integer"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_halt",
        "description": "Halt ships",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_build",
        "description": "Build a ship type at a builder (e.g. the Mothership)",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}, "description": "builder id(s)"},
            "type": {"type": "string", "description": "e.g. ResourceCollector, LightInterceptor"},
            "quantity": {"type": "integer"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_research",
        "description": "Start researching a technology",
        "parameters": {"type": "object", "properties": {
            "tech": {"type": "string"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_formation",
        "description": "Set fleet formation",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "formation": {"type": "string", "description": "delta|broad|delta3d|claw|wall|sphere"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "pause",
        "description": "Pause the simulation",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "resume",
        "description": "Resume the simulation",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "order_tactics",
        "description": "Set combat tactics: aggressive (default fighting), neutral, evasive (retreat behaviour)",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}},
            "tactics": {"type": "string", "description": "aggressive|neutral|evasive"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_launch",
        "description": "Launch all docked fighters from a carrier/mothership (they build docked!)",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}, "description": "the carrier id"},
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "autolaunch",
        "description": "Toggle auto-launch of newly built fighters",
        "parameters": {"type": "object", "properties": {
            "onOff": {"type": "boolean"}, "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "order_scuttle",
        "description": "Scuttle (self-destruct) ships — last resort only",
        "parameters": {"type": "object", "properties": {
            "ships": {"type": "array", "items": {"type": "integer"}}, "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "save_game",
        "description": "Save the match to a named savegame (call when nagged)",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "savegame name (agent auto-names it: autosave-<epoch seconds>)"}}}}},
    {"type": "function", "function": {
        "name": "queue_done",
        "description": "Mark the current instruction-queue directive COMPLETED "
                       "(done when the queue's top instruction is achieved)",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "queue_fail",
        "description": "Mark the current instruction-queue directive IMPOSSIBLE "
                       "(e.g. locked hull, engine E_* errors persist)",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "step",
        "description": "Advance N ticks while paused",
        "parameters": {"type": "object", "properties": {
            "ticks": {"type": "integer"}}}}},
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}