"""AnthropicBatchEngine — ADR-0099 M1 + M2.

Implements the ComputeEngine protocol by submitting all parameter
candidates as a single Anthropic Message Batches API (ABP) call.

Use-case: *prompt-sweep* evaluation — each candidate fills a Jinja2-style
prompt template, is sent to a Claude model as one batch request, and is
scored by extracting a float loss from the response text.

ComputeSpec.extra keys (all optional):
    prompt_template (str):
        Template string with ``{{param_name}}`` placeholders filled from
        ``param_grid``.  Default: ``"{{params}}"`` (JSON-dumps the params).
    model (str):
        Anthropic model id.  Default: ``claude-haiku-4-5-20251001``.
    max_tokens_per_call (int):
        Per-candidate token budget.  Default: ``256``.
    result_extractor (str):
        How to derive the float loss from the response text.
        ``"first_float"`` (default) — regex first float.
        ``"parse_float"``           — entire text as float.
        ``"json:<key>"``            — parse JSON and pluck key.
    system_prompt (str):
        Optional system message prepended to each request.
    session_key (str):
        ``"<channel>:<chat_id>"`` string; needed to write the
        ``open_batches.json`` cleanup file.  Omit in tests.

Compliance:
    L34 — anthropic_batch, locality=us_cloud, max_classification=INTERNAL.
    L8  — open batch_ids logged for session_reset cleanup hook.

Must NOT ``import anthropic`` — CI AST lint enforces this.
Uses ``httpx`` directly for all Anthropic API calls.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine_protocol import (
    ComputeEngine,
    ComputeResult,
    ComputeSpec,
    ComputeStatus,
    EngineDoesNotSupportGates,
    GateAction,
    UnknownJobId,
)

log = logging.getLogger(__name__)

_ABP_BASE = "https://api.anthropic.com/v1/messages/batches"
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_TOKENS = 256
_POLL_INTERVAL_S = 30.0
_BATCH_ENDED = frozenset({"ended"})

# ── internal state ────────────────────────────────────────────────────────────


@dataclass
class _BatchJob:
    job_id: str
    batch_id: str
    tenant_id: str
    session_key: str | None
    candidates: list[dict[str, Any]]  # [{custom_id, params}]
    submitted_at: float = field(default_factory=time.time)
    state: str = "running"             # running | succeeded | partial | failed | aborted
    result: dict[str, Any] | None = None
    error: str | None = None
    extractor: str = "first_float"     # result_extractor from spec.extra
    minimise: bool = True              # sort ascending (True) or descending (False)


# ── helpers ───────────────────────────────────────────────────────────────────


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "anthropic_batch: ANTHROPIC_API_KEY not set — "
            "install the corvin-batch manifest via the MCP Plugin Manager first"
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "x-api-key": _api_key(),
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "message-batches-2024-09-24",
        "content-type": "application/json",
    }


def _expand_grid(param_grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of param_grid values."""
    if not param_grid:
        return [{}]
    keys = list(param_grid.keys())
    values = [param_grid[k] if isinstance(param_grid[k], list)
               else [param_grid[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _fill_template(template: str, params: dict[str, Any]) -> str:
    """Simple {{key}} substitution — no Jinja2 dependency."""
    result = template
    for k, v in params.items():
        result = result.replace(f"{{{{{k}}}}}", str(v))
    return result


def _extract_loss(text: str, extractor: str) -> float | None:
    """Extract a float loss value from LLM response text."""
    try:
        if extractor == "parse_float":
            return float(text.strip())
        if extractor.startswith("json:"):
            key = extractor[5:]
            parsed = json.loads(text)
            return float(parsed[key])
        # default: "first_float" — regex first number
        m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text)
        if m:
            return float(m.group())
    except Exception:  # noqa: BLE001
        pass
    return None


def _open_batches_path(session_key: str, base: Path) -> Path:
    """<base>/sessions/<session_key>/compute/open_batches.json.

    ``base`` should be the tenant home (corvin_home/tenants/<tid>)
    so files are isolated per tenant.  Tests may pass a tempdir.
    """
    safe_key = re.sub(r"[/\\]", "_", session_key)
    return base / "sessions" / safe_key / "compute" / "open_batches.json"


def _tenant_home_path(tenant_id: str) -> Path:
    """Return the tenant home root for open_batches path construction.

    Uses _corvin_home() so tests that patch _corvin_home still work.
    Result: corvin_home/tenants/<tenant_id>
    """
    return _corvin_home() / "tenants" / tenant_id


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    try:
        from forge.paths import corvin_home  # type: ignore[import]
        return corvin_home()
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _record_open_batch(
    session_key: str | None,
    job_id: str,
    batch_id: str,
    candidate_count: int,
    tenant_id: str = "_default",
) -> None:
    """Append batch_id to the session-scoped open_batches.json for L8 cleanup."""
    if not session_key:
        return
    try:
        path = _open_batches_path(session_key, _tenant_home_path(tenant_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                existing = []
        existing.append({
            "job_id": job_id,
            "batch_id": batch_id,
            "submitted_at": int(time.time()),
            "candidate_count": candidate_count,
        })
        path.write_text(json.dumps(existing, indent=2))
    except Exception as exc:  # noqa: BLE001
        log.warning("anthropic_batch: failed to record open batch: %s", exc)


def _remove_open_batch(session_key: str | None, batch_id: str,
                        tenant_id: str = "_default") -> None:
    """Remove a completed batch_id from open_batches.json."""
    if not session_key:
        return
    try:
        path = _open_batches_path(session_key, _tenant_home_path(tenant_id))
        if not path.exists():
            return
        existing = json.loads(path.read_text())
        remaining = [e for e in existing if e.get("batch_id") != batch_id]
        if not remaining:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(remaining, indent=2))
    except Exception as exc:  # noqa: BLE001
        log.warning("anthropic_batch: failed to update open_batches.json: %s", exc)


# ── HTTP helpers (synchronous — engine runs in thread pool context) ────────────


def _http_post(url: str, payload: dict) -> dict:
    import httpx  # lazy import — not in compute critical path
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers=_headers())
        r.raise_for_status()
        return r.json()


def _http_get(url: str) -> dict:
    import httpx
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers())
        r.raise_for_status()
        return r.json()


def _http_post_empty(url: str) -> dict:
    import httpx
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, headers=_headers())
        r.raise_for_status()
        return r.json()


def _http_get_jsonl(url: str) -> list[dict]:
    """Fetch a JSONL stream and return parsed lines."""
    import httpx
    lines: list[dict] = []
    with httpx.Client(timeout=120.0) as client:
        with client.stream("GET", url, headers=_headers()) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return lines


# ── audit helper ──────────────────────────────────────────────────────────────


def _emit(event: str, *, job_id: str, tenant_id: str, **details: Any) -> None:
    try:
        from .. import audit as _compute_audit
        from forge.paths import corvin_home  # type: ignore[import]
        from forge.security_events import write_event  # type: ignore[import]
        audit_path = corvin_home() / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        _compute_audit.emit(
            event,
            path=audit_path,
            run_id=job_id,
            tenant_id=tenant_id,
            write_event_fn=write_event,
            **details,
        )
    except Exception:  # noqa: BLE001
        log.debug("anthropic_batch: audit emit skipped for %s", event)


# ── engine ────────────────────────────────────────────────────────────────────


class AnthropicBatchEngine:
    """ComputeEngine that submits all candidates as one ABP batch call.

    Thread-safe: ``_jobs`` protected by ``_lock``.
    """

    engine_id = "anthropic_batch"
    display_name = "Anthropic Batch Compute (ADR-0099)"
    job_id_prefix = "abatch_"
    supports_gates = False

    def __init__(self) -> None:
        self._jobs: dict[str, _BatchJob] = {}
        self._lock = threading.Lock()

    # ── ComputeEngine protocol ──────────────────────────────────────────

    def submit(self, spec: ComputeSpec) -> str:
        """Submit all candidates as one ABP batch. Returns job_id immediately."""
        from ..state import new_run_id

        self._assert_compliance(spec.tenant_id)

        extra = spec.extra or {}
        template = extra.get("prompt_template", "{{params}}")
        model = extra.get("model", _DEFAULT_MODEL)
        max_tokens = int(extra.get("max_tokens_per_call", _DEFAULT_MAX_TOKENS))
        system_prompt = extra.get("system_prompt")
        session_key = extra.get("session_key")
        extractor = extra.get("result_extractor", "first_float")

        candidates = _expand_grid(spec.param_grid or {})

        # Honour budget.max_iterations if set
        max_iter = (spec.budget or {}).get("max_iterations", len(candidates))
        candidates = candidates[:max_iter]

        if not candidates:
            raise ValueError("anthropic_batch: param_grid produced no candidates")

        requests = []
        for i, params in enumerate(candidates):
            prompt = _fill_template(
                template,
                {**params, "params": json.dumps(params)},
            )
            req: dict[str, Any] = {
                "custom_id": f"c{i}",
                "params": {
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            }
            if system_prompt:
                req["params"]["system"] = system_prompt
            requests.append(req)

        batch_resp = _http_post(_ABP_BASE, {"requests": requests})
        batch_id: str = batch_resp["id"]

        job_id = new_run_id().replace("compute_", self.job_id_prefix, 1)
        job = _BatchJob(
            job_id=job_id,
            batch_id=batch_id,
            tenant_id=spec.tenant_id,
            session_key=session_key,
            candidates=[{"custom_id": f"c{i}", "params": p}
                        for i, p in enumerate(candidates)],
            extractor=extractor,
            minimise=spec.minimise,
        )
        with self._lock:
            self._jobs[job_id] = job

        _record_open_batch(session_key, job_id, batch_id, len(candidates),
                           tenant_id=spec.tenant_id)

        _emit(
            "compute.batch_submitted",
            job_id=job_id,
            tenant_id=spec.tenant_id,
            batch_id_prefix=batch_id[:16],
            candidate_count=len(candidates),
        )
        log.info(
            "anthropic_batch: submitted job=%s batch=%s candidates=%d",
            job_id, batch_id[:16], len(candidates),
        )
        return job_id

    def status(self, job_id: str) -> ComputeStatus:
        job = self._get_job(job_id)
        if job.state in ("succeeded", "partial", "failed", "aborted"):
            return self._terminal_status(job)

        # Poll ABP for current state.
        try:
            raw = _http_get(f"{_ABP_BASE}/{job.batch_id}")
        except Exception as exc:
            log.warning("anthropic_batch: poll failed for %s: %s", job_id, exc)
            return ComputeStatus(
                job_id=job_id,
                engine_id=self.engine_id,
                state="running",
                progress={"error": str(exc)},
                detail={},
            )

        processing = raw.get("processing_status", "")
        req_counts = raw.get("request_counts", {})
        total = req_counts.get("processing", 0) + req_counts.get("succeeded", 0) + \
                req_counts.get("errored", 0) + req_counts.get("expired", 0) + \
                req_counts.get("canceled", 0)

        if processing in _BATCH_ENDED:
            self._collect_results(job, raw)
            return self._terminal_status(job)

        return ComputeStatus(
            job_id=job_id,
            engine_id=self.engine_id,
            state="running",
            progress={
                "processing_status": processing,
                "request_counts": req_counts,
                "total_candidates": len(job.candidates),
                "submitted_at": job.submitted_at,
            },
            detail={},
        )

    def result(self, job_id: str, wait_s: float = 30.0) -> ComputeResult:
        """Block until the batch ends or wait_s elapses, then return result."""
        job = self._get_job(job_id)
        deadline = time.monotonic() + wait_s

        while job.state == "running" and time.monotonic() < deadline:
            try:
                raw = _http_get(f"{_ABP_BASE}/{job.batch_id}")
                if raw.get("processing_status") in _BATCH_ENDED:
                    self._collect_results(job, raw)
                    break
            except Exception as exc:
                log.warning("anthropic_batch: result poll error: %s", exc)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(_POLL_INTERVAL_S, remaining))

        return ComputeResult(
            job_id=job_id,
            engine_id=self.engine_id,
            state=job.state,
            result=job.result or {},
        )

    def gate_action(self, job_id: str, action: GateAction) -> None:
        raise EngineDoesNotSupportGates(
            "AnthropicBatchEngine has no gates — use abort() to cancel"
        )

    def abort(self, job_id: str) -> None:
        job = self._get_job(job_id)
        if job.state not in ("succeeded", "partial", "failed", "aborted"):
            try:
                _http_post_empty(f"{_ABP_BASE}/{job.batch_id}/cancel")
            except Exception as exc:
                log.warning("anthropic_batch: cancel API error for %s: %s",
                            job_id, exc)
            with self._lock:
                job.state = "aborted"
            _remove_open_batch(job.session_key, job.batch_id,
                               tenant_id=job.tenant_id)
            _emit(
                "compute.batch_cancelled",
                job_id=job_id,
                tenant_id=job.tenant_id,
                batch_id_prefix=job.batch_id[:16],
                reason="explicit_abort",
            )

    # ── internal helpers ────────────────────────────────────────────────

    def _get_job(self, job_id: str) -> _BatchJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise UnknownJobId(job_id)
        return job

    def _terminal_status(self, job: _BatchJob) -> ComputeStatus:
        result = job.result or {}
        return ComputeStatus(
            job_id=job.job_id,
            engine_id=self.engine_id,
            state=job.state,
            progress={"total_candidates": len(job.candidates)},
            detail={
                "best_loss": result.get("best_loss"),
                "top_k": result.get("top_k", []),
                "partial": job.state == "partial",
                "failed_candidate_count": result.get("failed_candidate_count", 0),
            },
        )

    def _collect_results(self, job: _BatchJob, batch_meta: dict) -> None:
        """Fetch JSONL results, extract losses, build top-k."""
        with self._lock:
            if job.state in ("succeeded", "partial", "failed", "aborted"):
                return
        try:
            results_url = f"{_ABP_BASE}/{job.batch_id}/results"
            lines = _http_get_jsonl(results_url)
        except Exception as exc:
            with self._lock:
                job.state = "failed"
                job.error = str(exc)
            _emit("compute.batch_api_error",
                  job_id=job.job_id, tenant_id=job.tenant_id,
                  batch_id_prefix=job.batch_id[:16], error_class=type(exc).__name__)
            return

        # Build custom_id → params lookup
        cid_to_params = {c["custom_id"]: c["params"] for c in job.candidates}

        scored: list[dict[str, Any]] = []
        failed_count = 0

        for line in lines:
            cid = line.get("custom_id", "")
            res = line.get("result", {})
            if res.get("type") != "succeeded":
                failed_count += 1
                continue
            content = res.get("message", {}).get("content", [])
            text = " ".join(
                block.get("text", "") for block in content
                if block.get("type") == "text"
            )
            params = cid_to_params.get(cid, {})
            loss = _extract_loss(text, job.extractor)
            if loss is not None:
                scored.append({"params": params, "loss": loss})

        if not scored and failed_count == len(job.candidates):
            with self._lock:
                job.state = "failed"
                job.result = {"failed_candidate_count": failed_count}
            return

        scored.sort(key=lambda x: x["loss"], reverse=not job.minimise)
        top_k = scored[:5]
        best = scored[0] if scored else None

        with self._lock:
            job.state = "partial" if failed_count > 0 else "succeeded"
            job.result = {
                "best_params": best["params"] if best else {},
                "best_loss": best["loss"] if best else None,
                "top_k": [{"params": e["params"], "loss": e["loss"]}
                          for e in top_k],
                "total_candidates": len(job.candidates),
                "succeeded_count": len(scored),
                "failed_candidate_count": failed_count,
                "partial": failed_count > 0,
            }

        _remove_open_batch(job.session_key, job.batch_id,
                           tenant_id=job.tenant_id)

        if job.state == "partial":
            _emit(
                "compute.batch_partial",
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                batch_id_prefix=job.batch_id[:16],
                candidate_count=len(job.candidates),
                failed_candidate_count=failed_count,
            )
        else:
            _emit(
                "compute.batch_completed",
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                batch_id_prefix=job.batch_id[:16],
                candidate_count=len(job.candidates),
                duration_ms=int((time.time() - job.submitted_at) * 1000),
            )

    @staticmethod
    def _assert_compliance(tenant_id: str) -> None:
        """L34 gate — block if tenant matrix forbids anthropic_batch."""
        try:
            from data_classification import (  # type: ignore
                DataClassification, load_guard_for_tenant,
            )
        except Exception:
            # Forward-compatible: if the guard module isn't on path, best-effort pass.
            try:
                import sys
                _shared = Path(__file__).resolve().parents[5] / "operator" / "bridges" / "shared"
                if str(_shared) not in sys.path:
                    sys.path.insert(0, str(_shared))
                from data_classification import (  # type: ignore
                    DataClassification, load_guard_for_tenant,
                )
            except Exception:
                log.debug("anthropic_batch: L34 guard not available, skipping")
                return
        # Review fix: prior `for_tenant().check()` API did not exist
        # (AttributeError → silently dead gate). Use the shared opt-in
        # loader (reads the tenant's real matrix; None → no config → pass).
        # Only the guard LOAD may fail-open (genuinely-absent config); a
        # computed DENY must NEVER be swallowed by a best-effort audit emit,
        # so the deny is raised BEFORE the (isolated) emit. (HIGH-3)
        try:
            guard = load_guard_for_tenant(tenant_id)
        except Exception as exc:  # noqa: BLE001 — config load issue → fail-open
            log.warning("anthropic_batch: L34 guard load failed (non-fatal): %s", exc)
            return
        if guard is None:
            return
        decision = guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="anthropic_batch",
        )
        if not decision.allowed:
            try:
                _emit(
                    "compute.batch_gate_blocked",
                    job_id="(pre-submit)",
                    tenant_id=tenant_id,
                    reason=decision.reason,
                )
            except Exception:  # noqa: BLE001 — audit best-effort, never flips the deny
                pass
            raise PermissionError(
                f"anthropic_batch: L34 gate blocked for tenant {tenant_id!r}: "
                f"{decision.reason}"
            )


# ── cancel-all helper (called by L8 session-reset hook) ──────────────────────


def cancel_open_batches_for_session(session_key: str,
                                    tenant_id: str = "_default") -> list[str]:
    """Cancel all ABP batches listed in the session's open_batches.json.

    Returns list of cancelled batch_id prefixes.  Best-effort — never
    raises.  Called by session_reset._cancel_open_compute_batches().
    """
    cancelled: list[str] = []
    try:
        path = _open_batches_path(session_key, _tenant_home_path(tenant_id))
        if not path.exists():
            return []
        entries = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        log.warning("anthropic_batch: could not read open_batches.json: %s", exc)
        return []

    for entry in entries:
        bid = entry.get("batch_id", "")
        if not bid:
            continue
        try:
            _http_post_empty(f"{_ABP_BASE}/{bid}/cancel")
            cancelled.append(bid[:16])
        except Exception as exc:  # noqa: BLE001
            log.warning("anthropic_batch: cancel %s failed: %s", bid[:16], exc)

    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    return cancelled


# ── engine factory ────────────────────────────────────────────────────────────


def register_anthropic_batch_engine(registry=None) -> "AnthropicBatchEngine":
    from .. import engine_registry as _reg
    r = registry or _reg._default_registry
    engine = AnthropicBatchEngine()
    r.register(engine)
    return engine
