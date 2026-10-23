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
            self.last_raw = None
            raise LLMError("llm endpoint unreachable: %s" % e)
        message = completion.choices[0].message
        self.last_raw = _message_extras(message)   # observability tap
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

    def propose(self, messages, tools=None):
        """S2 (GLM-5.3) streaming propose.

        - tools given → MCP-style tool-call lane (live directive
          2026-10-23): the model rambles in reasoning_content, then issues
          the umbrella `submit_program` (or per verb) tool call; deltas
          accumulate; returns ({"calls": [...], ...}, usage).
        - tools omitted → legacy freeform-content lane (kept for tests).
        Streaming both ways: reasoning may END in the packet, then the
        content repeats it — extraction handles either (content first).
        """
        kwargs = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [
                t if t.get("type") == "function" else t for t in tools]
        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            base = {"packet": None, "calls": None, "raw": None}
            return dict(base, error="strategist channel error: %s" % e), {}
        content, reasoning = [], []
        calls_acc, finish = {}, None
        usage = {}
        stream_err = None
        try:
            for chunk in stream:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    usage = _usage_of(u)
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                c0 = choices[0]
                d = getattr(c0, "delta", None)
                if d is not None:
                    c = getattr(d, "content", None)
                    if c:
                        content.append(c)
                    m = getattr(d, "model_extra", None) or {}
                    r = m.get("reasoning_content") or m.get("reasoning")
                    if r:
                        reasoning.append(r)
                    for tc in (getattr(d, "tool_calls", None) or []):
                        idx = getattr(tc, "index", 0)
                        slot = calls_acc.setdefault(
                            idx, {"verb": None, "args": []})
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            nm = getattr(fn, "name", None)
                            if nm:
                                slot["verb"] = nm
                            a = getattr(fn, "arguments", None)
                            if a:
                                slot["args"].append(a)
                f = getattr(c0, "finish_reason", None)
                if f:
                    finish = f
        except Exception as e:      # mid-stream drop: FR-012, rescue below
            stream_err = "stream interrupted: %s" % e
        if stream_err is None and finish is None and not content \
                and not reasoning and not usage and not calls_acc:
            # streaming 4xx/5xx: the SDK ends the stream SILENTLY (no
            # chunks, no raise) — classify as transport, keep FR-012 lawful
            stream_err = "strategist channel error: empty stream (transport)"
        text = "".join(content)
        reas = "".join(reasoning) or None
        calls = []
        for _i, slot in sorted(calls_acc.items()):
            args_s = "".join(slot["args"]) or "{}"
            try:
                args = json.loads(args_s)
            except (ValueError, TypeError):
                continue          # malformed accumulated arguments dropped
            calls.append({"verb": slot["verb"], "params": args})
        raw = {"content": text, "reasoning": reas,
               "reasoning_key": "reasoning_content",
               "finish_reason": finish or (stream_err and "error"),
               "completion_tokens": usage.get("completion"),
               "stream_error": stream_err, "calls": calls}
        if stream_err:
            return {"packet": None, "calls": None, "raw": raw,
                    "error": stream_err}, usage
        if tools:
            return {"packet": None, "calls": calls or None, "raw": raw,
                    "error": None if calls else "no tool calls issued"}, usage
        packet = _extract_json_object(text)
        if packet is None and reas:
            packet = _extract_json_object(reas)   # GLM rescue paths
        if packet is not None and not isinstance(packet, dict):
            packet = None
        if packet is None:
            return ({"packet": None, "calls": None, "raw": raw,
                     "error": "unparseable JSON from strategist: %r" %
                              ((text or reas or "")[:200])}, usage)
        return {"packet": packet, "calls": calls or None, "raw": raw}, usage