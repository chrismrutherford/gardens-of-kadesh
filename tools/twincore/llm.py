"""Spec 004 twin-core: LLM channel clients (T015, D6, FR-028).

Both channels speak OpenAI-compatible chat-completions over HTTP (URLs are
configuration only; local/LAN). S1: native tool-calling, returns
validator-shaped proposals. S2: structured JSON output (schema-validated by
packet.py, ≤1 bounded repair there). Output budgets are enforced by
truncation-before-send never mid-parse. Usage metering per call → recorder
`usage` lines. Bounded HTTP retries; LLMError when the channel is unusable.
"""
import json
import urllib.request


class LLMError(RuntimeError):
    """Channel unusable (endpoint down, retry budget exhausted)."""


def _post(url, payload, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _chat(url, payload, retries=2, timeout=60.0):
    """Bounded HTTP retries; transport errors propagate as LLMError."""
    last = None
    for attempt in range(1 + retries):
        try:
            return _post(url, payload, timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            last = e
    raise LLMError("llm endpoint unreachable after %d attempts: %s"
                   % (1 + retries, last))


def _usage_of(body):
    u = body.get("usage") or {}
    return {"prompt": u.get("prompt_tokens"),
            "completion": u.get("completion_tokens"),
            "total": u.get("total_tokens")}


def _first_choice(body):
    choices = body.get("choices") or []
    return (choices[0] or {}).get("message") or {}


def _tool_calls_of(message):
    out = []
    for c in message.get("tool_calls") or []:
        fn = c.get("function") or {}
        name = fn.get("name")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            continue  # malformed arguments: dropped (truncation safety)
        out.append({"verb": name, "params": args or {}})
    return out


class S1Channel:
    """Tactical core channel: native tool-calling (D6)."""

    def __init__(self, url, model="", max_output=200, timeout=60.0,
                 temperature=0.2, thinking=None):
        self.url = url
        self.model = model
        self.max_output = max_output
        self.temperature = temperature
        self.timeout = timeout
        self.thinking = thinking  # None = server default; False = no think

    def propose(self, messages, tools):
        """messages (static-first list), tools: either legacy
        {name, desc} hints (schemaless) or full OpenAI function dicts
        passed through unchanged → (proposals, usage)."""
        def _tooldef(t):
            if isinstance(t, dict) and t.get("type") == "function":
                return t  # full schema from the registry (preferred)
            return {"type": "function", "function": {
                "name": t["name"], "description": t.get("desc", ""),
                "parameters": {"type": "object", "properties": {},
                               "additionalProperties": True}}}
        payload = {
            "model": self.model,
            "messages": list(messages),
            "tools": [_tooldef(t) for t in tools],
            "max_tokens": self.max_output,
            "temperature": self.temperature,
        }
        if self.thinking is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        body = _chat(self.url, payload, timeout=self.timeout)
        message = _first_choice(body)
        return _tool_calls_of(message), _usage_of(body)


def _extract_json_object(text):
    """Robust JSON object extraction: direct parse → fenced block → first
    balanced {...} (strategist models sometimes wrap or prefix)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith(("json", "JSON")):
            t = t[4:]
        t = t.strip()
    for candidate in (t,):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
    # first balanced braced object
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            ch = t[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except (ValueError, TypeError):
                        break
        start = t.find("{", start + 1)
    return None


class S2Channel:
    """Strategist channel: structured JSON output (D6).

    max_output = the DELIVERABLE packet budget (enforced after parsing —
    thinking models spend tokens on reasoning_content first, so the HTTP
    request needs headroom: request_max_tokens, default 4x)."""

    def __init__(self, url, model="", max_output=2048, timeout=120.0,
                 temperature=0.4, thinking=None, request_max_tokens=None):
        self.url = url
        self.model = model
        self.max_output = max_output
        self.temperature = temperature
        self.timeout = timeout
        self.thinking = thinking
        self.max_tokens = request_max_tokens or max_output * 4

    def propose(self, messages):
        """→ ({"packet": dict|None, "error": str?}, usage)."""
        payload = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        if self.thinking is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            payload["thinking"] = {"type": "disabled"}  # GLM-style param
        try:
            body = _chat(self.url, payload, timeout=self.timeout)
        except LLMError as e:
            return {"packet": None, "error": str(e)}, {}
        message = _first_choice(body)
        usage = _usage_of(body)
        text = message.get("content")
        packet = _extract_json_object(text)
        if packet is not None and not isinstance(packet, dict):
            packet = None
        # raw completion passed through for observability (debug IO tap;
        # not a trail row — v4 driver dumps it when V4_DEBUG_IO is set)
        if packet is None:
            return ({"packet": None, "raw": text,
                     "error": "unparseable JSON from strategist: %r"
                              % ((text or "")[:200])}, usage)
        return {"packet": packet, "raw": text}, usage