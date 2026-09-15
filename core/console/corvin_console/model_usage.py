"""Per-model usage shares, aggregated from the tenant's real audit chain.

The Engine Configuration console shows which model served which share of real
work. There is no counter table behind that and there deliberately isn't one: the
hash-chained audit trail already records every turn, so a second store would be a
second truth that can disagree with it (and, per ADR-0232/0233, the chain is the
one that is legally load-bearing). This module reads the chain and counts.

Source events, all already emitted by the live turn path:

* ``engine.span.start`` / ``engine.span.end`` — ``role`` (os|worker), ``engine_id``,
  ``model_id``, ``status``, ``duration_ms``. The engine-level record, and the only
  one that covers DELEGATED worker turns.
* ``os_turn.completed`` — ``model``, ``input_tokens``, ``output_tokens``,
  ``cache_creation_input_tokens``, ``cache_read_input_tokens``, ``exit_code``.
  The only source of token counts.

Counting unit is the SPAN, keyed by ``span_id``, because an OS turn and the worker
turn it delegates to share a ``turn_id`` — keying on that would silently merge two
different models' work into one row. An ``os_turn`` whose span never landed (a
crash between the two writes) is still counted, keyed by ``turn_id``, so a real
turn is never dropped just because its span is missing.

Provider attribution is RESOLVED, never guessed. Each row carries
``provider_source`` naming which real lookup produced it — the operator can tell a
catalogue hit from a registry inference — and an id nothing matches stays
``unknown`` rather than being pattern-matched into a plausible-looking provider.

Read-only. Never raises: an absent chain is the honest zero state of a fresh
install, not an error.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_SPAN_START = "engine.span.start"
_SPAN_END = "engine.span.end"
_OS_TURN_COMPLETED = "os_turn.completed"
_OS_TURN_STARTED = "os_turn.started"

_INTERESTING = frozenset({_SPAN_START, _SPAN_END, _OS_TURN_COMPLETED, _OS_TURN_STARTED})

#: Stand-in id for a real engine invocation whose emitter did not record WHICH
#: model ran. Deliberately not a plausible model id and deliberately not
#: silently dropped — see _collect. Provider attribution resolves it to
#: "unknown"/"unresolved" like any other unrecognised id.
UNREPORTED_MODEL = "(model not reported)"


@dataclass
class _Observation:
    """One unit of real work: a span, or a span-less OS turn."""

    model_id: str = ""
    role: str = ""
    engine_id: str = ""
    status: str = ""
    duration_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    #: True once an end/completed event was seen. A start-only observation is a
    #: turn still running (or one whose process died) — counted, but its status
    #: stays "" so it is not silently reported as a success.
    finished: bool = False


@dataclass
class _ModelRow:
    model_id: str
    turns: int = 0
    ok: int = 0
    failed: int = 0
    unfinished: int = 0
    total_duration_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    roles: dict[str, int] = field(default_factory=dict)
    engines: dict[str, int] = field(default_factory=dict)
    first_seen: float = 0.0
    last_seen: float = 0.0


# ---------------------------------------------------------------------------
# Chain reading
# ---------------------------------------------------------------------------


def _chain_path(tenant_id: str) -> Path | None:
    """Resolve the ONE audit chain for this tenant through the shared resolver.

    Composing this path by hand is explicitly forbidden (six divergent chain
    files for `_default` were measured that way — ADR-0650), so a missing
    resolver means "cannot read", not "compose a guess".
    """
    try:
        from core.paths import tenant_audit_chain  # noqa: PLC0415
        return tenant_audit_chain(tenant_id)
    except Exception:  # noqa: BLE001
        return None


def _iter_events(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                # Cheap reject before json.loads: the chain holds thousands of
                # license/threshold records this module has no use for.
                if '"event_type"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:  # noqa: BLE001 — a torn last line is normal
                    continue
                if isinstance(record, dict) and record.get("event_type") in _INTERESTING:
                    yield record
    except OSError:
        return


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _as_float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _collect(path: Path) -> dict[str, _Observation]:
    """Fold the chain into one observation per span (or span-less OS turn)."""
    spans: dict[str, _Observation] = {}
    os_turns: dict[str, _Observation] = {}
    #: turn_id → span_id of its os-role span, so token counts from
    #: os_turn.completed land on the span that actually did the work.
    os_span_of_turn: dict[str, str] = {}

    for record in _iter_events(path):
        event_type = record.get("event_type")
        details = record.get("details") or {}
        if not isinstance(details, dict):
            continue
        ts = _as_float(record.get("ts"))

        if event_type in (_SPAN_START, _SPAN_END):
            span_id = str(details.get("span_id") or "")
            if not span_id:
                continue
            obs = spans.setdefault(span_id, _Observation())
            obs.model_id = str(details.get("model_id") or "") or obs.model_id
            obs.role = str(details.get("role") or "") or obs.role
            obs.engine_id = str(details.get("engine_id") or "") or obs.engine_id
            if ts:
                obs.first_ts = min(obs.first_ts, ts) if obs.first_ts else ts
                obs.last_ts = max(obs.last_ts, ts)
            turn_id = str(details.get("turn_id") or "")
            if turn_id and obs.role == "os":
                os_span_of_turn[turn_id] = span_id
            if event_type == _SPAN_END:
                obs.finished = True
                obs.status = str(details.get("status") or "") or obs.status
                obs.duration_ms = _as_float(details.get("duration_ms")) or obs.duration_ms
                # ADR-0759 — a WORKER span is the only record of a delegated
                # turn's token use: there is no os_turn.completed behind it to
                # fold in below. Read the split here or the row reports real
                # turns and zero tokens, which reads as "delegation is free".
                # Guarded on role so an os-role span cannot double-count what
                # its own os_turn.completed already contributed.
                if obs.role != "os":
                    obs.input_tokens += _as_int(details.get("input_tokens"))
                    obs.output_tokens += _as_int(details.get("output_tokens"))
                    obs.cache_read_tokens += _as_int(details.get("cache_read_tokens"))
                    obs.cache_write_tokens += _as_int(details.get("cache_write_tokens"))
            continue

        turn_id = str(details.get("turn_id") or "")
        if not turn_id:
            continue
        target = spans.get(os_span_of_turn.get(turn_id, "")) or os_turns.setdefault(
            turn_id, _Observation(role="os")
        )
        target.model_id = target.model_id or str(details.get("model") or "")
        if ts:
            target.first_ts = min(target.first_ts, ts) if target.first_ts else ts
            target.last_ts = max(target.last_ts, ts)
        if event_type == _OS_TURN_COMPLETED:
            target.finished = True
            target.input_tokens += _as_int(details.get("input_tokens"))
            target.output_tokens += _as_int(details.get("output_tokens"))
            target.cache_read_tokens += _as_int(details.get("cache_read_input_tokens"))
            target.cache_write_tokens += _as_int(details.get("cache_creation_input_tokens"))
            target.duration_ms = target.duration_ms or _as_float(details.get("duration_ms"))
            if not target.status:
                target.status = "ok" if _as_int(details.get("exit_code")) == 0 else "error"

    # A turn that got an os-role span contributed its tokens to that span above;
    # keeping its os_turns entry too would count the turn twice.
    for turn_id in os_span_of_turn:
        os_turns.pop(turn_id, None)

    merged: dict[str, _Observation] = dict(spans)
    for turn_id, obs in os_turns.items():
        merged[f"turn:{turn_id}"] = obs
    # A span with no model_id used to be DROPPED here. That silently deleted
    # real work from every roll-up: the gateway's worker spans carried no
    # model_id at all until 2026-09-15, so 100% of delegated turns vanished and
    # the panel reported an install that only ever ran OS turns. An id we do not
    # have is not the same as a turn that did not happen — keep the observation
    # and say so. Only an observation with NEITHER an id NOR a role is dropped,
    # because that carries no information at all.
    out: dict[str, _Observation] = {}
    for key, obs in merged.items():
        if not obs.model_id:
            if not obs.role:
                continue
            obs.model_id = UNREPORTED_MODEL
        out[key] = obs
    return out


# ---------------------------------------------------------------------------
# Provider attribution
# ---------------------------------------------------------------------------


def _provider_index(tenant_id: str) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Build ``model_id -> (provider_id, how_it_was_resolved)`` from real sources.

    Three, written weakest-first so a stronger source overwrites a weaker one:

    1. the ADR-0119 registry — an engine's curated ``os_models``/``worker_models``
       plus the ``live_models.provider`` that engine drives. Weakest, because it is
       a shipped snapshot, but it is real configuration and it covers local
       engines that expose no catalogue.
    2. this tenant's saved ADR-0641 selection — the ``(selected_model, provider)``
       pairs the operator assigned through this very console. Operator-authored,
       so stronger than anything shipped; it is also the ONLY offline source that
       covers an Ollama/OpenAI model, because assigning an external provider is
       how such a model comes to serve a turn in the first place.
    3. ``model_catalog`` — what a provider ANSWERED when it was last asked
       (written only by a successful live fetch). Strongest: an id present here
       was offered by that provider, and nothing beats that short of asking again.

    Also returns ``provider_id -> label`` for display.
    """
    index: dict[str, tuple[str, str]] = {}
    labels: dict[str, str] = {}

    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        for provider_id, spec in load_providers(force_reload=False).items():
            labels[provider_id] = getattr(spec, "label", provider_id) or provider_id
    except Exception:  # noqa: BLE001
        pass

    try:
        from engine_models import load_registry  # type: ignore[import]  # noqa: PLC0415
        registry = load_registry(force_reload=False)
    except Exception:  # noqa: BLE001
        registry = {}

    for spec in registry.values():
        live = getattr(spec, "live_models", None)
        provider_id = getattr(live, "provider", "") if live is not None else ""
        if not provider_id:
            continue
        for role_attr in ("os_models", "worker_models"):
            for entry in getattr(spec, role_attr, None) or []:
                entry_id = getattr(entry, "id", "")
                if entry_id and entry_id not in index:
                    index[entry_id] = (provider_id, "registry")

    try:
        from core.models.model_selection_config import load_config  # noqa: PLC0415
        for entry in load_config(tenant_id).values():
            model_id = str(entry.get("selected_model") or "")
            provider_id = str(entry.get("provider") or "")
            if model_id and provider_id:
                index[model_id] = (provider_id, "tenant_config")
    except Exception:  # noqa: BLE001
        pass

    try:
        import model_catalog  # type: ignore[import]  # noqa: PLC0415
        for provider_id in list(labels) or ["anthropic", "bedrock"]:
            for entry in model_catalog.catalog_models(provider_id) or []:
                entry_id = entry.get("id") if isinstance(entry, dict) else None
                if entry_id:
                    index[entry_id] = (provider_id, "live_catalog")
    except Exception:  # noqa: BLE001
        pass

    return index, labels


def _engine_config_provider(
    tenant_id: str, model_id: str, engines: dict[str, int], roles: dict[str, int]
) -> str:
    """Provider from the tenant's per-engine ADR-0181 assignment, or "".

    Only claimed on an EXACT match: the engine that ran the span must currently
    have this very ``model_id`` configured for one of the roles the span ran in.
    A same-engine/different-model assignment says nothing about this row, and two
    of the row's engines disagreeing means the row is genuinely ambiguous — both
    return "" rather than picking a side.

    This is the current assignment applied to a past span, so it is an inference,
    not a measurement: callers surface it as ``engine_config``, distinct from a
    catalogue hit.
    """
    try:
        from engine_models import (  # type: ignore[import]  # noqa: PLC0415
            get_tenant_engine_model,
            get_tenant_engine_provider,
        )
    except Exception:  # noqa: BLE001
        return ""

    found: set[str] = set()
    for engine_id in engines:
        try:
            configured = {
                get_tenant_engine_model(tenant_id, engine_id, f"{role}_model")
                for role in roles or {"os": 1}
            }
            if model_id not in configured:
                continue
            provider_id = get_tenant_engine_provider(tenant_id, engine_id)
        except Exception:  # noqa: BLE001
            continue
        if provider_id:
            found.add(provider_id)
    return found.pop() if len(found) == 1 else ""


def _resolve_provider(
    model_id: str,
    index: dict[str, tuple[str, str]],
    provider_ids: set[str],
) -> tuple[str, str]:
    hit = index.get(model_id)
    if hit is not None:
        return hit
    # An engine that addresses models as "<provider>/<id>" (OpenCode's
    # live_models.prefix) encodes the provider in the id itself — reading that
    # back is parsing, not guessing. Compared against the REGISTERED provider
    # ids, not against providers that happen to have a model in the index: the
    # latter made the branch dead for exactly the providers it was written for
    # (nothing had ever fetched an openai/ollama catalogue, so no openai model
    # was in the index, so "openai/gpt-5" fell through to unknown).
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0]
        if prefix in provider_ids:
            return prefix, "id_prefix"
        # "ollama/…" addresses the ollama_local / ollama_cloud pair; accept it
        # only when exactly one registered id carries that family name.
        family = {pid for pid in provider_ids if pid.split("_", 1)[0] == prefix}
        if len(family) == 1:
            return family.pop(), "id_prefix"
    return "unknown", "unresolved"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _share(part: float, whole: float) -> float:
    return round(part * 100.0 / whole, 2) if whole else 0.0


def model_usage(tenant_id: str) -> dict[str, Any]:
    """Return per-model and per-provider usage shares for one tenant.

    Every number is a count or a sum of counts taken from the audit chain; there
    is no estimation step and no default row. A tenant with no turns yet gets
    empty lists and ``chain_readable`` telling the UI whether that is "no work
    yet" or "the chain could not be read".
    """
    path = _chain_path(tenant_id)
    result: dict[str, Any] = {
        "tenant_id": tenant_id,
        "chain_path_resolved": path is not None,
        "chain_readable": bool(path is not None and path.exists()),
        "models": [],
        "providers": [],
        "roles": [],
        "totals": {
            "turns": 0, "ok": 0, "failed": 0, "unfinished": 0,
            "total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        },
    }
    if path is None or not path.exists():
        return result

    observations = _collect(path)
    rows: dict[str, _ModelRow] = {}
    for obs in observations.values():
        row = rows.setdefault(obs.model_id, _ModelRow(model_id=obs.model_id))
        row.turns += 1
        if not obs.finished:
            row.unfinished += 1
        elif obs.status and obs.status != "ok":
            row.failed += 1
        else:
            row.ok += 1
        row.total_duration_ms += obs.duration_ms
        row.input_tokens += obs.input_tokens
        row.output_tokens += obs.output_tokens
        row.cache_read_tokens += obs.cache_read_tokens
        row.cache_write_tokens += obs.cache_write_tokens
        if obs.role:
            row.roles[obs.role] = row.roles.get(obs.role, 0) + 1
        if obs.engine_id:
            row.engines[obs.engine_id] = row.engines.get(obs.engine_id, 0) + 1
        if obs.first_ts:
            row.first_seen = min(row.first_seen, obs.first_ts) if row.first_seen else obs.first_ts
        if obs.last_ts:
            row.last_seen = max(row.last_seen, obs.last_ts)

    index, labels = _provider_index(tenant_id)
    provider_ids = set(labels)
    total_turns = sum(r.turns for r in rows.values())
    total_tokens_all = sum(
        r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_write_tokens
        for r in rows.values()
    )

    models: list[dict[str, Any]] = []
    provider_acc: dict[str, dict[str, Any]] = {}
    for row in rows.values():
        provider_id, provider_source = _resolve_provider(row.model_id, index, provider_ids)
        if provider_id == "unknown":
            from_engine = _engine_config_provider(
                tenant_id, row.model_id, row.engines, row.roles
            )
            if from_engine:
                provider_id, provider_source = from_engine, "engine_config"
        total_tokens = (
            row.input_tokens + row.output_tokens + row.cache_read_tokens + row.cache_write_tokens
        )
        models.append({
            "model_id": row.model_id,
            "provider": provider_id,
            "provider_label": labels.get(provider_id, provider_id),
            # How the provider was determined: live_catalog | tenant_config |
            # registry | id_prefix | engine_config | unresolved. Surfaced so a
            # weak attribution is visible as such instead of reading like a
            # measurement.
            "provider_source": provider_source,
            "turns": row.turns,
            "share_pct": _share(row.turns, total_turns),
            "ok": row.ok,
            "failed": row.failed,
            "unfinished": row.unfinished,
            "success_pct": _share(row.ok, row.ok + row.failed),
            "avg_duration_ms": round(row.total_duration_ms / row.turns, 1) if row.turns else 0.0,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cache_read_tokens": row.cache_read_tokens,
            "cache_write_tokens": row.cache_write_tokens,
            "total_tokens": total_tokens,
            "token_share_pct": _share(total_tokens, total_tokens_all),
            "roles": dict(sorted(row.roles.items())),
            "engines": sorted(row.engines),
            "first_seen": row.first_seen or None,
            "last_seen": row.last_seen or None,
        })
        acc = provider_acc.setdefault(
            provider_id,
            {"provider": provider_id, "provider_label": labels.get(provider_id, provider_id),
             "turns": 0, "total_tokens": 0, "models": 0},
        )
        acc["turns"] += row.turns
        acc["total_tokens"] += total_tokens
        acc["models"] += 1

    models.sort(key=lambda m: (-m["turns"], m["model_id"]))
    providers = sorted(provider_acc.values(), key=lambda p: (-p["turns"], p["provider"]))
    for entry in providers:
        entry["share_pct"] = _share(entry["turns"], total_turns)
        entry["token_share_pct"] = _share(entry["total_tokens"], total_tokens_all)

    # ── By role (ADR-0759) ────────────────────────────────────────────────
    # The OS turn and the WORKER turn it delegates to are two different engines
    # running two different models against two different budgets, and the panel
    # showed them added together. On a delegating install that is the number an
    # operator least wants: the OS row is chatty and cheap, the worker row is
    # where the capable model and the real spend are. Rolled up here, from the
    # SAME observations as everything above, so the two views cannot disagree.
    role_acc: dict[str, dict[str, Any]] = {}
    for obs in observations.values():
        role = obs.role or "unknown"
        acc = role_acc.setdefault(role, {
            "role": role, "turns": 0, "ok": 0, "failed": 0, "unfinished": 0,
            "total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "total_duration_ms": 0.0,
            "_models": set(), "_engines": set(),
        })
        acc["turns"] += 1
        if not obs.finished:
            acc["unfinished"] += 1
        elif obs.status and obs.status != "ok":
            acc["failed"] += 1
        else:
            acc["ok"] += 1
        acc["input_tokens"] += obs.input_tokens
        acc["output_tokens"] += obs.output_tokens
        acc["cache_read_tokens"] += obs.cache_read_tokens
        acc["cache_write_tokens"] += obs.cache_write_tokens
        acc["total_duration_ms"] += obs.duration_ms
        if obs.model_id:
            acc["_models"].add(obs.model_id)
        if obs.engine_id:
            acc["_engines"].add(obs.engine_id)

    roles_out: list[dict[str, Any]] = []
    for acc in role_acc.values():
        acc["total_tokens"] = (acc["input_tokens"] + acc["output_tokens"]
                               + acc["cache_read_tokens"] + acc["cache_write_tokens"])
        acc["models"] = sorted(acc.pop("_models"))
        acc["engines"] = sorted(acc.pop("_engines"))
        acc["share_pct"] = _share(acc["turns"], total_turns)
        acc["token_share_pct"] = _share(acc["total_tokens"], total_tokens_all)
        acc["success_pct"] = _share(acc["ok"], acc["ok"] + acc["failed"])
        acc["avg_duration_ms"] = (
            round(acc.pop("total_duration_ms") / acc["turns"], 1) if acc["turns"] else 0.0
        )
        # A role with real turns and zero tokens is NOT a role that costs
        # nothing — it is a role whose emitter does not report usage. Named so
        # the UI can say which, instead of rendering a confident $0.
        acc["tokens_reported"] = acc["total_tokens"] > 0
        roles_out.append(acc)
    roles_out.sort(key=lambda r: (-r["turns"], r["role"]))

    result["models"] = models
    result["providers"] = providers
    result["roles"] = roles_out
    result["totals"] = {
        "turns": total_turns,
        "ok": sum(r.ok for r in rows.values()),
        "failed": sum(r.failed for r in rows.values()),
        "unfinished": sum(r.unfinished for r in rows.values()),
        "total_tokens": total_tokens_all,
        "input_tokens": sum(r.input_tokens for r in rows.values()),
        "output_tokens": sum(r.output_tokens for r in rows.values()),
        "cache_read_tokens": sum(r.cache_read_tokens for r in rows.values()),
        "cache_write_tokens": sum(r.cache_write_tokens for r in rows.values()),
    }
    return result
