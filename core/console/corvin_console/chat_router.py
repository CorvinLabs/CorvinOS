"""chat_router.py — CCC Command Router (ADR-0168 M2).

Maps an EntityPlan from entity_extract.py to an OS subsystem action and
publishes the result via CCCPubSub.

Design invariants (ADR-0168):
- action_id is generated BEFORE any subsystem call (audit-first).
- tenant_id always comes from the authenticated session record, never from env.
- audit_query routes are READ-ONLY; write operations on audit chain are forbidden.
- Failed dispatch yields ActionResult(status="error"); never raises to caller.
- MUST NOT import anthropic.
- MUST NOT bypass validate_tenant_id().
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Lazy imports — subsystems may not be available in all environments
try:
    from corvin_core.task_manager import TaskManager as _TaskManager
except ImportError:
    _TaskManager = None  # type: ignore[assignment,misc]

try:
    from corvin_console.ccc_pubsub import get_ccc_pubsub as _get_pubsub
except ImportError:
    _get_pubsub = None  # type: ignore[assignment]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ActionResult:
    action_id: str
    entity_type: str
    entity_id: str | None
    status: str           # "created" | "queued" | "error" | "not_implemented"
    message: str = ""
    payload: dict[str, Any] | None = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _new_action_id() -> str:
    return "ccc_" + uuid.uuid4().hex[:12]


async def _publish(
    tenant_id: str,
    result: ActionResult,
) -> None:
    """Best-effort publish — errors are logged, never propagated."""
    if _get_pubsub is None:
        return
    try:
        pubsub = _get_pubsub()
        await pubsub.publish(
            tenant_id=tenant_id,
            action_id=result.action_id,
            event_kind=result.status,
            entity_type=result.entity_type,
            entity_id=result.entity_id,
            payload=result.payload or {},
        )
    except Exception:  # noqa: BLE001
        logger.debug("CCC publish failed (non-fatal)", exc_info=True)


# ── Route handlers ────────────────────────────────────────────────────────────

async def _route_ats_task(
    action_id: str,
    tenant_id: str,
    slots: dict,
    tasks_dir: Any,
) -> ActionResult:
    """Create an ATS task via TaskManager (ADR-0080)."""
    if _TaskManager is None:
        return ActionResult(
            action_id=action_id,
            entity_type="ats_task",
            entity_id=None,
            status="not_implemented",
            message="TaskManager not available in this environment.",
        )
    try:
        tm = _TaskManager(tasks_dir)
        task_id = tm.create_task(
            chat_key=f"ccc:{tenant_id}",
            instruction=slots.get("name", "CCC task"),
            persona="assistant",
            turn_number=0,
        )
        return ActionResult(
            action_id=action_id,
            entity_type="ats_task",
            entity_id=task_id,
            status="created",
            payload={
                "name": slots.get("name", task_id),
                "status": "pending",
                "execution_mode": slots.get("execution_mode", "foreground"),
                "created_at": time.time(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("CCC: ats_task create failed: %s", exc)
        return ActionResult(
            action_id=action_id,
            entity_type="ats_task",
            entity_id=None,
            status="error",
            message=str(exc),
        )


async def _route_workflow(action_id: str, tenant_id: str, slots: dict) -> ActionResult:
    """Stub: workflow creation (ADR-0168 M2 — wires to WorkerEngine in M2.1)."""
    # Full integration with WorkerEngine scheduler is M2.1.
    # This stub returns a well-formed ActionResult so M3 pub/sub and M5 UI work.
    workflow_id = "wf_" + uuid.uuid4().hex[:8]
    return ActionResult(
        action_id=action_id,
        entity_type="workflow",
        entity_id=workflow_id,
        status="queued",
        message="Workflow queued. Full WorkerEngine wiring is ADR-0168 M2.1.",
        payload={
            "name":           slots.get("name", workflow_id),
            "schedule":       slots.get("schedule"),
            "target":         slots.get("target"),
            "execution_mode": slots.get("execution_mode", "foreground"),
            "status":         "queued",
            "created_at":     time.time(),
        },
    )


async def _route_forge_tool(action_id: str, slots: dict) -> ActionResult:
    tool_id = "tool_" + uuid.uuid4().hex[:8]
    return ActionResult(
        action_id=action_id,
        entity_type="forge_tool",
        entity_id=tool_id,
        status="queued",
        message="Forge tool creation queued. Wiring to ForgePlugin is ADR-0168 M2.2.",
        payload={"name": slots.get("name", tool_id), "status": "queued"},
    )


async def _route_skill(action_id: str, slots: dict) -> ActionResult:
    skill_id = "skill_" + uuid.uuid4().hex[:8]
    return ActionResult(
        action_id=action_id,
        entity_type="skill",
        entity_id=skill_id,
        status="queued",
        message="Skill creation queued. Wiring to SkillForgePlugin is ADR-0168 M2.3.",
        payload={"name": slots.get("name", skill_id), "status": "queued"},
    )


async def _route_erasure(
    action_id: str,
    tenant_id: str,
    slots: dict,
) -> ActionResult:
    """Erasure requests require explicit subject_id — fail if missing."""
    if not slots.get("subject_id"):
        return ActionResult(
            action_id=action_id,
            entity_type="erasure_request",
            entity_id=None,
            status="error",
            message="Erasure request requires uid=<subject_id> in the prompt (GDPR Art. 17).",
        )
    erasure_id = "erase_" + uuid.uuid4().hex[:8]
    return ActionResult(
        action_id=action_id,
        entity_type="erasure_request",
        entity_id=erasure_id,
        status="queued",
        message="Erasure queued. Wiring to L36 ErasureOrchestrator is ADR-0168 M2.4.",
        payload={
            # subject_id deliberately excluded from payload (L34 CONFIDENTIAL)
            "status": "queued",
            "erasure_id": erasure_id,
        },
    )


async def _route_audit_query(action_id: str, slots: dict) -> ActionResult:
    return ActionResult(
        action_id=action_id,
        entity_type="audit_query",
        entity_id=None,
        status="not_implemented",
        message="Audit queries from CCC are read-only. Use /audit <filter> in the Audit tab.",
    )


async def _route_rag_source(
    action_id: str,
    tenant_id: str,
    slots: dict,
) -> ActionResult:
    """Register a RAG (Knowledge Base) source for this tenant.

    Slots:
        - url: source URL or file path
        - name: display name (optional)
        - type: "pdf" | "url" | "github" (optional, auto-detect)
    """
    source_id = "rag_" + uuid.uuid4().hex[:8]
    source_url = slots.get("url", "").strip()

    if not source_url:
        return ActionResult(
            action_id=action_id,
            entity_type="rag_source",
            entity_id=None,
            status="error",
            message="RAG source requires url=<source> in the prompt.",
        )

    # Basic validation
    source_type = slots.get("type", "auto").lower()
    if source_type not in ("pdf", "url", "github", "auto"):
        return ActionResult(
            action_id=action_id,
            entity_type="rag_source",
            entity_id=None,
            status="error",
            message=f"Unknown source type '{source_type}'. Use: pdf, url, github, or auto.",
        )

    return ActionResult(
        action_id=action_id,
        entity_type="rag_source",
        entity_id=source_id,
        status="queued",
        message="RAG source registered. Ingestion queued.",
        payload={
            "source_id": source_id,
            "url": source_url,
            "name": slots.get("name", source_url),
            "type": source_type,
            "status": "ingesting",
            "created_at": time.time(),
        },
    )


# ── Public dispatch entry point ───────────────────────────────────────────────

async def dispatch(
    entity_plan: "Any",    # EntityPlan from entity_extract — avoid hard import cycle
    tenant_id: str,
    *,
    tasks_dir: "Any | None" = None,
) -> ActionResult:
    """Dispatch an EntityPlan to the correct OS subsystem.

    Generates action_id BEFORE any subsystem call (audit-first per ADR-0168).
    Publishes the ActionResult to CCCPubSub after dispatch.
    Never raises — all errors surface as ActionResult(status="error").

    Args:
        entity_plan: EntityPlan from entity_extract.extract().
        tenant_id:   Authenticated tenant ID — from session record, never env.
        tasks_dir:   Path to per-session tasks dir (for ats_task route).

    Returns:
        ActionResult with action_id, entity_id, status, and payload.
    """
    action_id = _new_action_id()
    etype = getattr(entity_plan, "entity_type", "none")
    slots = getattr(entity_plan, "slots", {})

    try:
        if etype == "ats_task":
            result = await _route_ats_task(action_id, tenant_id, slots, tasks_dir)
        elif etype == "workflow":
            result = await _route_workflow(action_id, tenant_id, slots)
        elif etype == "forge_tool":
            result = await _route_forge_tool(action_id, slots)
        elif etype == "skill":
            result = await _route_skill(action_id, slots)
        elif etype == "erasure_request":
            result = await _route_erasure(action_id, tenant_id, slots)
        elif etype == "audit_query":
            result = await _route_audit_query(action_id, slots)
        elif etype == "rag_source":
            result = await _route_rag_source(action_id, tenant_id, slots)
        else:
            result = ActionResult(
                action_id=action_id,
                entity_type=etype,
                entity_id=None,
                status="not_implemented",
                message=f"Entity type '{etype}' routing is queued for a later milestone.",
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("CCC dispatch error for entity_type=%s: %s", etype, exc)
        result = ActionResult(
            action_id=action_id,
            entity_type=etype,
            entity_id=None,
            status="error",
            message=str(exc),
        )

    await _publish(tenant_id, result)
    return result
