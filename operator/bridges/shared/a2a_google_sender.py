"""Layer 38 — Google A2A Protocol Sender (outbound bridge).

Sends tasks to external Google-A2A-compatible agents from Corvin.
Complements :mod:`a2a_google_adapter` (inbound) and mirrors the audit
pattern of :mod:`remote_trigger_sender` (native A2A outbound).

REACH: nothing calls this (verified 2026-07-28)
-----------------------------------------------
Built, audited, test-covered — and unreachable. There is no CLI entry point
(no ``__main__``, no argparse), no route, and no importer outside
``test_a2a_google_e2e.py`` / ``test_a2a_google_adapter.py``. The ``/a2a`` slash
command and the orchestration MCP server both go to
:mod:`remote_trigger_sender` (NATIVE A2A), never here.

The inbound half is one step better off: :class:`~a2a_google_adapter.
GoogleA2AAdapter` is mounted by :mod:`a2a_http_server`, which an operator can
start by hand (``python -m a2a_http_server --port 8001 …``) — but the FastAPI
gateway, the only A2A surface a normal install runs, exposes ``/v1/a2a/ping``
and ``/v1/a2a/receive`` and no Google routes at all. So Google-A2A interop is
opt-in-by-manual-process inbound and absent outbound.

Recorded rather than quietly wired, for the reason
``corvin_plugins/surface_map.py`` states about the plugin surfaces: a component
that registers/loads/passes its tests but is never invoked looks identical to a
working one from the outside. Giving this a call site is a design decision (from
which command? what happens to the reply? which compliance row does the spawn
class sit in?) and belongs in an ADR, not in a review pass.

Endpoint config
--------------
Place a JSON file under
``operator/cowork/remote_endpoints/<endpoint_id>.json`` (mode 0600)
with a ``google_a2a`` block:

  {
    "endpoint_id": "my-external-agent",
    "url": "https://agent.example.com/a2a",
    "enabled": true,
    "google_a2a": {
      "api_key": "<bearer-token>",
      "default_ttl_s": 60
    }
  }

``api_key`` is sent as ``Authorization: Bearer <key>``. It is NEVER
written to the audit chain.

Audit events (L16 hash chain, best-effort)
------------------------------------------
  A2A.google_envelope_sent      INFO     After request build, before HTTP
  A2A.google_response_received  INFO     After successful response parse
  A2A.google_response_rejected  WARNING  HTTP error, parse failure, or
                                         JSON-RPC error in response

Audit details allow-list:
  endpoint_id, task_id, status, state, duration_ms, http_status,
  artifact_count, reason, ttl_s

NEVER in details:
  api_key, instruction, response text or data, URL, request body

CI lint: MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
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
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from audit import audit_path  # type: ignore[import-not-found]

# ── Endpoint registry ─────────────────────────────────────────────────────
_REMOTE_ENDPOINTS_ENV = "REMOTE_ENDPOINTS_DIR"
_REMOTE_ENDPOINTS_DEFAULT = (
    Path(__file__).resolve().parents[2] / "cowork" / "remote_endpoints"
)

_DEFAULT_TIMEOUT_S = 30
_DEFAULT_TTL_S = 60


# ── Result dataclass ──────────────────────────────────────────────────────

@dataclass
class GoogleA2ASendResult:
    """Result from :meth:`GoogleA2ASender.send`."""
    ok: bool
    state: str           # "completed" | "failed" | "working" | "unknown"
    task_id: str
    data: dict           # first ``data`` artifact (or {})
    text: str            # first ``text`` artifact (or "")
    artifacts: list      # all raw artifact dicts from the response
    duration_ms: int
    error: dict | None   # Google A2A error object from task status, if any
    http_status: int     # HTTP response code (0 on transport failure)


# ── GoogleA2ASender ───────────────────────────────────────────────────────

class GoogleA2ASender:
    """Sends tasks to external Google-A2A-compatible agents.

    Thread-safe: no mutable state after __init__.
    """

    def __init__(self, endpoints_dir: Path | None = None, *, forge_se: Any = None) -> None:
        env = os.environ.get(_REMOTE_ENDPOINTS_ENV)
        if env:
            self._dir = Path(env)
        elif endpoints_dir is not None:
            self._dir = Path(endpoints_dir)
        else:
            self._dir = _REMOTE_ENDPOINTS_DEFAULT
        self._inst_forge_se = forge_se

    # ── Public API ─────────────────────────────────────────────────────

    def send(
        self,
        endpoint_id: str,
        instruction: str,
        *,
        attachments: list | None = None,
        result_schema: dict | None = None,
        ttl_s: int | None = None,
        timeout_s: int | None = None,
        task_id: str | None = None,
    ) -> GoogleA2ASendResult:
        """Send a task to a Google A2A endpoint; return the parsed result.

        This method never raises — transport and parse errors are
        reflected in the returned :class:`GoogleA2ASendResult`.
        """
        start = time.time()
        task_id = task_id or str(uuid.uuid4())

        endpoint = self._load_endpoint(endpoint_id)
        if endpoint is None:
            return self._failed(
                task_id, start, 0, "endpoint_not_found",
                {"code": -32000, "message": "Endpoint not found or disabled"},
            )

        google_cfg = endpoint.get("google_a2a") or {}
        api_key = str(google_cfg.get("api_key", ""))
        effective_ttl = int(ttl_s or google_cfg.get("default_ttl_s") or _DEFAULT_TTL_S)
        effective_timeout = int(timeout_s or _DEFAULT_TIMEOUT_S)
        url = str(endpoint.get("url", ""))

        # Build Google A2A JSON-RPC request
        task_parts: list[dict] = [{"type": "text", "text": instruction}]
        for att in (attachments or []):
            att_d = att if isinstance(att, dict) else vars(att)
            task_parts.append({
                "type": "file",
                "file": {
                    "mimeType": att_d.get("mime", "application/octet-stream"),
                    "data": att_d.get("content_b64", ""),
                    "name": att_d.get("name", "attachment"),
                },
            })

        payload: dict = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/send",
            "params": {
                "id": task_id,
                "message": {"role": "user", "parts": task_parts},
                "metadata": {
                    "ttl_s": effective_ttl,
                    **({"result_schema": result_schema} if result_schema else {}),
                },
            },
        }
        raw_body = json.dumps(payload).encode()

        self._audit(
            "A2A.google_envelope_sent", "INFO",
            {"endpoint_id": endpoint_id, "task_id": task_id,
             "ttl_s": effective_ttl, "status": "sending"},
        )

        # HTTP POST
        http_status = 0
        try:
            req = urllib.request.Request(
                url,
                data=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                http_status = resp.status
                raw_resp = resp.read()
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            self._audit(
                "A2A.google_response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": f"http_error_{http_status}",
                 "http_status": http_status, "duration_ms": _ms(start)},
            )
            return self._failed(
                task_id, start, http_status, f"http_error_{http_status}",
                {"code": -32000, "message": f"HTTP {http_status}"},
            )
        except Exception as exc:
            self._audit(
                "A2A.google_response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": "transport_error", "http_status": 0,
                 "duration_ms": _ms(start)},
            )
            return self._failed(
                task_id, start, 0, "transport_error",
                {"code": -32000,
                 "message": f"Transport error: {type(exc).__name__}"},
            )

        # Parse JSON response
        try:
            resp_json = json.loads(raw_resp)
        except Exception:
            self._audit(
                "A2A.google_response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": "invalid_json", "http_status": http_status,
                 "duration_ms": _ms(start)},
            )
            return self._failed(
                task_id, start, http_status, "invalid_json",
                {"code": -32000, "message": "Invalid JSON in response"},
            )

        # JSON-RPC level error
        if "error" in resp_json:
            err = resp_json["error"]
            self._audit(
                "A2A.google_response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": "jsonrpc_error", "http_status": http_status,
                 "duration_ms": _ms(start)},
            )
            return GoogleA2ASendResult(
                ok=False, state="failed", task_id=task_id,
                data={}, text="", artifacts=[],
                duration_ms=_ms(start),
                error=err if isinstance(err, dict) else {"code": -32000,
                                                         "message": str(err)},
                http_status=http_status,
            )

        # Parse task result
        task_result = resp_json.get("result") or {}
        task_status_obj = task_result.get("status") or {}
        state = str(task_status_obj.get("state", "unknown"))
        error_obj = task_status_obj.get("error")
        artifacts = list(task_result.get("artifacts") or [])

        # Extract convenience fields
        first_data: dict = {}
        first_text: str = ""
        for artifact in artifacts:
            for part in (artifact.get("parts") or []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "data" and not first_data:
                    first_data = dict(part.get("data") or {})
                elif part.get("type") == "text" and not first_text:
                    first_text = str(part.get("text", ""))

        ok = state == "completed" and error_obj is None
        self._audit(
            "A2A.google_response_received", "INFO",
            {"endpoint_id": endpoint_id, "task_id": task_id,
             "state": state, "artifact_count": len(artifacts),
             "http_status": http_status, "duration_ms": _ms(start)},
        )
        return GoogleA2ASendResult(
            ok=ok,
            state=state,
            task_id=task_id,
            data=first_data,
            text=first_text,
            artifacts=artifacts,
            duration_ms=_ms(start),
            error=error_obj if isinstance(error_obj, dict) else None,
            http_status=http_status,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _load_endpoint(self, endpoint_id: str) -> dict | None:
        if (
            not endpoint_id
            or "/" in endpoint_id
            or "\\" in endpoint_id
            or endpoint_id.startswith(".")
        ):
            return None
        path = self._dir / f"{endpoint_id}.json"
        if not path.exists():
            return None
        try:
            fst = path.stat()
            if fst.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                return None  # world-readable endpoint config is insecure
            with path.open() as fh:
                cfg = json.load(fh)
            if not cfg.get("enabled", False):
                return None
            if not (cfg.get("google_a2a") or {}).get("api_key"):
                return None
            return cfg
        except Exception:
            return None

    @staticmethod
    def _failed(
        task_id: str,
        start: float,
        http_status: int,
        reason: str,
        error: dict,
    ) -> "GoogleA2ASendResult":
        return GoogleA2ASendResult(
            ok=False, state="failed", task_id=task_id,
            data={}, text="", artifacts=[],
            duration_ms=_ms(start),
            error=error,
            http_status=http_status,
        )

    def _audit(self, event_type: str, severity: str, details: dict) -> None:
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


def _ms(start: float) -> int:
    return int((time.time() - start) * 1000)
