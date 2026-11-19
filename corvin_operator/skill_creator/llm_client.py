"""LLM client resolution for Skill-Creator — Max-subscription first (ADR-0363).

Why this module exists
----------------------
Every Skill-Creator phase used to call ``anthropic.Anthropic()`` directly.
On an install without ``ANTHROPIC_API_KEY`` that constructor raises

    Could not resolve authentication method. Expected one of api_key,
    auth_token, or credentials to be set. …

which surfaced in the console as a FAILED run in the Planning phase.
CorvinOS installs authenticate through the **Claude Code CLI** (Max
subscription OAuth in ``~/.claude``), not through a raw API key, so the
default path must be the CLI — the same engine the console web-chat, ACS
runtime, TDE workers and the house-rules gate already drive.

Resolution order (``resolve_llm_client``):
  1. ``ClaudeCodeClient``  — ``claude -p --output-format json`` subprocess
     (Max subscription / whatever the CLI is logged in as). Default.
  2. ``anthropic.Anthropic`` — only when ``ANTHROPIC_API_KEY`` is set AND
     ``CORVIN_SKILL_CREATOR_ENGINE=api`` explicitly selects it.
  3. ``None``              — callers degrade to local template generation.

``ClaudeCodeClient`` is duck-typed against the slice of the Anthropic SDK
the Skill-Creator uses — ``client.messages.create(model=…, max_tokens=…,
messages=[…])`` returning an object with ``.content[0].text`` — so the
phase code and its unit-test mocks stay unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── shared `claude -p` prompt guard (ADR-0648 / adversarial review R4) ─────
# The claude CLI expands `/`, `!` and `#` at byte 0 and `@<path>` ANYWHERE in a
# prompt — client-side, BEFORE the model runs, so no tool policy, permission
# mode or `--disallowedTools` restricts them. Every payload this module hands
# to the CLI therefore goes through the ONE shared neutraliser. The fallback is
# a RAISING stub, never a pass-through: an unimportable guard refuses the
# spawn, it never downgrades it to an unguarded one.
try:  # pragma: no cover - import shape
    import os as _pg_os, sys as _pg_sys
    _pg_d = _pg_os.path.dirname(_pg_os.path.abspath(__file__))
    while _pg_d != _pg_os.path.dirname(_pg_d):
        _pg_c = _pg_os.path.join(_pg_d, "corvin_operator", "bridges", "shared")
        if _pg_os.path.isdir(_pg_c):
            if _pg_c not in _pg_sys.path:
                _pg_sys.path.insert(0, _pg_c)
            break
        _pg_d = _pg_os.path.dirname(_pg_d)
    from prompt_guard import guard_prompt_head as _guard_prompt_head  # type: ignore
except Exception:  # pragma: no cover - guard unavailable => refuse, never bypass
    def _guard_prompt_head(_text):  # type: ignore[misc]
        raise RuntimeError(
            "shared claude-CLI prompt guard unavailable "
            "(operator/bridges/shared/prompt_guard.py) - refusing to build an "
            "unguarded `claude -p` payload"
        )

# Engine selector: "claude_code" (default) | "api" | "local"

ENGINE_ENV = "CORVIN_SKILL_CREATOR_ENGINE"
MODEL_ENV = "CORVIN_SKILL_CREATOR_MODEL"
TIMEOUT_ENV = "CORVIN_SKILL_CREATOR_TIMEOUT_S"

#: The Opus FLOOR - what a generation call runs on only when the model lineage cannot be read.
#: The model a call actually asks for is :func:`default_model` (the Console's COMPLEX choice,
#: else the newest Opus; review PLS-2c). The phase code still names this literal in its
#: ``messages.create(model=...)`` calls; the CLI client reads it as "the top tier", never as a
#: pin to this exact version.
DEFAULT_MODEL = "claude-opus-5"
_SPAN_TURN_ID = "skill_creator"


def default_model(tenant_id: str | None = None) -> str:
    """The model a Skill-Creator generation asks for when nothing pins one: the tenant's
    Console COMPLEX choice, else the newest Opus (``model_selector.top_model``). The lineage is
    read per call, so a newer Opus is picked up without a restart. ``DEFAULT_MODEL`` only when
    the selector cannot be imported or answers nothing."""
    try:
        import model_selector as _ms  # type: ignore  # noqa: PLC0415  (bridges/shared, see above)
        chosen = _ms.top_model(tenant_id)
        chosen = _ms.normalise_pin(chosen, "claude_code") or chosen
        if isinstance(chosen, str) and chosen.strip():
            return chosen.strip()
    except Exception as exc:  # noqa: BLE001 - never break a generation on a routing lookup
        logger.warning("Skill-Creator: model selector unavailable (%s), using the floor %s",
                       type(exc).__name__, DEFAULT_MODEL)
    return DEFAULT_MODEL


def _pinned_model() -> str | None:
    """The operator's ``CORVIN_SKILL_CREATOR_MODEL`` pin in the form the CLI accepts
    (a ``provider/`` prefix is never valid CLI input), or None when unset."""
    raw = (os.environ.get(MODEL_ENV) or "").strip()
    if not raw:
        return None
    try:
        import model_selector as _ms  # type: ignore  # noqa: PLC0415
        return _ms.normalise_pin(raw, "claude_code") or raw
    except Exception:  # noqa: BLE001 - a pin this process cannot verify passes through
        return raw.split("/", 1)[1] if "/" in raw else raw


def _process_tenant() -> str:
    return (os.environ.get("CORVIN_TENANT_ID") or "").strip() or "_default"


def _worker_env(tenant_id: str) -> dict[str, str]:
    """The environment of a generation's ``claude -p`` (review PLS-2b): THE worker rule,
    ``acs_runtime.build_worker_env`` - deny-by-default, minus every secret, plus the engine's
    own credential / platform chain and the tenant's provider egress. Until 2026-11-19 the
    child inherited the console's whole process environment, tokens included.

    Raises :class:`ClaudeCodeUnavailable` when the helper cannot be imported: refusing the
    spawn, never falling back to the full environment."""
    try:
        from acs_runtime import build_worker_env  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ClaudeCodeUnavailable(
            f"worker env strip unavailable ({type(exc).__name__}) - refusing to spawn the "
            "Skill-Creator engine with the operator's environment"
        ) from exc
    env = build_worker_env(tenant_id)
    env["CORVIN_TENANT_ID"] = tenant_id
    return env


def _span_writer(tenant_id: str):
    def _write(event_type: str, *, details: dict[str, Any], severity: str = "INFO",
               **_routing: Any) -> None:
        import forge.paths as _fp  # type: ignore  # noqa: PLC0415
        import forge.security_events as _se  # type: ignore  # noqa: PLC0415
        _se.write_event(_fp.tenant_audit_chain(tenant_id), event_type,
                        details=details, severity=severity)
    return _write


def _span_start(tenant_id: str, requested_model: str, *, engine_id: str = "claude_code") -> str:
    """Open the ``engine.span`` of one generation call; '' when it cannot be written.
    Measurement only: a failing write is logged and the call proceeds."""
    try:
        import engine_span as _es  # type: ignore  # noqa: PLC0415
        span_id = _es.new_span_id()
        _es.emit_start(_span_writer(tenant_id), span_id=span_id, role="worker",
                       engine_id=engine_id, model_id=requested_model,
                       turn_id=_SPAN_TURN_ID, tenant_id=tenant_id)
        return span_id
    except Exception as exc:  # noqa: BLE001
        logger.error("Skill-Creator: engine.span.start not written (%s)", type(exc).__name__)
        return ""


def _span_end(tenant_id: str, span_id: str, *, reported_model: str, usage: dict[str, Any] | None,
              status: str, t0: float, engine_id: str = "claude_code") -> None:
    """Close the span on the model the CLI REPORTED ('' when it reported none - unpriced,
    honest; never the requested one) with the four-way token split from the ONE shared
    normaliser (ADR-0759)."""
    if not span_id:
        return
    try:
        import engine_span as _es  # type: ignore  # noqa: PLC0415
        _es.emit_end(_span_writer(tenant_id), span_id=span_id, role="worker",
                     engine_id=engine_id, model_id=reported_model or "",
                     status=status, duration_ms=int((time.perf_counter() - t0) * 1000),
                     turn_id=_SPAN_TURN_ID, tenant_id=tenant_id, **_es.usage_split(usage))
    except Exception as exc:  # noqa: BLE001
        logger.error("Skill-Creator: engine.span.end not written (%s)", type(exc).__name__)


def _reported_model(env: dict[str, Any]) -> str:
    """The model the CLI's envelope reports: the ``modelUsage`` entry with the most output
    tokens (a turn may list a helper model beside the one that answered). '' when absent."""
    per_model = env.get("modelUsage")
    if not isinstance(per_model, dict) or not per_model:
        return ""

    def _out(item: tuple[str, Any]) -> int:
        v = item[1].get("outputTokens", 0) if isinstance(item[1], dict) else 0
        return v if isinstance(v, int) and not isinstance(v, bool) else 0
    return str(max(per_model.items(), key=_out)[0])
# ADR-2094: raised 180 -> 300 (refine calls on long skills exceeded 180s).
# Raised again 300 -> 600 (2026-10-05): the Tool-Forge fix loop resends the
# full draft (impl up to MAX_IMPL_CHARS=64KiB) on every retry and `max_tokens`
# is not enforced on the Claude Code CLI path, so a single call's duration
# scales with draft size, not request complexity. Still an env override, not
# a cap on generation time — see ADR-2094 Amendments for the open structural
# follow-up (diff-based fixes instead of full-draft regeneration).
DEFAULT_TIMEOUT_S = 600.0

_BIN_FALLBACKS = (
    "/home/linuxbrew/.linuxbrew/bin/claude",
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
)


class ClaudeCodeUnavailable(RuntimeError):
    """Raised when the `claude` CLI cannot be located or invoked."""


# --------------------------------------------------------------------------
# Per-spawn gate (review R2-BR-9)
# --------------------------------------------------------------------------

class SpawnGateRefused(RuntimeError):
    """An engine call was refused before any gate ran: it carries no text a gate could
    judge. A gate that is handed an empty string has nothing to refuse, so "no text" must
    be a refusal itself and never an allow (review R3-BR-3)."""


def _require_gate_text(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise SpawnGateRefused(
            "Generation refused: the engine call carries no text the safety gates could judge.")
    return text


def run_gated(gate: "LatchedSpawnGate", fn: Callable[[], Any]) -> Any:
    """Run one generation (``fn``) under ``gate`` and surface the gate's refusal whatever
    the phase code did with it: wrapped in its own error (a planning error) or swallowed
    (a dropped reviewer). A generation that returns after a refused call is not a result."""
    try:
        result = fn()
    except BaseException as exc:
        if gate.refusal is not None and exc is not gate.refusal:
            raise gate.refusal from exc
        raise
    if gate.refusal is not None:
        raise gate.refusal
    return result


class LatchedSpawnGate:
    """A pre-spawn gate that is run before EVERY engine call of one generation and latches
    its first refusal.

    One generation makes about a dozen engine calls (plan, repair, LDD iterations, three
    reviewers); only the first one's text is known before the generation starts, the rest
    carries what the model wrote in between. ``gate(text)`` raises to refuse. After the first
    refusal every further call raises the same exception without running the gate again or
    reaching the engine: the phase code catches engine errors in places (a dropped reviewer,
    a wrapped planning error), and a refusal must end the whole generation, not one phase.
    The caller reads :attr:`refusal` when the generation returns or raises."""

    def __init__(self, gate: Callable[[str], None]) -> None:
        self._gate = gate
        self._lock = threading.Lock()
        self.refusal: BaseException | None = None
        self.calls = 0

    def __call__(self, text: str) -> None:
        with self._lock:
            if self.refusal is not None:
                raise self.refusal
            self.calls += 1
        try:
            self._gate(_require_gate_text(text))
        except BaseException as exc:
            with self._lock:
                if self.refusal is None:
                    self.refusal = exc
            raise


def _gate_flatten(messages: Any) -> str:
    """Every piece of text an engine call's ``messages`` carries, for the gate. Unlike
    ``ClaudeCodeClient._flatten`` (what the CLI is sent) this drops nothing: a content
    block that is not plain text is judged as its JSON form."""
    if isinstance(messages, (str, bytes)):
        return messages.decode("utf-8", "replace") if isinstance(messages, bytes) else messages
    if isinstance(messages, dict):
        messages = [messages]
    parts: list[str] = []
    for msg in messages or []:
        if isinstance(msg, str):
            parts.append(msg)
            continue
        if not isinstance(msg, dict):
            parts.append(str(msg))
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            blocks: list[str] = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    blocks.append(block["text"])
                elif isinstance(block, str):
                    blocks.append(block)
                else:
                    try:
                        blocks.append(json.dumps(block, default=str, sort_keys=True))
                    except (TypeError, ValueError):
                        blocks.append(str(block))
            content = "\n".join(blocks)
        role = msg.get("role", "user")
        parts.append(str(content) if role == "user" else f"[{role}]\n{content}")
    return "\n\n".join(p for p in parts if p)


def _gate_text(messages: Any, system: Any) -> str:
    """The text one engine call sends, as the gate judges it: the flattened messages plus
    the system prompt."""
    prompt = _gate_flatten(messages)
    if isinstance(system, (list, dict)):
        system = _gate_flatten([{"role": "user", "content": system if isinstance(system, list)
                                 else [system]}])
    return prompt + ("\n\n" + str(system) if system else "")


def _call_text(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Gate text of one ``messages.create(*args, **kwargs)`` call. The messages may arrive
    by keyword OR positionally (an injected client is duck-typed, not the SDK); reading
    the keyword only gated the empty string for a positional caller (review R3-BR-3)."""
    messages = kwargs.get("messages")
    system = kwargs.get("system")
    extra: list[str] = []
    for arg in args:
        if isinstance(arg, (list, tuple)) or (isinstance(arg, dict) and "content" in arg):
            if messages is None:
                messages = list(arg) if isinstance(arg, (list, tuple)) else [arg]
            else:
                extra.append(_gate_flatten(arg))
        elif isinstance(arg, str):
            extra.append(arg)
    for key in ("prompt", "input", "text"):
        if isinstance(kwargs.get(key), str):
            extra.append(kwargs[key])
    text = _gate_text(messages, system)
    return "\n\n".join(p for p in [text, *extra] if p)


class _GatedMessages:
    def __init__(self, inner: Any, gate: Callable[[str], None]) -> None:
        self._inner, self._gate = inner, gate

    def create(self, *args: Any, **kwargs: Any) -> Any:
        self._gate(_require_gate_text(_call_text(args, kwargs)))
        return self._inner.messages.create(*args, **kwargs)


class GatedClient:
    """A client of unknown type (an injected one) behind a spawn gate: ``messages.create``
    runs the gate on the call's text first. Everything else is the wrapped client's."""

    def __init__(self, inner: Any, gate: Callable[[str], None]) -> None:
        self._inner = inner
        self.spawn_gate = gate
        self.messages = _GatedMessages(inner, gate)

    @property
    def engine_id(self) -> Any:
        return getattr(self._inner, "engine_id", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def gated_client(client: Any, gate: Callable[[str], None] | None) -> Any:
    """``client`` with ``gate`` run before every engine call. None stays None (local template
    generation spawns nothing); the two clients of this module take the gate natively (it
    then runs before the span opens and before the child is started); any other client is
    wrapped."""
    if client is None or gate is None:
        return client
    if isinstance(client, (ClaudeCodeClient, ApiSpanClient)):
        client.spawn_gate = gate
        return client
    if isinstance(client, GatedClient):
        return GatedClient(client._inner, gate)
    return GatedClient(client, gate)


def resolve_claude_bin(binary: str | None = None) -> str:
    """Locate the `claude` binary. Honours CORVIN_CLAUDE_BIN (canonical env)."""
    candidate = binary or os.environ.get("CORVIN_CLAUDE_BIN") or "claude"
    if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which(candidate)
    if found:
        return found
    # A service unit's PATH often lacks ~/.local/bin where the npm global
    # install lands; probe the usual locations before giving up.
    home_local = Path.home() / ".local" / "bin" / "claude"
    for path in (str(home_local), *_BIN_FALLBACKS):
        if os.access(path, os.X_OK):
            return path
    raise ClaudeCodeUnavailable(
        f"claude binary not found (tried {candidate!r}, PATH, ~/.local/bin, "
        f"{list(_BIN_FALLBACKS)}). Install Claude Code or set CORVIN_CLAUDE_BIN."
    )


# --------------------------------------------------------------------------
# Anthropic-SDK-shaped response objects (duck-typed)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class LLMResponse:
    """Minimal stand-in for ``anthropic.types.Message``."""
    content: list[_TextBlock]
    model: str = DEFAULT_MODEL
    usage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
    #: The model the engine REPORTED for this call ('' when it reported none). ``model`` keeps
    #: its SDK meaning and falls back to the requested id; a span is closed on this field.
    reported_model: str = ""

    @property
    def text(self) -> str:
        return self.content[0].text if self.content else ""


class _Messages:
    """The ``client.messages`` namespace."""

    def __init__(self, client: "ClaudeCodeClient") -> None:
        self._client = client

    def create(self, *, model: str | None = None, max_tokens: int | None = None,
               messages: list[dict[str, Any]] | None = None,
               system: str | None = None, **_ignored: Any) -> LLMResponse:
        return self._client._create(
            model=model, max_tokens=max_tokens, messages=messages or [], system=system
        )


class ClaudeCodeClient:
    """Anthropic-SDK-shaped client backed by the `claude -p` CLI.

    Uses the operator's Claude Code login (Max subscription) — no API key.
    Each call is a single-turn, tool-free completion in a neutral scratch
    cwd, so a skill-generation prompt can never touch the repo.
    """

    engine_id = "claude_code"

    def __init__(self, *, binary: str | None = None, model: str | None = None,
                 timeout_s: float | None = None, tenant_id: str | None = None,
                 spawn_gate: Callable[[str], None] | None = None) -> None:
        self.binary = resolve_claude_bin(binary)
        #: Run on the exact text of every call before anything is spawned; raises to refuse
        #: (review R2-BR-9). None = the caller gated the request itself.
        self.spawn_gate = spawn_gate
        #: The tenant whose audit chain receives this client's spans, whose worker env the
        #: child gets and whose Console model choice applies. The caller names it (the
        #: orchestrators pass the authenticated session's tenant); the process tenant only
        #: when nobody does.
        self.tenant_id = (tenant_id or "").strip() or _process_tenant()
        #: An explicit pin: the constructor argument, else CORVIN_SKILL_CREATOR_MODEL.
        self._pin = (model or "").strip() or _pinned_model()
        if timeout_s is None:
            try:
                timeout_s = float(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
            except (TypeError, ValueError):
                timeout_s = DEFAULT_TIMEOUT_S
        self.timeout_s = timeout_s
        self.messages = _Messages(self)

    @property
    def model(self) -> str:
        """The model this client asks for: its pin, else :func:`default_model` (read now)."""
        return self._pin or default_model(self.tenant_id)

    def _resolve_model(self, requested: str | None) -> str:
        """What ``--model`` gets. A pin wins. No request, or the phase code's legacy literal
        (``DEFAULT_MODEL``), means "the top tier" and follows the lineage; any other requested
        id is the caller's explicit choice and is passed in the form the CLI accepts."""
        if self._pin:
            return self._pin
        req = (requested or "").strip()
        if not req or req == DEFAULT_MODEL:
            return default_model(self.tenant_id)
        try:
            import model_selector as _ms  # type: ignore  # noqa: PLC0415
            return _ms.normalise_pin(req, self.engine_id) or req
        except Exception:  # noqa: BLE001
            return req

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _flatten(messages: list[dict[str, Any]]) -> str:
        """Collapse an Anthropic ``messages`` list into one prompt string."""
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            role = msg.get("role", "user")
            parts.append(str(content) if role == "user" else f"[{role}]\n{content}")
        return "\n\n".join(p for p in parts if p)

    def _create(self, *, model: str | None, max_tokens: int | None,
                messages: list[dict[str, Any]], system: str | None) -> LLMResponse:
        prompt = self._flatten(messages)
        if not prompt.strip():
            raise ValueError("empty prompt")

        # R2-BR-9: the gate sees what THIS call sends - before the env is built, before the
        # span opens, before the child exists. A refusal leaves no span and no spawn.
        if self.spawn_gate is not None:
            self.spawn_gate(prompt + ("\n\n" + system if system else ""))

        use_model = self._resolve_model(model)
        argv = [
            # R4: whole-payload guard - `_flatten(messages)` concatenates the
            # operator's skill brief with prior turns. POSITIONAL argv. Reply
            # contract unaffected: the JSON envelope is parsed from stdout.
            self.binary, "-p", _guard_prompt_head(prompt),
            "--model", use_model,
            "--output-format", "json",
            "--max-turns", "1",
            # Skill generation is pure text synthesis: no file access, no
            # shell, no MCP. Keeps a generated prompt from reaching the FS.
            "--disallowedTools", "*",
        ]
        if system:
            argv.extend(["--append-system-prompt", system])

        # PLS-2b: deny-by-default worker env, built BEFORE the span opens - a refused spawn
        # has no span to close.
        child_env = _worker_env(self.tenant_id)

        # PLS-2a: every generation call is a priceable worker span on the caller's tenant
        # chain. Until 2026-11-19 this path spawned the top-tier model with no span at all,
        # so Skill-/Tool-/Plugin-Creator spend was invisible to model_usage and cost.
        t0 = time.perf_counter()
        span_id = _span_start(self.tenant_id, use_model)
        reported, usage, status = "", {}, "error"
        try:
            with tempfile.TemporaryDirectory(prefix="skill-creator-") as neutral_cwd:
                try:
                    proc = subprocess.run(
                        argv,
                        # Without stdin=DEVNULL the child inherits the service's
                        # stdin and `claude -p` blocks ~3s polling it on EVERY
                        # call (see llm_synthesis.py for the same finding).
                        stdin=subprocess.DEVNULL,
                        cwd=neutral_cwd,
                        env=child_env,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout_s, encoding="utf-8", errors="replace",
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ClaudeCodeUnavailable(
                        f"claude CLI timed out after {self.timeout_s}s"
                    ) from exc
                except OSError as exc:
                    raise ClaudeCodeUnavailable(f"claude CLI could not be spawned: {exc}") from exc

            # A failed call can still have been billed: read what the envelope reports
            # before deciding the outcome, so the span carries it either way.
            reported, usage = self._envelope_usage(proc.stdout)

            if proc.returncode != 0:
                tail = (proc.stderr or "").strip()[-500:]
                raise ClaudeCodeUnavailable(
                    f"claude CLI exited {proc.returncode}: {tail or '(no stderr)'}"
                )

            response = self._parse_envelope(proc.stdout, fallback_model=use_model)
            status = "ok"
            return response
        finally:
            _span_end(self.tenant_id, span_id, reported_model=reported, usage=usage,
                      status=status, t0=t0)

    @staticmethod
    def _envelope_usage(stdout: str | None) -> tuple[str, dict[str, Any]]:
        """``(reported_model, usage)`` of a CLI envelope; ``("", {})`` for anything else."""
        try:
            env = json.loads((stdout or "").strip())
        except (json.JSONDecodeError, ValueError, TypeError):
            return "", {}
        if not isinstance(env, dict):
            return "", {}
        usage = env.get("usage") if isinstance(env.get("usage"), dict) else {}
        return _reported_model(env), usage

    @staticmethod
    def _parse_envelope(stdout: str, *, fallback_model: str) -> LLMResponse:
        """Unwrap a `claude -p --output-format json` envelope.

        The CLI returns rc=0 with ``is_error: true`` for soft failures
        (max-turns hit, upstream API error). Those MUST surface as an
        exception — recording the error text as the phase's output would
        feed a bogus skill spec into the next phase.
        """
        raw = (stdout or "").strip()
        if not raw:
            raise ClaudeCodeUnavailable("claude CLI returned empty output")
        try:
            env = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Older CLI / plain-text reply: treat stdout as the answer.
            return LLMResponse(content=[_TextBlock(text=raw)], model=fallback_model)
        if not isinstance(env, dict):
            return LLMResponse(content=[_TextBlock(text=raw)], model=fallback_model)
        if env.get("is_error"):
            raise ClaudeCodeUnavailable(
                f"claude CLI error ({env.get('subtype') or 'unknown'}): "
                f"{str(env.get('result') or '')[:300]}"
            )
        text = env.get("result", "")
        if not isinstance(text, str):
            text = json.dumps(text)
        reported = _reported_model(env)
        usage = env.get("usage") if isinstance(env.get("usage"), dict) else {}
        return LLMResponse(
            content=[_TextBlock(text=text)],
            model=reported or fallback_model,
            usage=usage,
            stop_reason=env.get("stop_reason"),
            reported_model=reported,
        )


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

def resolve_llm_client(explicit=None, *, tenant_id: str | None = None):
    """Return the LLM client the Skill-Creator should use.

    Args:
        explicit: caller-injected client (tests, DI) — returned unchanged.
        tenant_id: the tenant the generation runs for (audit chain of its spans, worker env,
            Console model choice). None = the process tenant.

    Returns:
        A client exposing ``.messages.create(...)``, or ``None`` when no
        engine is reachable (callers degrade to local template generation).
    """
    if explicit is not None:
        return explicit

    engine = (os.environ.get(ENGINE_ENV) or "claude_code").strip().lower()

    if engine == "local":
        logger.info("Skill-Creator engine=local (explicit) — template generation")
        return None

    if engine == "api":
        return _api_client(tenant_id) or _claude_code_client(tenant_id)

    # Default: Max subscription via the Claude Code CLI.
    return _claude_code_client(tenant_id) or _api_client(tenant_id)


def _claude_code_client(tenant_id: str | None = None):
    try:
        client = ClaudeCodeClient(tenant_id=tenant_id)
        logger.info(
            "Skill-Creator engine=claude_code (Max subscription) bin=%s model=%s",
            client.binary, client.model,
        )
        return client
    except ClaudeCodeUnavailable as exc:
        logger.warning("Claude Code CLI unavailable: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never break generation on probe
        logger.warning("Claude Code client construction failed: %s", exc)
        return None


def _sdk_usage(resp: Any) -> dict[str, Any]:
    """The SDK response's usage object as the plain dict ``engine_span.usage_split`` reads."""
    usage = getattr(resp, "usage", None)
    if isinstance(usage, dict):
        return usage
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"):
        val = getattr(usage, key, None)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = int(val)
    return out


class _ApiMessages:
    def __init__(self, client: "ApiSpanClient") -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create(**kwargs)


class ApiSpanClient:
    """The Anthropic SDK client behind the same rules as the CLI client (review R2-BR-10).

    The raw ``anthropic.Anthropic`` object used to be handed to the phase code as it was:
    every call ran on the phase code's literal ``DEFAULT_MODEL`` (a fixed version), wrote no
    ``engine.span`` - so API-path spend was invisible to model_usage and priced at nothing -
    and passed no gate. This wrapper resolves the model like :class:`ClaudeCodeClient`
    (pin, else the lineage's top tier), opens and closes a priceable worker span on the
    caller's tenant chain (closed on the model the API REPORTED, four-way split from the one
    shared normaliser) and runs the spawn gate first."""

    engine_id = "api"

    def __init__(self, sdk_client: Any, *, tenant_id: str | None = None,
                 spawn_gate: Callable[[str], None] | None = None) -> None:
        self._sdk = sdk_client
        self.tenant_id = (tenant_id or "").strip() or _process_tenant()
        self._pin = _pinned_model()
        self.spawn_gate = spawn_gate
        self.messages = _ApiMessages(self)

    @property
    def model(self) -> str:
        return self._pin or default_model(self.tenant_id)

    def _resolve_model(self, requested: str | None) -> str:
        if self._pin:
            return self._pin
        req = (requested or "").strip()
        if not req or req == DEFAULT_MODEL:
            return default_model(self.tenant_id)
        return req.split("/", 1)[1] if "/" in req else req

    def _create(self, **kwargs: Any) -> Any:
        if self.spawn_gate is not None:
            self.spawn_gate(_require_gate_text(_call_text((), kwargs)))
        use_model = self._resolve_model(kwargs.pop("model", None))
        t0 = time.perf_counter()
        span_id = _span_start(self.tenant_id, use_model, engine_id=self.engine_id)
        reported, usage, status = "", {}, "error"
        try:
            resp = self._sdk.messages.create(model=use_model, **kwargs)
            _rm = getattr(resp, "model", None)
            reported = _rm if isinstance(_rm, str) else ""
            usage = _sdk_usage(resp)
            status = "ok"
            return resp
        finally:
            _span_end(self.tenant_id, span_id, reported_model=reported, usage=usage,
                      status=status, t0=t0, engine_id=self.engine_id)


def _api_client(tenant_id: str | None = None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # noqa: PLC0415
        logger.info("Skill-Creator engine=api (ANTHROPIC_API_KEY)")
        return ApiSpanClient(anthropic.Anthropic(api_key=api_key), tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic API client construction failed: %s", exc)
        return None


def engine_id_of(client) -> str:
    """Human-readable engine id for status/audit surfaces.

    Always a str: the value is serialised into the run status, and a client
    object that exposes a non-string `engine_id` (a bare Mock, a proxy) made
    the whole status endpoint 500 on a run that had actually succeeded.
    """
    if client is None:
        return "local"
    engine = getattr(client, "engine_id", None)
    return engine if isinstance(engine, str) and engine else "api"
