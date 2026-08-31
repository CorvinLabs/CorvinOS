"""test_anthropic_openai_bridge.py — Anthropic<->OpenAI translation + local
proxy server (ADR-0181 follow-up: the translating proxy Claude Code needs to
actually talk to OpenRouter/Ollama, not just have a correctly-resolved key).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import anthropic_openai_bridge as bridge  # type: ignore


# ─── Request translation ───────────────────────────────────────────────────

def test_simple_text_request_translates():
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 100,
        "system": "You are helpful.",
        "messages": [{"role": "user", "content": "Hello there"}],
    }
    out = bridge.anthropic_request_to_openai(body, model="qwen3:8b")
    assert out["model"] == "qwen3:8b"
    assert out["max_tokens"] == 100
    assert out["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert out["messages"][1] == {"role": "user", "content": "Hello there"}


def test_content_block_list_request_translates():
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "part one"}, {"type": "text", "text": " part two"}]},
        ],
    }
    out = bridge.anthropic_request_to_openai(body, model="m")
    assert out["messages"][0]["content"] == "part one part two"


def test_assistant_tool_use_becomes_tool_calls():
    body = {
        "messages": [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Berlin"}},
            ]},
        ],
    }
    out = bridge.anthropic_request_to_openai(body, model="m")
    msg = out["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Let me check."
    assert msg["tool_calls"] == [{
        "id": "toolu_1", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Berlin"}'},
    }]


def test_tool_result_becomes_tool_role_message():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "17 degrees, sunny"},
            ]},
        ],
    }
    out = bridge.anthropic_request_to_openai(body, model="m")
    assert out["messages"][0] == {
        "role": "tool", "tool_call_id": "toolu_1", "content": "17 degrees, sunny",
    }


def test_tools_and_tool_choice_translate():
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "get_weather"},
    }
    out = bridge.anthropic_request_to_openai(body, model="m")
    assert out["tools"] == [{
        "type": "function",
        "function": {"name": "get_weather", "description": "Get weather", "parameters": {"type": "object"}},
    }]
    assert out["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}


def test_tool_choice_auto_and_any():
    assert bridge._anthropic_tool_choice_to_openai({"type": "auto"}) == "auto"
    assert bridge._anthropic_tool_choice_to_openai({"type": "any"}) == "required"


def test_stop_sequences_and_temperature_pass_through():
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "stop_sequences": ["STOP"],
        "temperature": 0.3,
    }
    out = bridge.anthropic_request_to_openai(body, model="m")
    assert out["stop"] == ["STOP"]
    assert out["temperature"] == 0.3


def test_stream_flag_passed_through():
    out = bridge.anthropic_request_to_openai({"messages": [], "stream": True}, model="m")
    assert out["stream"] is True
    out2 = bridge.anthropic_request_to_openai({"messages": []}, model="m")
    assert out2["stream"] is False


def test_streaming_request_asks_for_usage():
    """Without stream_options.include_usage, most OpenAI-compatible servers
    omit `usage` from every streamed chunk, so token-usage accounting always
    reports 0 for streamed turns (adversarial review, 2026-07-14)."""
    out = bridge.anthropic_request_to_openai({"messages": [], "stream": True}, model="m")
    assert out["stream_options"] == {"include_usage": True}
    out2 = bridge.anthropic_request_to_openai({"messages": []}, model="m")
    assert "stream_options" not in out2


# ─── Response translation (non-streaming) ──────────────────────────────────

def test_plain_text_response_translates():
    body = {
        "id": "chatcmpl-1",
        "choices": [{"message": {"role": "assistant", "content": "Hi there!"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    out = bridge.openai_response_to_anthropic(body, model="claude-sonnet-5")
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert out["content"] == [{"type": "text", "text": "Hi there!"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 10, "output_tokens": 3}


def test_tool_call_response_translates():
    body = {
        "choices": [{
            "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "get_weather", "arguments": '{"city": "Berlin"}'}}],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    out = bridge.openai_response_to_anthropic(body, model="m")
    assert out["content"] == [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Berlin"}}]
    assert out["stop_reason"] == "tool_use"


def test_length_finish_reason_maps_to_max_tokens():
    body = {"choices": [{"message": {"content": "..."}, "finish_reason": "length"}], "usage": {}}
    out = bridge.openai_response_to_anthropic(body, model="m")
    assert out["stop_reason"] == "max_tokens"


# ─── Streaming translation ──────────────────────────────────────────────────

def test_streaming_text_only():
    t = bridge.AnthropicSSETranslator(message_id="msg_1", model="m")
    pieces = []
    pieces.append(t.feed({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}))
    pieces.append(t.feed({"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]}))
    pieces.append(t.feed({"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]}))
    pieces.append(t.feed({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    tail = t.finalize()
    full = "".join(pieces) + tail

    assert "event: message_start" in full
    assert full.count("event: content_block_start") == 1
    assert full.count('"text_delta"') == 2
    assert "event: content_block_stop" in full
    assert '"stop_reason": "end_turn"' in full
    assert "event: message_stop" in full


def test_streaming_tool_call():
    t = bridge.AnthropicSSETranslator(message_id="msg_2", model="m")
    pieces = []
    pieces.append(t.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": ""}},
    ]}, "finish_reason": None}]}))
    pieces.append(t.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": '{"city":'}},
    ]}, "finish_reason": None}]}))
    pieces.append(t.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": '"Berlin"}'}},
    ]}, "finish_reason": None}]}))
    pieces.append(t.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
    full = "".join(pieces) + t.finalize()

    assert '"type": "tool_use"' in full
    assert '"name": "get_weather"' in full
    assert full.count('"input_json_delta"') == 2
    assert '"stop_reason": "tool_use"' in full


def test_streaming_text_then_tool_call_gets_two_content_blocks():
    t = bridge.AnthropicSSETranslator(message_id="msg_3", model="m")
    out = []
    out.append(t.feed({"choices": [{"delta": {"content": "Checking..."}, "finish_reason": None}]}))
    out.append(t.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}},
    ]}, "finish_reason": None}]}))
    out.append(t.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
    full = "".join(out) + t.finalize()
    # text block (index 0) must be explicitly closed before the tool_use
    # block (index 1) opens — Anthropic requires sequential, non-overlapping
    # content-block lifecycles.
    text_stop_pos = full.index('"content_block_stop", "index": 0')
    tool_start_pos = full.index('"content_block_start", "index": 1')
    assert text_stop_pos < tool_start_pos


def test_finalize_alone_emits_message_start():
    """Regression (adversarial review, 2026-07-14): if the upstream SSE
    stream never yields a single parseable chunk (empty response, upstream
    closes early), finalize() must still emit message_start before
    message_delta/message_stop -- otherwise the client sees a malformed
    Anthropic event sequence."""
    t = bridge.AnthropicSSETranslator(message_id="msg_4", model="m")
    full = t.finalize()
    assert full.index("event: message_start") < full.index("event: message_delta")
    assert "event: message_stop" in full


# ─── chat_completions_url_for ───────────────────────────────────────────────

def test_ollama_url_uses_v1_prefix():
    assert bridge.chat_completions_url_for("http://localhost:11434", "ollama") == \
        "http://localhost:11434/v1/chat/completions"


def test_openrouter_url_appends_directly():
    assert bridge.chat_completions_url_for("https://openrouter.ai/api/v1", "openrouter") == \
        "https://openrouter.ai/api/v1/chat/completions"


# ─── End-to-end: real HTTP server, mocked upstream ─────────────────────────

class _FakeUpstreamNonStreaming(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        assert body["model"] == "qwen3:8b"
        resp = {
            "id": "chatcmpl-fake",
            "choices": [{"message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        payload = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_fake_upstream(handler_cls) -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t, f"http://127.0.0.1:{server.server_address[1]}"


def test_proxy_end_to_end_non_streaming():
    upstream, u_thread, upstream_url = _start_fake_upstream(_FakeUpstreamNonStreaming)
    try:
        target = bridge.ProxyTarget(
            chat_completions_url=f"{upstream_url}/chat/completions",
            api_key="test-key", model="qwen3:8b",
        )
        base = bridge.ensure_proxy(target)

        req_body = json.dumps({
            "model": "claude-sonnet-5", "max_tokens": 100,
            "messages": [{"role": "user", "content": "What is 6*7?"}],
        }).encode("utf-8")
        req = urllib.request.Request(f"{base}/v1/messages", data=req_body,
                                      headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        assert out["type"] == "message"
        assert out["content"] == [{"type": "text", "text": "42"}]
        assert out["stop_reason"] == "end_turn"

        # Idempotent: calling ensure_proxy again with the SAME target reuses
        # the running server instead of starting a second one.
        base2 = bridge.ensure_proxy(target)
        assert base2 == base
    finally:
        bridge.shutdown_all()
        upstream.shutdown()
        u_thread.join(timeout=2)


class _FakeUpstreamStreaming(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ("Hel", "lo"):
            chunk = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()
        final = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(final)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def test_proxy_end_to_end_streaming():
    upstream, u_thread, upstream_url = _start_fake_upstream(_FakeUpstreamStreaming)
    try:
        target = bridge.ProxyTarget(
            chat_completions_url=f"{upstream_url}/chat/completions",
            api_key="test-key", model="qwen3:8b",
        )
        base = bridge.ensure_proxy(target)

        req_body = json.dumps({
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }).encode("utf-8")
        req = urllib.request.Request(f"{base}/v1/messages", data=req_body,
                                      headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
        assert "event: message_start" in raw
        assert raw.count('"text_delta"') == 2
        assert "event: message_stop" in raw
    finally:
        bridge.shutdown_all()
        upstream.shutdown()
        u_thread.join(timeout=2)


class _FakeOllamaCapturingThink(BaseHTTPRequestHandler):
    captured: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).captured["think"] = body.get("think")
        resp = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        payload = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_disable_reasoning_sends_think_false_for_ollama():
    """qwen3-style thinking models otherwise spend real latency generating a
    separate 'reasoning' field even when the visible answer is what's wanted
    (verified live against a real running Ollama during development) — the
    same class of issue already fixed for Hermes/summarize.py's calls."""
    _FakeOllamaCapturingThink.captured = {}
    upstream, u_thread, upstream_url = _start_fake_upstream(_FakeOllamaCapturingThink)
    try:
        target = bridge.ProxyTarget(
            chat_completions_url=f"{upstream_url}/chat/completions",
            api_key="", model="qwen3:8b", disable_reasoning=True,
        )
        base = bridge.ensure_proxy(target)
        req_body = json.dumps({
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        req = urllib.request.Request(f"{base}/v1/messages", data=req_body,
                                      headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5):
            pass
        assert _FakeOllamaCapturingThink.captured.get("think") is False
    finally:
        bridge.shutdown_all()
        upstream.shutdown()
        u_thread.join(timeout=2)


def test_no_disable_reasoning_omits_think_field():
    target = bridge.ProxyTarget(
        chat_completions_url="http://example.invalid/chat/completions",
        api_key="", model="claude-sonnet-5", disable_reasoning=False,
    )
    assert target.disable_reasoning is False


def test_target_key_distinguishes_disable_reasoning():
    """Regression (adversarial review, 2026-07-14): the proxy cache key
    previously omitted disable_reasoning, so a second ensure_proxy() call
    for the same url/model/key but a different disable_reasoning silently
    reused the wrong cached server instead of starting a fresh one."""
    target_a = bridge.ProxyTarget(
        chat_completions_url="http://example.invalid/chat/completions",
        api_key="k", model="m", disable_reasoning=False,
    )
    target_b = bridge.ProxyTarget(
        chat_completions_url="http://example.invalid/chat/completions",
        api_key="k", model="m", disable_reasoning=True,
    )
    try:
        base_a = bridge.ensure_proxy(target_a)
        base_b = bridge.ensure_proxy(target_b)
        assert base_a != base_b, "distinct disable_reasoning must get distinct proxy instances"
    finally:
        bridge.shutdown_all()


class _FakeUpstreamStallsMidStream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
        self.wfile.flush()
        time.sleep(2.0)  # stall past the client's short request_timeout


def test_streaming_upstream_stall_closes_gracefully_instead_of_hanging():
    """Regression (adversarial review, 2026-07-14): the streaming forward
    loop only caught BrokenPipeError/ConnectionResetError, not a TimeoutError
    from a stalled upstream read -- so a provider that stopped responding
    mid-stream without closing the connection left the client hanging with
    zero feedback (the same failure class task #73 fixed for the tool-call
    layer, here at the proxy layer)."""
    upstream, u_thread, upstream_url = _start_fake_upstream(_FakeUpstreamStallsMidStream)
    try:
        target = bridge.ProxyTarget(
            chat_completions_url=f"{upstream_url}/chat/completions",
            api_key="test-key", model="qwen3:8b", request_timeout=0.3,
        )
        base = bridge.ensure_proxy(target)

        req_body = json.dumps({
            "messages": [{"role": "user", "content": "hi"}], "stream": True,
        }).encode("utf-8")
        req = urllib.request.Request(f"{base}/v1/messages", data=req_body,
                                      headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
        elapsed = time.monotonic() - t0
        assert elapsed < 1.5, f"must not wait for the full 2s stall — took {elapsed:.2f}s"
        assert "event: message_start" in raw
        assert "event: message_stop" in raw
    finally:
        bridge.shutdown_all()
        upstream.shutdown()
        u_thread.join(timeout=2)


def test_health_endpoint():
    target = bridge.ProxyTarget(chat_completions_url="http://example.invalid/chat/completions",
                                api_key="", model="m")
    base = bridge.ensure_proxy(target)
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
            assert resp.status == 200
    finally:
        bridge.shutdown_all()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
