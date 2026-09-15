"""Layer 36 — ADR-0153 M4: CorvinID Erasure Handler.

Implements :class:`ErasureHandler` Protocol for CorvinID certificate data, as
required by GDPR Art. 17 and the L36 erasure orchestrator (ADR-0045).

Scope of erasure
----------------
CorvinID-related data that can be attributed to a ``subject_id``:

  * **IBC certificate** (``<corvin_home>/global/instance_cert.jwt``) — the
    Instance Binding Certificate JWT that links this deployment's Ed25519
    public key to the registered email+license. Deleted on erasure.

  * **Identity registry entry** (``<corvin_home>/global/identity_registry.json``)
    — maps instance UUIDs to {email, name, registered_at}. The entry matching
    ``subject_id`` is removed.

  * **Revocation (best-effort, fire-and-forget)** — optionally notifies the
    Corvin Labs revocation endpoint. Network errors are silently swallowed;
    the local deletion always proceeds regardless.

Audit-first invariant
---------------------
The ``identity.certificate_revoked`` audit event is emitted BEFORE the cert
file is deleted, following the same pattern as L38 ``A2AErasureHandler``.

Returns:
  * ``APPLIED`` + ``count=N`` — if ≥1 file/entry was deleted.
  * ``SKIPPED`` — if no cert exists and no registry entry found.

Layer ID: ``L153-corvinid``

Must NOT do:
  - ``import anthropic`` (CI AST lint enforces)
  - Put subject_id, email, or any PII in audit details
  - Block erasure on revocation network failure
"""
from __future__ import annotations

import json
import os
import time
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Lazy import so this module works standalone in tests.
try:
    from erasure_orchestrator import (  # type: ignore[import-not-found]
        ErasureLayerResult, LayerStatus,
    )
except ImportError:
    import sys as _sys
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in _sys.path:
        _sys.path.insert(0, str(_shared))
    from erasure_orchestrator import (  # type: ignore[import-not-found]
        ErasureLayerResult, LayerStatus,
    )

_REVOKE_URL = "https://corvin-features-production.up.railway.app/v1/instance/revoke"
_IDENTITY_REGISTRY_FILE = "identity_registry.json"
_IBC_FILE = "instance_cert.jwt"
_GLOBAL_DIR = "global"
_lock = threading.Lock()


def _default_corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".corvin"


def _audit_emit(event_type: str, severity: str, details: dict[str, Any]) -> None:
    """Best-effort audit emit — silently swallows all errors."""
    try:
        # SecurityEventsPlugin does not exist; use write_event() directly, which is
        # the pattern used by all other callers (remote_trigger_receiver, clag, etc.).
        try:
            from .forge.security_events import write_event as _write_event  # type: ignore
        except ImportError:
            try:
                from forge.security_events import write_event as _write_event  # type: ignore
            except ImportError:
                return
        # Resolve audit path via the shared audit module (same pattern as L36 orchestrator).
        try:
            try:
                from .audit import audit_path as _audit_path  # type: ignore
            except ImportError:
                from audit import audit_path as _audit_path  # type: ignore
            _log_path = _audit_path()
        except Exception:  # noqa: BLE001
            # Fallback to ~/.corvin/global/audit.jsonl if audit module is unavailable.
            _log_path = _default_corvin_home() / "global" / "audit.jsonl"
        _write_event(_log_path, event_type, severity=severity, details=details)
    except Exception:  # noqa: BLE001
        pass


def _fire_and_forget_revoke(instance_id: str) -> None:
    """POST to the Corvin Labs revocation endpoint in a daemon thread.

    Signed with the instance's own Ed25519 key over "instance_id:ts" —
    the server endpoint is deliberately unauthenticated (no license/HMAC
    context required, since local credentials may already be gone by the
    time this fires) but requires proof of possession of the instance
    private key so an unrelated caller cannot revoke someone else's binding
    by guessing/enumerating instance_ids (ADR-0145).

    Never blocks erasure. Network errors — and a failure to produce a
    signature — are silently swallowed; the local deletion already
    happened regardless of remote outcome.
    """
    def _do_revoke() -> None:
        revoke_url = os.environ.get("CORVIN_IBC_REVOKE_URL", _REVOKE_URL)
        try:
            ts = str(int(time.time()))
            sig = ""
            try:
                try:
                    from .instance_identity import sign_payload  # type: ignore
                except ImportError:
                    from instance_identity import sign_payload  # type: ignore
                sig = sign_payload(f"{instance_id}:{ts}".encode("utf-8"))
            except Exception:  # noqa: BLE001
                pass  # no local key (or crypto lib missing) — server will no-op
            body = json.dumps({"instance_id": instance_id, "ts": ts, "sig": sig}).encode("utf-8")
            req = urllib.request.Request(
                revoke_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception:  # noqa: BLE001
            pass  # best-effort; erasure is not conditional on remote success

    t = threading.Thread(target=_do_revoke, daemon=True, name="corvinid-revoke")
    t.start()


@dataclass
class CorvinIDErasureHandler:
    """L36 erasure handler for CorvinID certificate + identity registry.

    Registered via ``orchestrator.register_handler(CorvinIDErasureHandler())``.
    """

    layer_id: str = "L153-corvinid"
    corvin_home: Path | None = None
    revoke_remote: bool = True
    """When True, fires a best-effort POST to the Corvin Labs revocation endpoint
    after local deletion. Set False in tests or air-gapped deployments."""

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        """Delete CorvinID cert and identity-registry entry for *subject_id*.

        Audit-first: emits ``identity.certificate_revoked`` BEFORE any
        file deletion.
        """
        start = time.time()
        home = self.corvin_home or _default_corvin_home()
        global_dir = home / _GLOBAL_DIR

        cert_path = Path(os.environ.get("CORVIN_INSTANCE_CERT_PATH", "")
                         or global_dir / _IBC_FILE)
        registry_path = global_dir / _IDENTITY_REGISTRY_FILE

        cert_exists = cert_path.exists()
        instance_id = self._read_instance_id(global_dir)

        # Find the registry entry (if any) before deletion for revocation
        registry_had_entry, registry_count = self._check_registry(
            registry_path, subject_id
        )

        if not cert_exists and not registry_had_entry:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="no_corvinid_cert_or_registry_entry_found",
                duration_ms=int((time.time() - start) * 1000),
            )

        # Audit BEFORE deletion (audit-first invariant)
        if cert_exists:
            _audit_emit("identity.certificate_revoked", "WARNING", {
                "request_id": request_id,
                # Never include subject_id, email, or PII here
            })

        count = 0

        # Delete the IBC cert
        if cert_exists:
            try:
                cert_path.unlink()
                count += 1
            except OSError:
                pass

        # Remove registry entry
        if registry_had_entry:
            removed = self._remove_registry_entry(registry_path, subject_id)
            count += removed

        # Fire-and-forget revocation (non-blocking)
        if self.revoke_remote and instance_id:
            _fire_and_forget_revoke(instance_id)

        duration_ms = int((time.time() - start) * 1000)

        if count > 0:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.APPLIED,
                count=count,
                reason="",
                duration_ms=duration_ms,
            )
        # Cert existed but deletion failed (permission denied, etc.)
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            count=0,
            reason="deletion_failed_or_no_matching_entry",
            duration_ms=duration_ms,
        )

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _read_instance_id(global_dir: Path) -> str | None:
        """Read the instance_id from instance_id.json, best-effort."""
        id_file = global_dir / "instance_id.json"
        try:
            data = json.loads(id_file.read_text("utf-8"))
            return data.get("instance_id") or None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _check_registry(registry_path: Path, subject_id: str) -> tuple[bool, int]:
        """Check if subject_id appears as a key in the identity registry.

        Returns (found: bool, count: int).
        """
        if not registry_path.exists():
            return False, 0
        try:
            with _lock:
                data = json.loads(registry_path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            return False, 0
        if not isinstance(data, dict):
            return False, 0
        if subject_id in data:
            return True, 1
        return False, 0

    @staticmethod
    def _remove_registry_entry(registry_path: Path, subject_id: str) -> int:
        """Remove subject_id from identity_registry.json. Returns count removed."""
        if not registry_path.exists():
            return 0
        try:
            with _lock:
                raw = registry_path.read_text("utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict) or subject_id not in data:
                    return 0
                del data[subject_id]
                tmp = registry_path.with_suffix(".tmp")
                with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
                    json.dump(data, fh, indent=2, sort_keys=True)
                    fh.write("\n")
                os.replace(tmp, registry_path)
                return 1
        except Exception:  # noqa: BLE001
            return 0


__all__ = ["CorvinIDErasureHandler"]
