"""Which model a workflow node runs on (ADR-2308).

Pure functions over injectable readers. Nothing here spawns anything; the engines call
:func:`resolve_node_model` (via :func:`stamp_call` / :func:`describe`) and own the argv.

Precedence, first source with a value wins (ADR-2308 decision 1):

1. operator pin (``worker_model`` slot of the console selection)
2. node value ``model`` (validated; an unknown value is rejected, never replaced)
3. gate-backed recommendation (acts only for ``adopt`` rows, see :func:`_recommendation`)
4. site default: the ``CORVIN_HELPER_MODEL_WORKFLOW_NODE`` variable, else the path default
   (an empty model id means "keep the argv this path had before")

Only node types in :data:`SCOPE_TYPES` are resolved (``route`` only in ``mode: classify``, the
one route mode that calls a model); every other type is returned untouched.
No model-id literal lives here: tiers are looked up by name through the injected readers.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

_log = logging.getLogger("corvin_workflows.model_resolution")

TIER_NAMES = ("haiku", "sonnet", "opus")
SCOPE_TYPES = frozenset({"agent", "fan_out", "route"})
PATHS = ("legacy", "dag")
WORKER_ROLE = "worker_model"
ENV_SITE = "workflow_node"

# ``node_class`` context buckets, in characters of the assembled prompt.
_CTX_SMALL = 8_000
_CTX_LARGE = 32_000

_TOOL_KEYS = ("tools", "forge_tools", "skills")


class ModelUnknown(ValueError):
    """A node value the registry does not know. Raised, never swallowed or replaced. The message
    is fixed: it reaches ``node.failed`` on the audit chain, which never carries a node value."""

    reason_code = "model_unknown"


class ModelUnresolved(ModelUnknown):
    """The node names a model but the resolver could not run (a reader fault). Fail the node:
    running it without ``--model`` would silently pick the path default instead."""

    reason_code = "model_unresolved"


@dataclass(frozen=True)
class Resolution:
    model_id: str            # "" = keep the argv this path had before
    source: str              # pin | node | recommendation | recommendation_shadow | site_default
    gate_status: str         # adopt | shadow | none | rolled_back | stale
    shadow_model_id: Optional[str] = None


# ── dependencies ─────────────────────────────────────────────────────────────

def _ensure_shared_on_path() -> None:
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        cand = os.path.join(d, "corvin_operator", "bridges", "shared")
        if os.path.isdir(cand):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return
        d = os.path.dirname(d)


class DefaultDeps:
    """The real readers, imported lazily so the module stays importable on its own."""

    def __init__(self) -> None:
        _ensure_shared_on_path()
        import console_selection as _cs  # type: ignore[import-not-found]
        import helper_model as _hm  # type: ignore[import-not-found]
        import model_lineage as _ml  # type: ignore[import-not-found]
        import model_selector as _ms  # type: ignore[import-not-found]
        self._cs, self._hm, self._ml, self._ms = _cs, _hm, _ml, _ms

    def pin(self, tenant_id: str) -> Optional[str]:
        return self._cs.pinned_model(WORKER_ROLE, tenant_id=tenant_id)

    def env(self) -> Optional[str]:
        return self._hm.env_only_helper_model(ENV_SITE)

    def registry_id(self, value: str) -> Optional[str]:
        return self._ms.resolve_registry_id(value, "claude_code")

    def tier(self, name: str) -> str:
        return self._ms.tier_model(name)

    def rank(self, model: Optional[str]) -> int:
        return self._ms._rank(model)

    def current(self, model: str) -> str:
        return self._ml.current(model) or model

    def legacy_default(self, tenant_id: str) -> Optional[str]:
        """The model the console's own executor runs an unset node on. That is the CLI's default,
        not a Corvin setting, so there is nothing to read: unmeasured -> ``None``."""
        return None

    def autoselect_is_off(self) -> bool:
        return os.environ.get("CORVIN_OS_MODEL_AUTOSELECT", "").strip().lower() == "off"


_DEPS: Optional[DefaultDeps] = None


def _default_deps() -> DefaultDeps:
    """One shared instance: the imports behind it are the expensive part."""
    global _DEPS
    if _DEPS is None:
        _DEPS = DefaultDeps()
    return _DEPS


# ── node facts ───────────────────────────────────────────────────────────────

def _node_type(node: dict[str, Any]) -> str:
    return str(node.get("type", "agent"))


def _scoped(node: dict[str, Any]) -> bool:
    """In scope: a type in :data:`SCOPE_TYPES`; a ``route`` node only when it classifies
    (``mode: condition`` is deterministic and never spawns)."""
    ntype = _node_type(node)
    if ntype not in SCOPE_TYPES:
        return False
    return ntype != "route" or node.get("mode") == "classify"


def gate_key(node: dict[str, Any]) -> str:
    """The coarse gate key ``wf:<type>`` (ADR-2308 decision 4)."""
    return f"wf:{_node_type(node)}"


def _has_tools(node: dict[str, Any]) -> bool:
    if any(node.get(k) for k in _TOOL_KEYS):
        return True
    return any(node.get(k) for k in node if str(k).startswith(("compute", "x_compute")))


def node_class(node: dict[str, Any], prompt_len: Optional[int]) -> str:
    """``type|tools|ctx|contract``; no free text, no agent name. ``ctx`` is empty
    when no prompt was assembled (``model_unknown``)."""
    if prompt_len is None:
        ctx = ""
    elif prompt_len < _CTX_SMALL:
        ctx = "<8k"
    elif prompt_len <= _CTX_LARGE:
        ctx = "8k-32k"
    else:
        ctx = ">32k"
    return f"{_node_type(node)}|{'t' if _has_tools(node) else 'n'}|{ctx}|json"


# ── resolution ───────────────────────────────────────────────────────────────

def _node_value(value: str, deps: Any) -> str:
    if value in TIER_NAMES:
        return deps.tier(value)
    rid = deps.registry_id(value)
    if not rid:
        raise ModelUnknown("unknown node model (model_unknown)")
    return deps.current(rid)


def _path_reference(path: str, tenant_id: str, deps: Any) -> Optional[str]:
    """The tier the node runs on today (ADR-2308 decision 6). DAG engine: the cheapest tier (its
    alias). Console legacy path: the CLI's OWN default, which Corvin does not control and which
    differs per account — measured 2026-10-28 on this install it was a tier above the console's
    default (``high_model``), at about 100x the price of the cheapest tier. ``None`` until a
    per-install measurement exists; then no recommendation acts on that path."""
    return deps.legacy_default(tenant_id) if path == "legacy" else deps.tier("haiku")


def _is_stale(row: dict[str, Any], deps: Any) -> bool:
    """Derived on read, never stored: the measured id no longer is what the tier resolves to
    (lineage change), or the row names no known tier."""
    tier = row.get("tier")
    return tier not in TIER_NAMES or row.get("model_id") != deps.tier(tier)


def _recommendation(node, tenant_id, path, table, env, deps) -> Optional[Resolution]:
    """Level 3: only a tools-free node, only a row measured against this path's reference."""
    row = table.get(gate_key(node)) if table else None
    if not row or _has_tools(node):
        return None
    ref = _path_reference(path, tenant_id, deps)
    if ref is None or row.get("r") != ref:
        return None
    status = row.get("gate_status", "none")
    if status == "rolled_back":
        # a row that does not act leaves the node on level 4: the site default, else the path default
        return Resolution(env or "", "site_default", "rolled_back")
    if _is_stale(row, deps):
        return Resolution(env or "", "site_default", "stale")
    if status == "shadow":
        return Resolution(env or "", "recommendation_shadow", "shadow", row["model_id"])
    if status == "adopt" and not env and not deps.autoselect_is_off() \
            and deps.rank(row["model_id"]) < deps.rank(row["r"]):
        return Resolution(row["model_id"], "recommendation", "adopt")
    return None


def resolve_node_model(node: dict[str, Any], tenant_id: str, path: str,
                       table: Optional[dict[str, Any]] = None, *, deps: Any = None) -> Resolution:
    """Resolve the model for one node. Raises :class:`ModelUnknown` for an invalid node value."""
    if not _scoped(node):
        return Resolution("", "site_default", "none")
    deps = deps or _default_deps()
    pin = deps.pin(tenant_id)
    if pin:
        return Resolution(pin, "pin", "none")
    value = str(node.get("model") or "").strip()
    if value:
        return Resolution(_node_value(value, deps), "node", "none")
    env = deps.env()
    rec = _recommendation(node, tenant_id, path, table or {}, env, deps)
    if rec is not None:
        return rec
    return Resolution(env or "", "site_default", "none")


def operator_pin(tenant_id: str) -> str:
    """The operator's ``worker_model`` pin for *tenant_id*, normalised, or ``""``. Never raises."""
    try:
        return str(_default_deps().pin(tenant_id) or "")
    except Exception:  # noqa: BLE001 - a hint, never a failure
        return ""


def site_default() -> str:
    """The ``CORVIN_HELPER_MODEL_WORKFLOW_NODE`` site default (precedence level 4), or ``""``."""
    try:
        return str(_default_deps().env() or "")
    except Exception:  # noqa: BLE001 - a hint, never a failure
        return ""


def class_view(node: dict[str, Any]) -> dict[str, Any]:
    """The few node facts ``node_class`` reads, and nothing else (no instructions, no agent name),
    so it can travel on ``EngineCall.metadata`` without carrying the node."""
    return {"type": _node_type(node), "tools": [True] if _has_tools(node) else []}


def validate_node_model(value: Any, deps: Any = None) -> None:
    """Validate a ``model`` field at load time (ADR-2308 decision 1.2). ``ValueError`` on failure,
    which the validator turns into ``WorkflowInvalid`` (the console answers HTTP 400 on save).
    Absent or empty is valid."""
    if value in (None, ""):
        return
    if not isinstance(value, str):
        raise ValueError("model must be a string")
    try:
        _node_value(value.strip(), deps or _default_deps())
    except ModelUnknown:
        # save time: the operator's own editor gets the value back; nothing here is audited
        raise ValueError(f"unknown node model {value.strip()!r} ({ModelUnknown.reason_code})") from None


# Built-in node types that never call a model: a ``model:`` on one would read as if it did, and is
# rejected at validation. ``delegation_loop`` calls a model, but its manager picks the workers, so
# there is no node-level model to honour (ADR-2308 scope). Types registered by a plugin are not
# listed: their fields are theirs.
NO_MODEL_TYPES = frozenset({"delegation_loop", "deliver", "code", "merge", "answer", "ask_human",
                            "approval", "peer_agent"})


def in_scope(node: dict[str, Any]) -> bool:
    """Only these node types carry a model (ADR-2308 scope); every other type is left alone."""
    return _scoped(node)


def describe(node: dict[str, Any], tenant_id: str, path: str, wid: str, rid: str,
             item_index: Optional[int] = None) -> tuple[str, dict[str, Any]]:
    """Resolve a node's model for ONE spawn and describe it for the spawner.

    Returns ``(model_id, meta)``: ``model_id`` is what to pass as ``--model`` (empty keeps the
    argv this path always had), ``meta`` is what the spawner needs to write the audit event once
    the prompt exists (the context bucket needs its length). An out-of-scope node returns
    ``("", {})``: the spawn is exactly what it was before; so does a reader fault on a node that
    asked for no model. An invalid node value, or a reader fault on a node that names one, writes
    its event here (there is no prompt and no span) and raises :class:`ModelUnknown` /
    :class:`ModelUnresolved` - never a silent run on the path default."""
    if not in_scope(node):
        return "", {}
    try:
        res = resolve_node_model(node, tenant_id, path, {})
    except ModelUnknown:
        emit_node_model_resolved(
            tenant_id=tenant_id, wid=wid, rid=rid, node_id=str(node.get("id", "")),
            node_class=node_class(node, None), resolution=Resolution("", "node", "none"),
            item_index=item_index)
        raise
    except Exception as exc:  # noqa: BLE001
        if str(node.get("model") or "").strip():
            # the node asked for a model; running it on the path default instead would be a silent
            # substitution (and on the legacy path the most expensive one)
            _log.error("[workflow-engine] node model not resolved (%s); node fails", type(exc).__name__)
            emit_node_model_resolved(
                tenant_id=tenant_id, wid=wid, rid=rid, node_id=str(node.get("id", "")),
                node_class=node_class(node, None), resolution=Resolution("", "node", "none"),
                item_index=item_index)
            raise ModelUnresolved("node model could not be resolved (model_unresolved)") from None
        # nothing configured on the node: it keeps the spawn it always had
        _log.warning("[workflow-engine] node model not resolved (%s); node unchanged", type(exc).__name__)
        return "", {}
    meta: dict[str, Any] = {
        "model_resolution": res, "class_view": class_view(node), "workflow_id": wid,
        "run_id": rid, "tenant_id": tenant_id,
    }
    if item_index is not None:
        meta["item_index"] = item_index
    return res.model_id, meta


def stamp_call(call: Any, node: dict[str, Any], state: dict[str, Any], tenant_id: str,
               path: str = "dag", item_index: Optional[int] = None) -> Resolution:
    """Stamp the resolved model and the audit description onto an ``EngineCall`` (DAG engine)."""
    model, meta = describe(node, tenant_id, path, str(state.get("__workflow__", "")),
                           str(state.get("__run_id__", "")), item_index)
    call.model = model
    call.metadata.update(meta)
    return meta.get("model_resolution") or Resolution("", "site_default", "none")


def emit_node_model_resolved(*, tenant_id: str, wid: str, rid: str, node_id: str, node_class: str,
                             resolution: Resolution, span_id: str = "",
                             item_index: Optional[int] = None, escalated: bool = False) -> None:
    """Write ``workflow.node_model_resolved`` on the tenant's chain. Ids and closed enums only.
    A failing write is logged and never decides the node's outcome (it is measurement, not a gate)."""
    details: dict[str, Any] = {
        "tenant_id": tenant_id, "wid": wid, "rid": rid, "node_id": node_id, "node_class": node_class,
        "model_id": resolution.model_id, "source": resolution.source,
        "gate_status": resolution.gate_status, "escalated": bool(escalated), "span_id": span_id,
    }
    if item_index is not None:
        details["item_index"] = int(item_index)
    if resolution.shadow_model_id:
        details["shadow_model_id"] = resolution.shadow_model_id
    try:
        import forge.paths as _fp  # type: ignore[import-not-found]
        import forge.security_events as _se  # type: ignore[import-not-found]
        _se.write_event(_fp.tenant_audit_chain(tenant_id), "workflow.node_model_resolved", details=details)
    except Exception as exc:  # noqa: BLE001
        _log.error("[workflow-engine] node_model_resolved audit write failed (%s)", type(exc).__name__)
