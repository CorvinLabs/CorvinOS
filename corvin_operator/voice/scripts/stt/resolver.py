"""STT provider resolver + fallback chain.

Resolution order:

1. ``CORVIN_STT_PROVIDER=<name>`` env override → use this provider
   exclusively. No fallback. Fail-loud if unavailable.
2. Otherwise: walk ``DEFAULT_CHAIN`` in order, take the first
   provider where ``is_available()`` returns True. If none, raise
   ``STTProviderUnavailable`` aggregating the chain.

The chain default is ``openai → local`` (changed 2026-07-09): cloud Whisper
is meaningfully more accurate than any local GGML model small enough to
auto-download, so when an API key is configured it should always win.
``is_available()`` already makes ``openai`` a no-op without a key, so this
still degrades to local-only for operators with no API key, air-gapped
environments, or a data-residency policy forbidding OpenAI — no separate
"key present?" branch needed. Operators who want local-first (e.g. to avoid
sending audio off-box even when a key exists) set
``CORVIN_STT_CHAIN=local,openai`` (or pin via ``CORVIN_STT_PROVIDER=local``).

A cloud provider is used only when the turn tenant's L35 egress policy admits
its host (``egress_gate``, engine id ``stt_openai``): a denied provider is
skipped in a chain and refused when pinned. ``CORVIN_STT_CHAIN`` that is set
but names no known provider raises ``STTProviderUnavailable`` - it never
falls back to the cloud-first default.

A failed ``transcribe()`` call falls through to the next provider in
the chain for ``STTProviderUnavailable`` / ``STTTranscriptionFailed``,
and also for ``STTTimeout`` as long as another provider remains in the
chain — a blackholed/slow leading provider (e.g. ``openai``) must not
strand a healthy on-box ``local`` model. Only a timeout on the LAST
provider re-raises.

When the caller passes an explicit ``timeout_s``, that budget is a
ceiling on the WHOLE chain, not a fresh allowance per provider: the
remaining budget (original minus elapsed wall-clock time) is what gets
passed to each subsequent provider, so falling through on timeout
cannot multiply the caller's intended wait. When ``timeout_s`` is
``None`` (no caller-specified budget), each provider still falls back
to its own built-in default — there is no caller intent to violate.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

from .base import (
    STTError,
    STTProvider,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
)
from .openai_whisper import OpenAIWhisperProvider
from .local_whisper import LocalWhisperProvider


# Order matters — first available wins by default.
DEFAULT_CHAIN: tuple[str, ...] = ("openai", "local")

_PROVIDERS: dict[str, type[STTProvider]] = {
    "openai": OpenAIWhisperProvider,
    "local":  LocalWhisperProvider,
}


#: Providers that send the audio off the host, and the egress-gate engine id
#: (``egress_gate.DEFAULT_ENGINE_HOSTS``) that names where it goes.
_CLOUD_ENGINE_IDS: dict[str, str] = {"openai": "stt_openai"}
_CLOUD_FALLBACK_HOSTS: dict[str, str] = {"openai": "api.openai.com"}

_log = logging.getLogger("corvin.stt.resolver")


def _chain_from_env() -> tuple[str, ...]:
    raw = os.environ.get("CORVIN_STT_CHAIN", "")
    if not raw.strip():
        return DEFAULT_CHAIN
    names = tuple(p.strip() for p in raw.split(",") if p.strip())
    known = tuple(n for n in names if n in _PROVIDERS)
    if not known:
        # Round 3 (R3-NEW-27): a chain that was SET but names no known
        # provider used to fall back to DEFAULT_CHAIN - cloud first. An
        # operator who wrote ``CORVIN_STT_CHAIN=lokal`` to keep audio on the
        # box got every voice message sent to OpenAI instead. A chain the
        # operator set is never replaced by a different one.
        raise STTProviderUnavailable(
            f"CORVIN_STT_CHAIN names no known STT provider; known: {sorted(_PROVIDERS)}"
        )
    # An unknown name next to a known one is still skipped: the remaining
    # chain is a subset of what the operator asked for, never a superset.
    return known


def _turn_tenant(tenant_id: str | None) -> str:
    return tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"


def _cloud_egress_refusal(name: str, tenant_id: str | None) -> str | None:
    """L35 check before audio leaves the host (R3-NEW-6).

    ``None`` when provider *name* may be used for this tenant, else a short
    reason. Local providers are never asked. A tenant without an egress policy
    has no gate (``load_egress_gate_for_tenant`` answers ``None``) and passes,
    exactly like every engine spawn. A gate that cannot be loaded or raises
    anything but a decision refuses the cloud provider: the on-box model is the
    fallback, and "the check broke" must not read as "allowed".
    """
    engine_id = _CLOUD_ENGINE_IDS.get(name)
    if engine_id is None:
        return None
    try:
        try:
            import egress_gate as _eg  # type: ignore[import-not-found]
        except ImportError:
            shared = Path(__file__).resolve().parents[3] / "bridges" / "shared"
            if str(shared) not in sys.path:
                sys.path.insert(0, str(shared))
            import egress_gate as _eg  # type: ignore[import-not-found]
        gate = _eg.load_egress_gate_for_tenant(_turn_tenant(tenant_id))
        if gate is None:
            return None
        host = _eg.DEFAULT_ENGINE_HOSTS.get(engine_id) or _CLOUD_FALLBACK_HOSTS[name]
        try:
            gate.validate_or_raise(host, engine_id=engine_id)
        except _eg.EgressDenied:
            return "egress policy denies the provider host"
        return None
    except Exception as exc:  # noqa: BLE001 - fail closed for the cloud provider
        _log.warning("STT egress check for %s failed (%s); provider not used",
                     name, type(exc).__name__)
        return f"egress check unavailable ({type(exc).__name__})"


def available_providers() -> list[str]:
    """Names of providers whose ``is_available()`` returns True now."""
    out: list[str] = []
    for name in _PROVIDERS:
        try:
            if _PROVIDERS[name]().is_available():
                out.append(name)
        except Exception:  # noqa: BLE001
            # Defensive: probing should never crash the resolver.
            continue
    return out


def provider_status() -> dict[str, dict]:
    """Structured per-provider status for the Console voice-status panel
    (ADR-0185 M4, Decision 4).

    Cheap introspection only — NEVER calls ``transcribe()``, NEVER raises.
    Goes a bit further than ``is_available()`` so the UI can show *why* a
    provider isn't ready (missing package vs. missing model file vs. no
    API key) instead of a flat true/false. Each entry:

      ready:              bool       — usable right now (mirrors is_available())
      package_installed:  bool       — underlying package imports
      model_present:      bool|None  — local model file on disk (None: n/a)
      key_configured:     bool|None  — API key resolvable (None: n/a)
      detail:             str        — short, human-readable, non-leaky status

    Never includes raw exception text or internal chain/failure strings —
    those must never reach the UI (ADR-0185 Must-NOT).
    """
    status: dict[str, dict] = {}

    # -- local (pywhispercpp / opt-in faster-whisper) --
    try:
        from .local_whisper import (
            _default_local_model,
            _faster_whisper_importable,
            _models_dir,
            _prefer_faster_whisper,
        )

        if _prefer_faster_whisper() and _faster_whisper_importable():
            status["local"] = {
                "ready": True,
                "package_installed": True,
                "model_present": None,
                "key_configured": None,
                "detail": "faster-whisper engine active (CORVIN_STT_LOCAL_ENGINE override)",
            }
        else:
            try:
                import pywhispercpp.model  # noqa: F401
                package_installed = True
            except ImportError:
                package_installed = False
            model_name = os.environ.get("CORVIN_STT_LOCAL_MODEL", "").strip() or _default_local_model()
            model_path = _models_dir() / f"ggml-{model_name}.bin"
            model_present = model_path.exists() and model_path.stat().st_size > 0
            ready = package_installed and model_present
            if ready:
                detail = f"ready (model {model_name!r})"
            elif not package_installed:
                detail = "pywhispercpp not installed"
            else:
                detail = f"model {model_name!r} not downloaded yet"
            status["local"] = {
                "ready": ready,
                "package_installed": package_installed,
                "model_present": model_present,
                "key_configured": None,
                "detail": detail,
            }
    except Exception as exc:  # noqa: BLE001 — status probe must never crash
        status["local"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": None,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    # -- openai --
    try:
        from .openai_whisper import _resolve_api_key

        key_configured = bool(_resolve_api_key())
        try:
            import openai  # noqa: F401
            package_installed = True
        except ImportError:
            package_installed = False
        ready = key_configured and package_installed
        if ready:
            detail = "ready"
        elif not key_configured:
            detail = "no API key configured"
        else:
            detail = "openai package not installed"
        status["openai"] = {
            "ready": ready,
            "package_installed": package_installed,
            "model_present": None,
            "key_configured": key_configured,
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001
        status["openai"] = {
            "ready": False,
            "package_installed": False,
            "model_present": None,
            "key_configured": False,
            "detail": f"status probe failed ({exc.__class__.__name__})",
        }

    return status


def resolve(name: str | None = None, *, tenant_id: str | None = None) -> STTProvider:
    """Pick a provider, honouring env override + chain defaults.

    Raises ``STTProviderUnavailable`` if no provider in the chain is
    available. Callers that need a specific provider pass *name*
    explicitly (same semantics as the env override).

    ``tenant_id`` is the tenant of the turn whose audio is transcribed; its
    L35 egress policy decides whether a cloud provider may be picked. Without
    it the process tenant (``CORVIN_TENANT_ID``, else ``_default``) is used.
    """
    explicit = name or os.environ.get("CORVIN_STT_PROVIDER")
    if explicit:
        cls = _PROVIDERS.get(explicit)
        if cls is None:
            raise STTProviderUnavailable(
                f"unknown STT provider {explicit!r}; "
                f"known: {sorted(_PROVIDERS)}"
            )
        refusal = _cloud_egress_refusal(explicit, tenant_id)
        if refusal:
            raise STTProviderUnavailable(
                f"STT provider {explicit!r} is not permitted: {refusal}. "
                f"No fallback because the provider was pinned."
            )
        instance = cls()
        if not instance.is_available():
            raise STTProviderUnavailable(
                f"STT provider {explicit!r} is not available "
                f"(check API key / deps / model). "
                f"No fallback because CORVIN_STT_PROVIDER pinned it."
            )
        return instance

    chain = _chain_from_env()
    failures: list[str] = []
    for n in chain:
        cls = _PROVIDERS.get(n)
        if cls is None:
            continue
        refusal = _cloud_egress_refusal(n, tenant_id)
        if refusal:
            failures.append(f"{n}: {refusal}")
            continue
        try:
            instance = cls()
            if instance.is_available():
                return instance
            failures.append(f"{n}: not available")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{n}: {exc}")
    raise STTProviderUnavailable(
        f"no STT provider available; chain={chain}; failures={failures}"
    )


def transcribe(
    audio_path: Path,
    *,
    lang: str | None = None,
    timeout_s: float | None = None,
    provider: str | None = None,
    tenant_id: str | None = None,
) -> TranscriptResult:
    """High-level entry: resolve + call + fall through to next on failure.

    ``provider=<name>`` pins the choice and disables fallback (mirror
    of the env-override semantics in ``resolve``).

    ``tenant_id``: the tenant of the turn (see :func:`resolve`). A cloud
    provider the tenant's L35 egress policy denies is skipped - the chain
    continues with the next (local) provider - and is refused outright when
    it was pinned.
    """
    explicit = provider or os.environ.get("CORVIN_STT_PROVIDER")
    if explicit:
        # Pinned — no fallback by contract.
        p = resolve(explicit, tenant_id=tenant_id)
        return p.transcribe(audio_path, lang=lang, timeout_s=timeout_s)

    chain = _chain_from_env()
    failures: list[str] = []
    last_error: Optional[Exception] = None
    start = time.monotonic()
    for idx, n in enumerate(chain):
        cls = _PROVIDERS.get(n)
        if cls is None:
            continue
        refusal = _cloud_egress_refusal(n, tenant_id)
        if refusal:
            failures.append(f"{n}: {refusal}")
            continue
        try:
            instance = cls()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{n}: init failed: {exc}")
            last_error = exc
            continue
        if not instance.is_available():
            failures.append(f"{n}: not available")
            continue
        # An explicit caller budget is a ceiling on the WHOLE chain: charge
        # elapsed wall-clock time against it so a fallthrough after an earlier
        # provider's timeout can't multiply the caller's intended wait (e.g.
        # BRIDGE_TRANSCRIBE_TIMEOUT=30 must not turn into up to 60s across a
        # 2-provider chain). No explicit budget (timeout_s is None) → each
        # provider keeps using its own built-in default; there's no caller
        # intent to violate in that case.
        if timeout_s is None:
            provider_timeout = None
        else:
            provider_timeout = timeout_s - (time.monotonic() - start)
            if provider_timeout <= 0:
                failures.append(f"{n}: skipped — chain timeout budget exhausted")
                last_error = last_error or STTTimeout(
                    f"chain timeout budget ({timeout_s}s) exhausted before {n}"
                )
                continue
        try:
            return instance.transcribe(
                audio_path, lang=lang, timeout_s=provider_timeout,
            )
        except STTTimeout as exc:
            # A timeout on the LAST provider is terminal — don't multiply the
            # user's wait by falling through to nothing. But when a provider
            # earlier in the chain times out (e.g. the default chain now leads
            # with "openai", and a blackholed/slow endpoint burns the budget),
            # we MUST still try the remaining local provider instead of hard-
            # failing STT while a healthy on-box model sits unused.
            failures.append(f"{n}: timeout")
            last_error = exc
            if idx == len(chain) - 1:
                raise
            continue
        except (STTProviderUnavailable, STTTranscriptionFailed) as exc:
            failures.append(f"{n}: {exc}")
            last_error = exc
            continue
    if last_error is not None:
        raise STTProviderUnavailable(
            f"all STT providers exhausted; chain={chain}; "
            f"failures={failures}"
        ) from last_error
    raise STTProviderUnavailable(
        f"no STT provider available; chain={chain}; failures={failures}"
    )
