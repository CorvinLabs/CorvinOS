"""Layer 38 — Google A2A Protocol Adapter (inbound bridge).

Bridges the Google Agent-to-Agent (A2A) JSON-RPC 2.0 protocol to the
Corvin L38 TaskEnvelope pipeline. External Google-A2A-compatible agents
invoke Corvin via:

  POST /a2a
  Authorization: Bearer <api_key>
  Content-Type: application/json

  {
    "jsonrpc": "2.0",
    "method": "tasks/send",
    "id": "req-1",
    "params": {
      "id": "task-uuid",
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Do something"}]
      },
      "metadata": {"ttl_s": 60, "result_schema": {...}}
    }
  }

Security design
--------------
API keys are validated via SHA-256 hash comparison (constant-time via
hmac.compare_digest). The raw key is never stored, logged, or included
in audit details.

After authentication the instruction is wrapped in a standard L38
TaskEnvelope, HMAC-signed with the origin's internal bridge key, and
routed through RemoteTriggerReceiver.receive() — all seven validation
steps, the audit-first chain write, and the six-layer prompt-injection
defences run identically to native A2A requests. The adapter is a
protocol-translation shim, not a security bypass.

Supported JSON-RPC methods
--------------------------
  tasks/send    — synchronous dispatch through full L38 pipeline
  tasks/get     — not supported (stateless); returns -32000 error
  tasks/cancel  — not supported (stateless); returns -32000 error

Agent card
----------
  GET /.well-known/agent.json
  URL is derived from the request Host header at serve time.

Origin config extension
-----------------------
Add a ``google_a2a`` block to an existing origin config (mode 0600):

  {
    "origin_id": "google_a2a.my-agent",
    "hmac_key": "<hex-64>",
    "recv_key": "<hex-64>",
    "enabled": true,
    "max_ttl_s": 300,
    "allowed_personas": ["assistant"],
    "spawn_worker": true,
    "google_a2a": {
      "enabled": true,
      "api_key_sha256": "<sha256-hex-of-bearer-token>"
    }
  }

Audit events (L16 hash chain, best-effort)
------------------------------------------
  A2A.google_auth_failed   WARNING  API key missing or unrecognised

  All downstream events (A2A.envelope_received, A2A.engine_spawned, …)
  are emitted by RemoteTriggerReceiver as usual.

Audit details allow-list: reason, status, duration_ms.
NEVER in details: api_key, api_key_hash, instruction, origin_id
  (before auth success), task_id (before auth success).

CI lint: MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import re
import secrets
import stat
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Forge security_events (audit chain) ──────────────────────────────────
_forge_se: Any = None
try:
    _forge_parent = Path(__file__).resolve().parents[2] / "forge"
    if str(_forge_parent) not in sys.path:
        sys.path.insert(0, str(_forge_parent))
    from forge import security_events as _forge_se  # type: ignore[import-not-found]
except Exception:
    _forge_se = None

try:
    from audit import audit_path  # type: ignore[import-not-found]
except ImportError:
    from audit import audit_path  # type: ignore[import-not-found]

try:
    from instance_identity import get_instance_id  # type: ignore[import-not-found]
except ImportError:
    def get_instance_id() -> str:  # type: ignore[misc]
        return ""

from remote_trigger_receiver import (  # type: ignore[import-not-found]
    RemoteTriggerReceiver,
    TaskEnvelope,
)

# ── JSON-RPC error codes ──────────────────────────────────────────────────
# Standard codes
_JSONRPC_METHOD_NOT_FOUND = -32601
# Google A2A extensions
_GOOGLE_A2A_UNAUTHORIZED  = -32001
_GOOGLE_A2A_BAD_PARAMS    = -32002
_GOOGLE_A2A_NOT_SUPPORTED = -32000

# ── Attachment name sanitizer ─────────────────────────────────────────────
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")


class GoogleA2AError(Exception):
    """Internal error raised during Google A2A request processing.

    Caught in _tasks_send() and converted to a JSON-RPC error response.
    """


def _sanitize_attachment_name(raw: str, index: int) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", raw or "")
    if not name or name[0] == "." or not _SAFE_NAME_RE.match(name):
        name = f"attachment_{index}.bin"
    return name


# ── Default agent card ────────────────────────────────────────────────────
_DEFAULT_AGENT_CARD: dict = {
    "name": "Corvin",
    "description": (
        "Corvin Layer 38 — secure, audit-first agent-to-agent endpoint. "
        "All requests are HMAC-validated, injection-resistant, and anchored "
        "in a tamper-evident L16 audit chain."
    ),
    "version": "1.0",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
    },
    "skills": [
        {
            "id": "general",
            "name": "General Assistant",
            "description": "Execute instructions via the Corvin WorkerEngine.",
        }
    ],
}


# ── GoogleA2AAdapter ──────────────────────────────────────────────────────

class GoogleA2AAdapter:
    """Bridges Google A2A JSON-RPC to the Corvin L38 TaskEnvelope pipeline.

    Thread-safe: no mutable state after __init__.
    """

    def __init__(
        self,
        receiver: RemoteTriggerReceiver,
        origins_dir: Path,
        *,
        instance_id: str | None = None,
        agent_card_overrides: dict | None = None,
        forge_se: Any = None,
    ) -> None:
        self._receiver = receiver
        self._origins_dir = Path(origins_dir)
        if instance_id is not None:
            self._instance_id = instance_id
        else:
            try:
                self._instance_id = get_instance_id()
            except Exception:
                self._instance_id = ""
        self._agent_card: dict = {
            **_DEFAULT_AGENT_CARD,
            **(agent_card_overrides or {}),
        }
        self._inst_forge_se = forge_se

    # ── Public API ─────────────────────────────────────────────────────

    def agent_card(self, base_url: str = "") -> dict:
        """Return the A2A agent card; sets ``url`` to ``<base_url>/a2a``."""
        card = dict(self._agent_card)
        card["url"] = f"{base_url.rstrip('/')}/a2a"
        return card

    def dispatch(self, body: dict, api_key: str | None) -> dict:
        """Handle one Google A2A JSON-RPC request; return a JSON-RPC dict.

        HTTP status is always 200 — errors are expressed as JSON-RPC
        error objects per spec.
        """
        jsonrpc_id = body.get("id")
        method = str(body.get("method", ""))

        if method == "tasks/send":
            return self._tasks_send(body, api_key, jsonrpc_id)
        if method == "tasks/get":
            return _jsonrpc_error(
                _GOOGLE_A2A_NOT_SUPPORTED,
                "tasks/get not supported (stateless receiver)",
                jsonrpc_id,
            )
        if method == "tasks/cancel":
            return _jsonrpc_error(
                _GOOGLE_A2A_NOT_SUPPORTED,
                "tasks/cancel not supported (stateless receiver)",
                jsonrpc_id,
            )
        return _jsonrpc_error(_JSONRPC_METHOD_NOT_FOUND, "Method not found", jsonrpc_id)

    # ── tasks/send ─────────────────────────────────────────────────────

    def _tasks_send(
        self, body: dict, api_key: str | None, jsonrpc_id: Any
    ) -> dict:
        start = time.time()
        params = body.get("params") or {}
        task_id = str(params.get("id") or uuid.uuid4())
        message = params.get("message") or {}

        # Step 1 — Authenticate via API key.
        origin_cfg = self._find_origin_by_api_key(api_key)
        if origin_cfg is None:
            self._audit_best_effort(
                "A2A.google_auth_failed", "WARNING",
                {"reason": "no_matching_origin", "status": "rejected",
                 "duration_ms": _ms(start)},
            )
            return _jsonrpc_error(
                _GOOGLE_A2A_UNAUTHORIZED, "Unauthorized", jsonrpc_id
            )

        # Step 2 — Extract instruction from message parts.
        instruction = self._extract_instruction(message)
        if not instruction:
            return _jsonrpc_error(
                _GOOGLE_A2A_BAD_PARAMS,
                "No usable text or data found in message parts",
                jsonrpc_id,
            )

        # Step 3 — Convert Google A2A file parts to L38 attachments.
        # validate_attachments() enforces name rules, count cap, total-bytes
        # cap, and digest verification — required so Google A2A attachments
        # receive the same validation as native L38 attachments
        # (ADR-0099 iter-2 finding MED-A2A-GOOGLE-02).
        try:
            file_attachments = self._extract_attachments(
                message.get("parts") or []
            )
        except GoogleA2AError as _ae:
            return _jsonrpc_error(
                _GOOGLE_A2A_BAD_PARAMS,
                f"invalid attachment: {_ae}",
                jsonrpc_id,
            )
        if file_attachments:
            try:
                from a2a_attachments import (  # type: ignore[import-not-found]
                    validate_attachments as _va,
                    AttachmentError as _AE,
                )
                file_attachments_raw = [a.to_dict() for a in file_attachments]
                file_attachments = list(_va(file_attachments_raw))
            except ImportError:
                pass  # a2a_attachments not installed — envelope layer validates
            except Exception as _ve:
                return _jsonrpc_error(
                    _GOOGLE_A2A_BAD_PARAMS,
                    f"attachment validation failed: {type(_ve).__name__}",
                    jsonrpc_id,
                )

        # Step 4 — Derive result_schema and ttl_s from metadata.
        meta = params.get("metadata") or {}
        result_schema: dict = meta.get("result_schema") or {}
        max_ttl = int(origin_cfg.get("max_ttl_s") or 300)
        ttl_s = min(int(meta.get("ttl_s") or 60), max_ttl)

        # Step 5 — Build and HMAC-sign an internal TaskEnvelope.
        #   This makes the call indistinguishable from a native A2A call
        #   to the receiver — all L38 validations run normally.
        envelope_dict = self._build_signed_envelope(
            task_id=task_id,
            origin_cfg=origin_cfg,
            instruction=instruction,
            result_schema=result_schema,
            ttl_s=ttl_s,
            file_attachments=file_attachments,
        )

        # Step 6 — Route through the full L38 pipeline.
        response_env = self._receiver.receive(envelope_dict)

        # Step 7 — Convert ResponseEnvelope to Google A2A Task.
        task = self._to_google_task(task_id, response_env)
        return _jsonrpc_result(task, jsonrpc_id)

    # ── Helpers ────────────────────────────────────────────────────────

    def _find_origin_by_api_key(self, api_key: str | None) -> dict | None:
        """Scan origins_dir for a google_a2a-enabled origin whose
        api_key_sha256 matches the presented Bearer token.

        Uses constant-time comparison (hmac.compare_digest) to prevent
        timing-oracle attacks.
        """
        if not api_key:
            return None

        presented_hash = hashlib.sha256(api_key.encode()).hexdigest()

        try:
            candidates = sorted(self._origins_dir.glob("*.json"))
        except Exception:
            return None

        for path in candidates:
            try:
                fst = path.stat()
                if fst.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                    continue  # skip world-readable files (same as OriginRegistry)
                with path.open() as fh:
                    cfg = json.load(fh)
                if not cfg.get("enabled", False):
                    continue
                google_cfg = cfg.get("google_a2a") or {}
                if not google_cfg.get("enabled", False):
                    continue
                stored = str(google_cfg.get("api_key_sha256", ""))
                if not stored:
                    continue
                if _hmac.compare_digest(presented_hash, stored.lower()):
                    return cfg
            except Exception:
                continue
        return None

    def _build_signed_envelope(
        self,
        *,
        task_id: str,
        origin_cfg: dict,
        instruction: str,
        result_schema: dict,
        ttl_s: int,
        file_attachments: list,
    ) -> dict:
        """Build + sign a TaskEnvelope using the origin's hmac_key."""
        nonce = secrets.token_hex(32)
        env = TaskEnvelope(
            task_id=task_id,
            nonce=nonce,
            issued_at=time.time(),
            origin_id=origin_cfg["origin_id"],
            instruction=instruction,
            result_schema=result_schema,
            ttl_s=ttl_s,
            sender_instance_id=self._instance_id,
            attachments=[
                asdict(a) if hasattr(a, "__dataclass_fields__") else a
                for a in file_attachments
            ],
            signature="",
        )
        key = bytes.fromhex(origin_cfg["hmac_key"])
        sig = _hmac.new(key, env.canonical_payload(), hashlib.sha256).hexdigest()
        d = asdict(env)
        d["signature"] = sig
        return d

    @staticmethod
    def _extract_instruction(message: dict) -> str:
        """Join text parts; serialize data parts as JSON. Skip file parts."""
        parts = message.get("parts") or []
        segments: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            ptype = str(part.get("type", ""))
            if ptype == "text":
                txt = str(part.get("text", "")).strip()
                if txt:
                    segments.append(txt)
            elif ptype == "data":
                data = part.get("data")
                if data is not None:
                    segments.append(json.dumps(data, ensure_ascii=False))
        return "\n".join(segments).strip()

    @staticmethod
    def _extract_attachments(parts: list) -> list:
        """Convert Google A2A file parts to L38 Attachment objects.

        Raises GoogleA2AError on any base64 decode failure so that the
        request is rejected, not silently truncated (ADR-0099 iter-2
        finding HIGH-A2A-GOOGLE-01).
        """
        try:
            from a2a_attachments import Attachment  # type: ignore[import-not-found]
        except ImportError:
            return []

        result: list = []
        file_index = 0
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "file":
                continue
            file_info = part.get("file") or {}
            raw_data = file_info.get("data", "")
            if not raw_data:
                continue
            mime = str(file_info.get("mimeType", "application/octet-stream"))
            raw_name = str(file_info.get("name", f"attachment_{file_index}.bin"))
            name = _sanitize_attachment_name(raw_name, file_index)
            try:
                content = base64.b64decode(raw_data, validate=True)
            except Exception as _b64e:
                # Reject the entire request on malformed attachment data.
                # Silent skip would cause audit-log mismatch and hide
                # attacker-controlled data truncation.
                raise GoogleA2AError(
                    f"attachment_{file_index}_invalid_base64:{type(_b64e).__name__}"
                ) from _b64e
            result.append(Attachment.from_bytes(name=name, mime=mime, content=content))
            file_index += 1
        return result

    @staticmethod
    def _to_google_task(task_id: str, response_env: Any) -> dict:
        """Convert an L38 ResponseEnvelope to a Google A2A Task object."""
        status = response_env.status

        if status in ("ok", "filtered"):
            state = "completed"
            error_info = None
        elif status == "timeout":
            state = "failed"
            error_info = {"code": -32001, "message": "Task timed out"}
        else:  # "rejected" or unknown
            state = "failed"
            error_info = {"code": -32000, "message": "Request rejected"}

        task_status: dict = {"state": state}
        if error_info:
            task_status["error"] = error_info

        artifacts: list = []

        # Structured data → Google A2A data part
        if response_env.data:
            artifacts.append({
                "name": "result",
                "parts": [{"type": "data", "data": response_env.data}],
            })

        # Binary attachments → Google A2A file parts
        for att in (response_env.attachments or []):
            att_d = att if isinstance(att, dict) else asdict(att)
            artifacts.append({
                "name": att_d.get("name", "attachment"),
                "parts": [{
                    "type": "file",
                    "file": {
                        "mimeType": att_d.get("mime", "application/octet-stream"),
                        "data": att_d.get("content_b64", ""),
                    },
                }],
            })

        return {
            "id": task_id,
            "status": task_status,
            "artifacts": artifacts,
        }

    # ── Audit ──────────────────────────────────────────────────────────

    def _audit_best_effort(self, event_type: str, severity: str, details: dict) -> None:
        try:
            se = self._inst_forge_se if self._inst_forge_se is not None else _forge_se
            if se is None:
                return
            path = audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            se.write_event(
                path, event_type,
                severity=severity, tool="", run_id="",
                details=details, hash_chain=True,
            )
        except Exception:
            pass


# ── JSON-RPC helpers ──────────────────────────────────────────────────────

def _jsonrpc_result(result: Any, jsonrpc_id: Any) -> dict:
    return {"jsonrpc": "2.0", "id": jsonrpc_id, "result": result}


def _jsonrpc_error(code: int, message: str, jsonrpc_id: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": jsonrpc_id,
        "error": {"code": code, "message": message},
    }


def _ms(start: float) -> int:
    return int((time.time() - start) * 1000)
