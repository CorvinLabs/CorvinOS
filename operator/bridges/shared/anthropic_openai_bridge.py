"""anthropic_openai_bridge.py — local translating proxy: Anthropic Messages
API <-> OpenAI Chat Completions API (ADR-0181 M3 follow-up, 2026-07-14).

Claude Code (the ``claude`` CLI) only ever speaks the Anthropic Messages API
(``POST /v1/messages``, streaming via Anthropic's own SSE event sequence).
OpenRouter and Ollama's OpenAI-compatible endpoints speak OpenAI's Chat
Completions API instead — different request/response shape, different
streaming protocol, different tool-call representation. Pointing
``ANTHROPIC_BASE_URL`` straight at either would fail immediately (wrong
request shape) even with a perfectly valid, perfectly-resolved API key.

This module is the ADR-0181 "HONEST REMAINING REQUIREMENT" seam
(``ProviderSpec.proxy_base_url``), built in rather than left as an operator's
own external LiteLLM-style deployment: a lightweight, in-process HTTP server
that translates one format to the other in both directions, including
streaming and tool use. Started lazily, on demand, per (provider, model) —
never a separate process to install/manage.

Scope (deliberately not "every possible Anthropic API feature"):
  - POST /v1/messages, streaming and non-streaming.
  - text content blocks, tool_use / tool_result blocks, system prompt.
  - stop_reason / finish_reason mapping.
  - usage token counts (best-effort; OpenAI-compatible servers vary in
    whether they report them mid-stream).
  Not implemented: vision/image content blocks, prompt caching directives,
  extended thinking blocks — none of these are load-bearing for Claude
  Code's own coding-agent loop against a text + tool-use capable backend.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

log = logging.getLogger("corvin.anthropic_openai_bridge")


# ─── Request translation: Anthropic -> OpenAI ──────────────────────────────

def _anthropic_content_to_openai_text(content: Any) -> str:
    """Flatten an Anthropic content value (string, or list of content blocks)
    into plain text for an OpenAI ``content`` string field. Tool blocks are
    handled by the caller separately (they become tool_calls / tool messages,
    not plain text)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(str(block.get("text", "")))
        # tool_use / tool_result are handled by the caller, not flattened here.
    return "".join(parts)


def _anthropic_message_to_openai(msg: dict) -> list[dict]:
    """A single Anthropic ``messages[]`` entry can expand into MULTIPLE OpenAI
    messages (an assistant turn with both text and a tool_use becomes one
    OpenAI assistant message with tool_calls; a user turn carrying a
    tool_result becomes a separate ``role: tool`` message per result)."""
    role = msg.get("role", "user")
    content = msg.get("content", "")

    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": ""}]

    if role == "assistant":
        text = _anthropic_content_to_openai_text(content)
        tool_calls = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}) or {}),
                    },
                })
        out: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            out["tool_calls"] = tool_calls
        return [out]

    # role == "user" (or anything else): text stays a user message; each
    # tool_result becomes its own role:"tool" message (OpenAI requires one
    # tool message per tool_call_id, not a bundled block).
    out_messages: list[dict] = []
    text = _anthropic_content_to_openai_text(content)
    if text:
        out_messages.append({"role": "user", "content": text})
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                result_content = _anthropic_content_to_openai_text(result_content)
            elif not isinstance(result_content, str):
                result_content = json.dumps(result_content)
            out_messages.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": result_content,
            })
    if not out_messages:
        out_messages.append({"role": "user", "content": ""})
    return out_messages


def _anthropic_tools_to_openai(tools: Any) -> list[dict] | None:
    if not isinstance(tools, list) or not tools:
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}) or {"type": "object", "properties": {}},
            },
        })
    return out or None


def _anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return None
    kind = tool_choice.get("type")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    return None


def anthropic_request_to_openai(body: dict, *, model: str) -> dict:
    """Translate an Anthropic ``POST /v1/messages`` request body into an
    OpenAI ``POST /chat/completions`` request body. ``model`` overrides
    whatever model name the client sent (Claude Code sends its own Claude
    model names — the caller substitutes the real target-provider model)."""
    messages: list[dict] = []

    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        sys_text = _anthropic_content_to_openai_text(system)
        if sys_text.strip():
            messages.append({"role": "system", "content": sys_text})

    for msg in body.get("messages", []) or []:
        if isinstance(msg, dict):
            messages.extend(_anthropic_message_to_openai(msg))

    out: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": bool(body.get("stream", False)),
    }
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    if "top_p" in body:
        out["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]

    tools = _anthropic_tools_to_openai(body.get("tools"))
    if tools:
        out["tools"] = tools
        tool_choice = _anthropic_tool_choice_to_openai(body.get("tool_choice"))
        if tool_choice is not None:
            out["tool_choice"] = tool_choice

    return out


# ─── Response translation: OpenAI -> Anthropic (non-streaming) ────────────

_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
    None: "end_turn",
}


def _openai_message_to_anthropic_content(message: dict) -> list[dict]:
    content: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for call in (message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        fn = call.get("function", {}) or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        content.append({
            "type": "tool_use",
            "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
            "name": fn.get("name", ""),
            "input": args,
        })
    if not content:
        content.append({"type": "text", "text": ""})
    return content


def openai_response_to_anthropic(body: dict, *, model: str) -> dict:
    """Translate a non-streaming OpenAI chat-completion response body into
    an Anthropic Messages API response body."""
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message", {}) or {}
    finish_reason = choice.get("finish_reason")
    usage = body.get("usage") or {}

    return {
        "id": body.get("id") or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": _openai_message_to_anthropic_content(message),
        "model": model,
        "stop_reason": _FINISH_REASON_MAP.get(finish_reason, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ─── Streaming translation: OpenAI SSE chunks -> Anthropic SSE events ─────

class AnthropicSSETranslator:
    """Stateful translator: feed it decoded OpenAI ``chat.completion.chunk``
    dicts one at a time (in arrival order); it returns the Anthropic SSE
    ``event: ...\\ndata: ...\\n\\n`` text to forward for each, tracking
    content-block indices/types (Anthropic numbers text and each tool_use as
    separate indexed content blocks; OpenAI deltas arrive as one unified
    per-choice delta with an implicit single text stream plus a list of
    partial tool_calls keyed by their own ``index``)."""

    def __init__(self, *, message_id: str, model: str) -> None:
        self._message_id = message_id
        self._model = model
        self._started = False
        self._text_block_open = False
        self._text_block_index = 0
        # tool_call openai-index -> anthropic content-block index
        self._tool_block_index: dict[int, int] = {}
        self._next_block_index = 0
        self._finish_reason: str | None = None
        self._output_tokens = 0

    def _sse(self, event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def _ensure_started(self) -> str:
        if self._started:
            return ""
        self._started = True
        return self._sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self._message_id, "type": "message", "role": "assistant",
                "content": [], "model": self._model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

    def feed(self, chunk: dict) -> str:
        out: list[str] = [self._ensure_started()]
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {}) or {}
        usage = chunk.get("usage") or {}
        if usage.get("completion_tokens") is not None:
            self._output_tokens = usage["completion_tokens"]

        text = delta.get("content")
        if text:
            if not self._text_block_open:
                self._text_block_open = True
                self._text_block_index = self._next_block_index
                self._next_block_index += 1
                out.append(self._sse("content_block_start", {
                    "type": "content_block_start", "index": self._text_block_index,
                    "content_block": {"type": "text", "text": ""},
                }))
            out.append(self._sse("content_block_delta", {
                "type": "content_block_delta", "index": self._text_block_index,
                "delta": {"type": "text_delta", "text": text},
            }))

        for call in (delta.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            oi = call.get("index", 0)
            fn = call.get("function", {}) or {}
            if oi not in self._tool_block_index:
                if self._text_block_open:
                    out.append(self._sse("content_block_stop", {
                        "type": "content_block_stop", "index": self._text_block_index,
                    }))
                    self._text_block_open = False
                ai_idx = self._next_block_index
                self._next_block_index += 1
                self._tool_block_index[oi] = ai_idx
                out.append(self._sse("content_block_start", {
                    "type": "content_block_start", "index": ai_idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        "name": fn.get("name", ""), "input": {},
                    },
                }))
            args_piece = fn.get("arguments")
            if args_piece:
                out.append(self._sse("content_block_delta", {
                    "type": "content_block_delta", "index": self._tool_block_index[oi],
                    "delta": {"type": "input_json_delta", "partial_json": args_piece},
                }))

        if choice.get("finish_reason"):
            self._finish_reason = choice["finish_reason"]

        return "".join(p for p in out if p)

    def finalize(self) -> str:
        out: list[str] = []
        if self._text_block_open:
            out.append(self._sse("content_block_stop", {
                "type": "content_block_stop", "index": self._text_block_index,
            }))
        for ai_idx in self._tool_block_index.values():
            out.append(self._sse("content_block_stop", {
                "type": "content_block_stop", "index": ai_idx,
            }))
        out.append(self._sse("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": _FINISH_REASON_MAP.get(self._finish_reason, "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": self._output_tokens},
        }))
        out.append(self._sse("message_stop", {"type": "message_stop"}))
        return "".join(out)


# ─── Outbound call to the real provider ────────────────────────────────────

def _post_json(url: str, payload: dict, *, api_key: str, timeout: float) -> Any:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed provider URL
        return json.loads(resp.read().decode("utf-8", "replace"))


def _iter_sse_lines(resp) -> Iterator[dict]:
    """Iterate a streaming OpenAI SSE response, yielding decoded JSON chunks.
    Stops (without yielding) on the terminal ``data: [DONE]`` line.

    ``resp`` is an ``http.client.HTTPResponse`` (what ``urllib.request.urlopen``
    returns) — it is itself a buffered file-like object, so ``.readline()``
    is both correct and efficient (no need to hand-roll byte-at-a-time
    buffering)."""
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


# ─── HTTP server ────────────────────────────────────────────────────────

class ProxyTarget:
    """Where a running proxy instance forwards translated requests to, and
    which real model name to substitute for whatever Claude Code asked for."""

    def __init__(self, *, chat_completions_url: str, api_key: str, model: str,
                 request_timeout: float = 120.0) -> None:
        self.chat_completions_url = chat_completions_url
        self.api_key = api_key
        self.model = model
        self.request_timeout = request_timeout


def _make_handler(target: ProxyTarget) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # Route through the module logger (never the value of any
            # request body, which would leak prompt content into stdout).
            log.debug("proxy: %s", fmt % args)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/health"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.startswith("/v1/messages"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8", "replace") or "{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_error(400, f"invalid request body: {exc}")
                return

            openai_req = anthropic_request_to_openai(body, model=target.model)
            message_id = f"msg_{uuid.uuid4().hex[:24]}"

            if openai_req.get("stream"):
                self._handle_streaming(openai_req, message_id)
            else:
                self._handle_non_streaming(openai_req, message_id)

        def _send_error(self, code: int, message: str) -> None:
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "type": "error",
                    "error": {"type": "api_error", "message": message},
                }).encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _handle_non_streaming(self, openai_req: dict, message_id: str) -> None:
            try:
                resp = _post_json(
                    target.chat_completions_url, openai_req,
                    api_key=target.api_key, timeout=target.request_timeout,
                )
            except urllib.error.HTTPError as exc:
                self._send_error(exc.code, f"upstream error: {exc.reason}")
                return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self._send_error(502, f"upstream unreachable: {exc}")
                return
            anth = openai_response_to_anthropic(resp, model=target.model)
            anth["id"] = message_id
            payload = json.dumps(anth).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _handle_streaming(self, openai_req: dict, message_id: str) -> None:
            data = json.dumps(openai_req).encode("utf-8")
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if target.api_key:
                headers["Authorization"] = f"Bearer {target.api_key}"
            req = urllib.request.Request(
                target.chat_completions_url, data=data, headers=headers, method="POST",
            )
            try:
                upstream = urllib.request.urlopen(req, timeout=target.request_timeout)  # noqa: S310
            except urllib.error.HTTPError as exc:
                self._send_error(exc.code, f"upstream error: {exc.reason}")
                return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self._send_error(502, f"upstream unreachable: {exc}")
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # No Content-Length (the body length isn't known up front) and no
            # chunked transfer-encoding (BaseHTTPRequestHandler doesn't frame
            # chunks for us) — under HTTP/1.1 that leaves "read until the
            # connection closes" as the only way the client (claude CLI's
            # own HTTP client, or urllib in tests) knows the body ended.
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            translator = AnthropicSSETranslator(message_id=message_id, model=target.model)
            try:
                with upstream:
                    for chunk in _iter_sse_lines(upstream):
                        piece = translator.feed(chunk)
                        if piece:
                            self.wfile.write(piece.encode("utf-8"))
                            self.wfile.flush()
                self.wfile.write(translator.finalize().encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client (claude CLI) disconnected — nothing to clean up

    return Handler


# ─── Lazy singleton lifecycle ──────────────────────────────────────────────

_servers: dict[str, tuple[ThreadingHTTPServer, threading.Thread]] = {}
_servers_lock = threading.Lock()


def _target_key(target: ProxyTarget) -> str:
    # A new key (new server) whenever the effective target changes (model,
    # URL, or key rotates) — cheap to spin up a fresh thread, and this way a
    # provider/key switch can never keep talking to a stale target.
    return f"{target.chat_completions_url}|{target.model}|{hash(target.api_key)}"


def ensure_proxy(target: ProxyTarget) -> str:
    """Start (or reuse) a local proxy server forwarding to *target*. Returns
    its base URL (``http://127.0.0.1:<port>``), suitable for ANTHROPIC_BASE_URL.
    Idempotent per distinct target; safe to call on every spawn."""
    key = _target_key(target)
    with _servers_lock:
        existing = _servers.get(key)
        if existing is not None:
            server, _ = existing
            return f"http://127.0.0.1:{server.server_address[1]}"

        handler_cls = _make_handler(target)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(
            target=server.serve_forever, name="anthropic-openai-proxy", daemon=True,
        )
        thread.start()
        _servers[key] = (server, thread)
        # Stale entries (old provider/key) are never actively evicted — each
        # is one idle daemon thread + listening socket, and a real install
        # switches providers rarely enough that this is not worth the
        # complexity of a reaper. A process restart clears all of them.
        port = server.server_address[1]
        log.info("anthropic_openai_bridge: started local proxy on 127.0.0.1:%d -> %s",
                  port, target.chat_completions_url)
        return f"http://127.0.0.1:{port}"


def shutdown_all() -> None:
    """Test/shutdown helper: stop every running proxy instance."""
    with _servers_lock:
        for server, thread in _servers.values():
            server.shutdown()
            thread.join(timeout=2.0)
        _servers.clear()


def chat_completions_url_for(base_url: str, model_source: str) -> str:
    """The OpenAI-compatible chat-completions endpoint for a given provider
    base_url. Ollama exposes its OpenAI-compat surface under ``/v1/``; other
    OpenAI-format providers (OpenRouter) already ship an ``/api/v1``-style
    base_url that the endpoint hangs directly off of."""
    base = base_url.rstrip("/")
    if model_source == "ollama":
        return f"{base}/v1/chat/completions"
    return f"{base}/chat/completions"
