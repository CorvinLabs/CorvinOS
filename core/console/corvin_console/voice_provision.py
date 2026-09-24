"""Runtime provisioning of the offline (Piper) voice for the operator's language.

Before this existed, changing Settings → Voice → Display language downloaded
nothing: the installer fetched ONE model (the install-time locale), and every
other language either spoke through that wrong-language model or fell silent
the moment the network tiers were unreachable — while the status panel said
"ready", because it checked for ANY model on disk.

Three triggers call ``provision()``; all return immediately (the download runs
on a daemon thread in ``corvinOS.shared.voice_models``):
  * ``PUT /profile`` when ``display_language`` changes        (trigger=language_change)
  * ``GET /voice/status`` when the current language has no
    usable model — self-healing, rate-limited                  (trigger=self_heal)
  * ``POST /voice/provision`` — the panel's retry button       (trigger=manual)

Every finished download is audited through the existing, already-allowlisted
``console.action_performed`` / ``console.action_failed`` events
(action=``voice.model_provision``, target_kind=``voice_model``, target_id=<lang>).
The failure reason is a fixed category, never the exception text.
"""
from __future__ import annotations

import logging
import threading
import time

from . import audit as console_audit

_log = logging.getLogger(__name__)

# One failed self-heal attempt per language per this many seconds: a machine
# that is offline must not re-download on every 30 s status poll.
_SELF_HEAL_BACKOFF_S = 600.0
_last_self_heal: dict[str, float] = {}
_lock = threading.Lock()

try:  # the corvinOS package ships in the same install; guard anyway
    from corvinOS.shared import voice_models as _vm
except Exception:  # noqa: BLE001
    _vm = None
    _log.warning("voice_models unavailable — offline voices cannot be provisioned", exc_info=True)


def available() -> bool:
    return _vm is not None


def normalise(lang: str | None) -> str:
    return _vm.normalise_lang(lang) if _vm else (lang or "").split("-")[0].lower()


def status(lang: str | None) -> dict:
    if _vm is None:
        return {"lang": normalise(lang), "state": "unavailable"}
    return _vm.language_status(lang or "en")


def _reason(job: dict) -> str:
    err = (job.get("error") or "").lower()
    if "engine" in err:
        return "engine-install-failed"
    if "incomplete" in err:
        return "incomplete-download"
    if "no offline voice" in err:
        return "no-offline-voice"
    return "download-failed"


def _egress_denied(tenant_id: str) -> str | None:
    """Ask the L35 egress gate whether the model host may be contacted — the
    same gate/semantics as routes/voice.py::_openai_tts_base_url_rejected:
    fail-OPEN when no gate is configured, honour an EXPLICIT denial. The
    download ships no user content (it fetches a public model), so
    CORVIN_TTS_LOCAL_ONLY does not apply; an egress lockdown does."""
    from urllib.parse import urlparse  # noqa: PLC0415
    host = urlparse(_vm.HF_BASE).hostname or ""
    try:
        from egress_gate import load_egress_gate_for_tenant  # type: ignore[import]  # noqa: PLC0415
        gate = load_egress_gate_for_tenant(tenant_id or "_default")
        if gate is None:
            return None
        gate.validate_or_raise(host, engine_id="voice_model_download")
    except ImportError:
        return None
    except Exception:  # noqa: BLE001 — explicit denial
        return host
    return None


def provision(lang: str | None, *, tenant_id: str, sid_fingerprint: str, trigger: str) -> dict:
    """Start (or report) the download of *lang*'s offline voice. Never raises."""
    if _vm is None or not lang:
        return status(lang)
    st = status(lang)
    if st.get("state") in ("ready", "online_only", "queued", "downloading"):
        return st
    blocked = _egress_denied(tenant_id)
    if blocked:
        console_audit.action_denied(
            tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
            action="voice.model_provision", target_kind="voice_model",
            target_id=st.get("lang", ""), reason="egress-policy")
        return {**st, "state": "failed",
                "error": f"the egress policy does not allow downloads from {blocked}"}

    def _done(job: dict) -> None:
        try:
            if job.get("state") == "ready":
                console_audit.action_performed(
                    tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
                    action="voice.model_provision", target_kind="voice_model",
                    target_id=job.get("lang", ""), trigger=trigger)
            else:
                console_audit.action_failed(
                    tenant_id=tenant_id, sid_fingerprint=sid_fingerprint,
                    action="voice.model_provision", target_kind="voice_model",
                    target_id=job.get("lang", ""), reason=_reason(job))
        except Exception:  # noqa: BLE001 — audit failure must not kill the worker
            _log.warning("voice model audit failed", exc_info=True)

    try:
        return _vm.request(lang, on_done=_done)
    except Exception:  # noqa: BLE001
        _log.warning("voice model provisioning could not start", exc_info=True)
        return status(lang)


def self_heal(lang: str | None, *, tenant_id: str) -> dict:
    """Provision *lang* if it has no usable model — at most once per backoff."""
    st = status(lang)
    if st.get("state") not in ("missing", "failed", "engine_missing"):
        return st
    key = st.get("lang", "")
    now = time.monotonic()
    with _lock:
        if now - _last_self_heal.get(key, -_SELF_HEAL_BACKOFF_S) < _SELF_HEAL_BACKOFF_S:
            return st
        _last_self_heal[key] = now
    return provision(lang, tenant_id=tenant_id, sid_fingerprint="system", trigger="self_heal")
