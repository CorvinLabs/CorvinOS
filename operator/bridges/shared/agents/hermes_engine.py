"""HermesEngine — wraps local Ollama models via the Ollama HTTP API.

Integrates locally-running Ollama models (default: qwen3:8b) as a fourth
WorkerEngine in the Corvin L22 layer.

Unlike the other three engines (ClaudeCode, Codex, OpenCode) Hermes
does NOT spawn a subprocess — it drives Ollama's local HTTP streaming
API (localhost:11434/api/chat). This makes it the only engine with
locality=local and network_egress=none for L34 data-classification
purposes, qualifying it for CONFIDENTIAL task classes without a
compliance-zone exception.

Ollama API contract used:
    POST /api/chat   {"model": "...", "messages": [...], "stream": true}
    Response: NDJSON lines, one per token chunk
    Final line: {"done": true, "eval_count": N, "prompt_eval_count": N}

Event mapping:
    POST connect OK           -> session_started  (model name in raw)
    content chunk (non-empty) -> text_delta
    done=true                 -> turn_completed   (usage from eval_count)
    URLError / socket error   -> error

Design constraints (MUST NOT do):
    - MUST NOT import anthropic (CI AST lint enforces, ADR-0066)
    - MUST NOT spawn a subprocess (HTTP avoids shell-injection surface
      on model names or prompts — ADR-0066 §Must NOT do)
    - MUST NOT hard-code base_url — configurable for docker-network
      scenarios (Ollama in sidecar container)
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from . import StreamEvent, parse_jsonl_line


# ---------------------------------------------------------------------------
# Model alias table
# ---------------------------------------------------------------------------

HERMES_MODEL_ALIASES: dict[str, str] = {
    "hermes-fast":     "qwen3:1.7b",
    "hermes-balanced": "qwen3:8b",
    "hermes-capable":  "qwen3:8b",
    "hermes-large":    "qwen3:8b",
}

# Default model when neither caller nor constructor specifies one.
# Override via CORVIN_HERMES_MODEL env var.
_DEFAULT_MODEL = "qwen3:8b"


def _resolve_default_model() -> str:
    """Resolution order:
    1. CORVIN_HERMES_MODEL env var (direct Ollama tag or alias)
    2. Built-in default: "qwen3:8b"
    """
    env = os.environ.get("CORVIN_HERMES_MODEL", "").strip()
    if env:
        return HERMES_MODEL_ALIASES.get(env, env)
    return _DEFAULT_MODEL


def _resolve_base_url() -> str:
    """Resolution order:
    1. CORVIN_OLLAMA_BASE_URL env var
    2. OLLAMA_HOST env var (Ollama's own convention)
    3. Default http://localhost:11434
    """
    for env_key in ("CORVIN_OLLAMA_BASE_URL", "OLLAMA_HOST"):
        v = os.environ.get(env_key, "").strip()
        if v:
            return v.rstrip("/")
    return "http://localhost:11434"


def _ping_ollama(base_url: str, timeout: float = 3.0) -> bool:
    """True if the Ollama HTTP API answers."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:  # noqa: BLE001
        return False


def _import_ensure_ollama_running():
    """Locate ensure_ollama_running across install layouts (installed package,
    bridges-shared on sys.path, or the operator path)."""
    for mod in ("corvin_console.hermes_bootstrap", "hermes_bootstrap",
                "operator.bridges.shared.hermes_bootstrap"):
        try:
            return __import__(mod, fromlist=["ensure_ollama_running"]).ensure_ollama_running
        except Exception:  # noqa: BLE001
            continue
    return None


def ensure_hermes_ready(base_url: str, model: str, timeout: float = 120.0) -> tuple[bool, str]:
    """Make Hermes actually usable at call time — the fix for the post-install
    "hermes connect error: timed out" on Windows/macOS (no systemd healing there):

      1. If the Ollama server isn't reachable, START it (desktop app on Windows,
         `ollama serve` on POSIX) and wait for the HTTP API.
      2. WARM the model (load it into RAM via /api/generate with keep_alive) using
         a GENEROUS timeout, so the subsequent streaming request — clamped to a
         short per-read timeout so cancel() stays responsive — doesn't time out on
         the one-time cold model load (which takes 20-60 s for a multi-GB model).

    Cheap when already warm (keep_alive holds the model loaded). Returns (ok, detail).

    Timeout budget: this warm-up runs synchronously BEFORE the first StreamEvent,
    so its total silent window must stay under the adapter's stream-idle watchdog
    (``stream_idle_to`` = 300 s). We bound it: server-start wait ≤ 30 s + warm-read
    ≤ 200 s = 240 s worst case, leaving a 60 s margin so a genuinely cold load does
    NOT trip the idle timeout and error the turn. This bound also caps the window
    in which a mid-warm-up cancel() is unresponsive (self._response is still None).
    """
    if not _ping_ollama(base_url, 3.0):
        starter = _import_ensure_ollama_running()
        if starter is not None:
            try:
                starter()
            except Exception:  # noqa: BLE001
                pass
        deadline = time.time() + 30.0
        while time.time() < deadline and not _ping_ollama(base_url, 2.0):
            time.sleep(1.0)
        if not _ping_ollama(base_url, 2.0):
            return False, (f"Ollama server not reachable at {base_url} and could not be "
                           f"started — launch the Ollama app (or run `ollama serve`).")
    # Preload the model so the first real request returns tokens promptly.
    try:
        warm = json.dumps({"model": model, "keep_alive": "30m"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/generate", data=warm,
            headers={"Content-Type": "application/json"}, method="POST")
        # Ceiling 200 s (not caller's inf): keep total warm-up < 300 s idle watchdog.
        with urllib.request.urlopen(req, timeout=max(60.0, min(timeout, 200.0))) as r:
            r.read()
        return True, "ready"
    except urllib.error.HTTPError as e:  # model likely not pulled
        return False, (f"Hermes model '{model}' is not available ({e.code}) — "
                       f"run: ollama pull {model}")
    except Exception as e:  # noqa: BLE001
        return False, f"Hermes model '{model}' could not be loaded: {e}"


# ---------------------------------------------------------------------------
# HermesEngine
# ---------------------------------------------------------------------------


class HermesEngine:
    """WorkerEngine wrapping NousResearch Hermes via local Ollama HTTP API.

    Capabilities (M1):
      stream_json=True        — NDJSON events emitted per token chunk
      mcp=False               — M1; full MCP bridge in M3
      mid_stream_inject=False — Ollama HTTP has no streaming-stdin entry
      hooks=False             — no filesystem hook system
      skills_tool=False       — M1; prompt-inject planned for M3
      permission_modes=["read_only"] — HTTP API has no fs-write concept
      session_pinning=False   — Ollama has no session-resume equivalent
    """

    name = "hermes"

    capabilities: dict[str, Any] = {
        "mid_stream_inject": "buffered",  # M2: buffered /btw queue for next turn
        "hooks":             "teb_brokered",  # M4: synthetic hooks via TEB
        "skills":            "system_message",  # M3: skill as HTTP JSON system role
        "skills_tool":       False,
        "mcp":               False,
        "stream_json":       True,
        "permission_modes":  ["read_only"],
        "add_system_prompt": True,
        "version_flag":      None,
        "session_pinning":   False,
    }

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model: str = (
            HERMES_MODEL_ALIASES.get(model, model)
            if model
            else _resolve_default_model()
        )
        self.base_url: str = (
            base_url.rstrip("/") if base_url else _resolve_base_url()
        )
        self._cancel = threading.Event()
        self._response: Any = None  # http.client.HTTPResponse during spawn
        self._temperature: float | None = None  # set via /e:temp; None = Ollama default

    # ------------------------------------------------------------------
    # WorkerEngine Protocol
    # ------------------------------------------------------------------

    def spawn(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        working_dir: Path | None = None,
        timeout: float = 120.0,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        tools: list[dict] | None = None,
        tool_executor: Any | None = None,
        session_dir: Path | None = None,  # M2: for buffered /btw dequeue
    ) -> Iterator[StreamEvent]:
        """Spawn a Hermes turn via Ollama /api/chat (streaming).

        ADR-0069 M2 — FCB tool-use loop:
        When ``tools`` is provided (list of OpenAI-format function defs from
        the FCB) and ``tool_executor`` is callable, the engine enters a
        tool-use loop: model emits tool_calls → executor runs them → results
        appended as "tool" messages → model continues until done=true with no
        further tool_calls.

        M2: If ``session_dir`` is provided, check for buffered /btw injections
        and prepend to user prompt.

        ``working_dir``, ``extra_args``, and ``env`` are accepted for
        protocol compatibility but unused — the engine drives Ollama HTTP,
        not a subprocess.
        """
        self._cancel.clear()

        # M2: Check for buffered /btw injections from prior turns
        user_prompt = prompt
        if session_dir:
            try:
                from eci.transport_buffered import dequeue_all_injections
                buffered = dequeue_all_injections(session_dir)
                if buffered:
                    user_prompt = buffered + "\n\n" + prompt
            except ImportError:
                pass

        effective_model = (
            HERMES_MODEL_ALIASES.get(model, model) if model else self.model
        )

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_prompt})

        payload_dict: dict[str, Any] = {
            "model":    effective_model,
            "messages": messages,
            "stream":   True,
        }
        if tools:
            payload_dict["tools"] = tools
        # Apply temperature set via /e:temp (None = Ollama default, omit field)
        if self._temperature is not None:
            payload_dict["options"] = {"temperature": self._temperature}
        payload = json.dumps(payload_dict).encode("utf-8")

        url = f"{self.base_url}/api/chat"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start_time = time.time()

        # Ensure Ollama is running AND the model is warm BEFORE the streaming
        # request — otherwise a cold model load (20-60 s for a multi-GB model)
        # blows past the short per-read socket timeout below and surfaces as the
        # opaque "hermes connect error: timed out" (esp. on Windows/macOS, which
        # have no systemd healing to keep the server up). Cheap when already warm.
        _ready, _detail = ensure_hermes_ready(self.base_url, effective_model, timeout)
        if not _ready:
            yield StreamEvent(type="error", error=f"hermes not ready: {_detail}")
            return

        # Per-read socket timeout clamped to 10 s so cancel() takes effect
        # within that window even when Ollama is between tokens.
        socket_timeout = min(timeout, 10.0)

        try:
            self._response = urllib.request.urlopen(req, timeout=socket_timeout)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            yield StreamEvent(
                type="error",
                error=f"ollama HTTP {e.code} at {self.base_url}: {body}",
            )
            return
        except urllib.error.URLError as e:
            yield StreamEvent(
                type="error",
                error=f"ollama unavailable at {self.base_url}: {e.reason}",
            )
            return
        except OSError as e:
            yield StreamEvent(type="error", error=f"hermes connect error: {e}")
            return

        yield StreamEvent(
            type="session_started",
            raw={"model": effective_model},
        )

        accumulated: list[str] = []
        usage: dict[str, Any] = {}

        # ADR-0069 M2 — FCB tool-use loop.
        # Outer loop: each iteration is one Ollama HTTP round-trip.
        # Exits when the model finishes without requesting tool calls,
        # or after MAX_TOOL_ROUNDS to prevent runaway loops.
        MAX_TOOL_ROUNDS = 8
        _fcb_imports_ok = False
        mcp_result_to_openai_message = None
        openai_call_to_mcp_call = None
        if tools and tool_executor is not None:
            try:
                from teb.fcb import (  # type: ignore[import]
                    mcp_result_to_openai_message,
                    openai_call_to_mcp_call,
                )
                _fcb_imports_ok = True
            except ImportError:
                pass

        try:
            for _tool_round in range(MAX_TOOL_ROUNDS):
                pending_tool_calls: list[dict] = []

                for raw_line in self._response:
                    if self._cancel.is_set():
                        yield StreamEvent(type="error", error="hermes engine cancelled")
                        return

                    if time.time() - start_time > timeout:
                        yield StreamEvent(type="error", error="hermes stream timeout")
                        return

                    obj = parse_jsonl_line(raw_line)
                    if obj is None:
                        continue

                    if obj.get("error"):
                        yield StreamEvent(
                            type="error",
                            error=f"ollama error: {obj['error']}",
                            raw=obj,
                        )
                        return

                    msg_obj = obj.get("message") or {}
                    if msg_obj.get("tool_calls"):
                        pending_tool_calls = msg_obj["tool_calls"]

                    chunk = msg_obj.get("content") or ""
                    if chunk:
                        accumulated.append(chunk)
                        yield StreamEvent(type="text_delta", text=chunk, raw=obj)

                    if obj.get("done"):
                        usage = {
                            "input_tokens":  obj.get("prompt_eval_count", 0),
                            "output_tokens": obj.get("eval_count", 0),
                        }
                        break  # inner for loop done; check pending_tool_calls below

                # No tool calls or FCB unavailable — we are done
                if not pending_tool_calls or not _fcb_imports_ok or tool_executor is None:
                    break

                # Execute tool calls, append results, re-POST
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": pending_tool_calls,
                })
                for tc in pending_tool_calls:
                    call = openai_call_to_mcp_call(tc)  # type: ignore[misc]
                    yield StreamEvent(
                        type="tool_call",
                        text=call["name"],
                        raw={"name": call["name"], "args": call["arguments"]},
                    )
                    try:
                        tool_result = tool_executor(call["name"], call["arguments"])
                    except Exception as exc:  # noqa: BLE001
                        tool_result = f"tool error: {exc}"
                    yield StreamEvent(
                        type="tool_result",
                        text=str(tool_result),
                        raw={"name": call["name"], "result": str(tool_result)},
                    )
                    messages.append(mcp_result_to_openai_message(tool_result))  # type: ignore[misc]

                payload_dict["messages"] = messages
                new_payload = json.dumps(payload_dict).encode("utf-8")
                try:
                    self._response.close()
                except Exception:  # noqa: BLE001
                    pass
                self._response = None
                new_req = urllib.request.Request(
                    url, data=new_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    self._response = urllib.request.urlopen(new_req, timeout=socket_timeout)
                except Exception as exc:  # noqa: BLE001
                    yield StreamEvent(type="error", error=f"hermes tool re-spawn: {exc}")
                    return
                # loop back to next _tool_round

        except OSError as e:
            if self._cancel.is_set():
                yield StreamEvent(type="error", error="hermes engine cancelled")
            else:
                yield StreamEvent(type="error", error=f"hermes stream IO error: {e}")
            return
        except Exception as e:  # noqa: BLE001
            yield StreamEvent(type="error", error=f"hermes stream error: {e}")
            return
        finally:
            try:
                if self._response is not None:
                    self._response.close()
            except Exception:  # noqa: BLE001
                pass
            self._response = None

        yield StreamEvent(
            type="turn_completed",
            text="".join(accumulated),
            usage=usage,
        )

    def cancel(self) -> None:
        """Cancel an in-flight spawn(). Sets a threading.Event and closes
        the HTTP response to interrupt the blocking readline sooner than
        the socket_timeout fires.
        """
        self._cancel.set()
        resp = self._response
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # ECI native-command handlers (ADR-0069 M6)
    # ------------------------------------------------------------------

    @property
    def command_manifest(self):  # type: ignore[return]
        from eci import EngineCommandManifest, NativeCommandSpec
        return EngineCommandManifest(
            mid_stream_inject="buffered",
            cancel="http_delete",
            compact=None,
            native_commands={
                "model": NativeCommandSpec(
                    description="Switch Ollama model",
                    handler_method="eci_set_model",
                    usage="<model-name|alias>",
                ),
                "temp": NativeCommandSpec(
                    description="Set sampling temperature (0.0–2.0)",
                    handler_method="eci_set_temp",
                    usage="<float>",
                ),
                "ctx": NativeCommandSpec(
                    description="Show context window info",
                    handler_method="eci_show_ctx",
                ),
            },
        )

    def eci_set_model(self, args: str) -> "CommandResult":  # type: ignore[name-defined]
        from eci import CommandResult
        new_model = args.strip()
        if not new_model:
            return CommandResult(success=False, message="✗ /e:model: Modell-Name fehlt")
        resolved = HERMES_MODEL_ALIASES.get(new_model, new_model)
        old_model = self.model
        self.model = resolved
        return CommandResult(
            success=True,
            message=f"🔄 Modell gewechselt: {old_model} → {resolved}",
        )

    def eci_set_temp(self, args: str) -> "CommandResult":  # type: ignore[name-defined]
        from eci import CommandResult
        val = args.strip()
        try:
            temp = float(val)
        except ValueError:
            return CommandResult(success=False, message=f"✗ /e:temp: '{val}' ist keine Zahl")
        if not 0.0 <= temp <= 2.0:
            return CommandResult(success=False, message=f"✗ /e:temp: {temp} außerhalb [0.0, 2.0]")
        self._temperature = temp
        return CommandResult(success=True, message=f"🌡 Temperatur gesetzt: {temp}")

    def eci_show_ctx(self, _args: str) -> "CommandResult":  # type: ignore[name-defined]
        from eci import CommandResult
        temp_str = str(self._temperature) if self._temperature is not None else "default (Ollama)"
        return CommandResult(
            success=True,
            message=(
                f"📊 Hermes-Engine — Modell: {self.model} | "
                f"Temperatur: {temp_str} | "
                f"Ollama: {self.base_url}"
            ),
        )


__all__ = ["HermesEngine", "HERMES_MODEL_ALIASES"]
