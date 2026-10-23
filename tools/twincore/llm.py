"""Spec 004 twin-core: LLM channel clients (T015, D6, FR-028).

Both channels speak OpenAI-compatible chat-completions via the official
openai python SDK (URLs are configuration only; local/LAN). S1: native
tool-calling, returns validator-shaped proposals. S2: structured JSON
output (schema validation in program.py/packet.py, ≤1 bounded repair
there). GLM-style reasoning models: reasoning_content is surfaced in the
raw passthrough and scanned for the answer JSON when content is empty
(thinking-mode rescue). Usage metering per call → recorder `usage` lines.
Transport retries are the SDK's (max_retries=1); any channel failure
becomes LLMError.
"""
import json

from openai import OpenAI

LLM_RETRIES = 1


class LLMError(RuntimeError):
    """Channel unusable (endpoint down, retry budget exhausted)."""


def _base_url(url):
    """Endpoint URL (…/v1/chat/completions) → SDK base_url (…/v1)."""
    suffix = "/chat/completions"
    return url[:-len(suffix)] if url.endswith(suffix) else url


def _client(url, timeout):
    return OpenAI(base_url=_base_url(url), api_key="local",
                  timeout=timeout, max_retries=LLM_RETRIES)


def _usage_of(usage):
    if usage is None:
        return {"prompt": None, "completion": None, "total": None}
    return {"prompt": usage.prompt_tokens,
            "completion": usage.completion_tokens,
            "total": usage.total_tokens}


def _message_extras(message):
    m = message.model_extra or {}
    for k in ("reasoning_content", "reasoning"):
        if m.get(k):
            return {"content": message.content, "reasoning": m[k],
                    "reasoning_key": k}
    return {"content": message.content, "reasoning": None,
            "reasoning_key": None}


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
        self._client = _client(url, timeout)

    def _tooldef(self, t):
        if isinstance(t, dict) and t.get("type") == "function":
            return dict(t)  # full schema from the registry (preferred)
        return {"type": "function", "function": {
            "name": t["name"], "description": t.get("desc", ""),
            "parameters": {"type": "object", "properties": {},
                           "additionalProperties": True}}}

    def propose(self, messages, tools):
        """messages (static-first list), tools: either legacy
        {name, desc} hints (schemaless) or full OpenAI function dicts
        passed through unchanged → (proposals, usage)."""
        kwargs = {
            "model": self.model,
            "messages": list(messages),
            "tools": [self._tooldef(t) for t in tools],
            "max_tokens": self.max_output,
            "temperature": self.temperature,
        }
        if self.thinking is False:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}}
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError("llm endpoint unreachable: %s" % e)
        message = completion.choices[0].message
        out = []
        for c in message.tool_calls or []:
            name = c.function.name
            try:
                args = json.loads(c.function.arguments or "{}")
            except ValueError:
                continue  # malformed arguments: dropped (truncation safety)
            out.append({"verb": name, "params": args or {}})
        return out, _usage_of(completion.usage)


def _extract_json_object(text):
    """Robust JSON object extraction: direct parse → fenced block → first
    balanced {...} (strategist models sometimes wrap or prefix)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith(("json", "JSON")):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
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
        self._client = _client(url, timeout)

    def propose(self, messages):
        """→ ({"packet": dict|None, "raw": {...}, "error": str?}, usage).
        Connection failures degrade to an error result (no raise) so the
        pulse loop keeps the last program (FR-012)."""
        kwargs = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        if self.thinking is False:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False},
                "thinking": {"type": "disabled"},  # GLM-style param
            }
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            return {"packet": None, "raw": None,
                    "error": "strategist channel error: %s" % e}, {}
        message = completion.choices[0].message
        usage = _usage_of(completion.usage)
        ex = _message_extras(message)
        text, reasoning = ex["content"], ex["reasoning"]
        packet = _extract_json_object(text)
        if packet is None and reasoning:
            # GLM-thinking mode: the answer JSON sometimes surfaces only
            # in reasoning_content (content empty / prefix-trimmed)
            packet = _extract_json_object(reasoning)
        raw = {"content": text, "reasoning": reasoning,
               "reasoning_key": ex["reasoning_key"]}
        if packet is not None and not isinstance(packet, dict):
            packet = None
        if packet is None:
            return ({"packet": None, "raw": raw,
                     "error": "unparseable JSON from strategist: %r" %
                              ((text or reasoning or "")[:200])}, usage)
        return {"packet": packet, "raw": raw}, usage