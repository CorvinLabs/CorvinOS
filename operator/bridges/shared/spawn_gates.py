"""spawn_gates.py — Canonical L44 (acceptable-use) + L34 (data-classification)
+ L35 (egress) pre-spawn gate checks.

ADR-0158 M1: Single Source of Truth for spawn-gate logic shared across
adapter.py, acs_runtime.py, the owner console, and any future spawn sites.

Invariants:
  * Must NOT import anthropic (CI AST lint gate).
  * Audit-first: gate denials are audited by the library functions
    (HouseRulesGate.classify / DataFlowGuard.validate / EgressGate.validate)
    before returning.
  * Fail-open on operational errors for L34/L35 (missing module, malformed
    config). ONLY explicit policy denials return an error string — operational
    errors return None (allowed) to preserve the pre-ADR-0158 contract.
  * Fail-CLOSED for L44 (ADR-0143): the acceptable-use gate is MANDATORY. A
    missing module, a tampered/unparseable policy, or any gate/classifier
    error all REFUSE the turn (return a refusal string) — an acceptable-use
    guarantee must never evaporate into fail-open.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

# ── Module-level mtime-keyed caches (moved from adapter.py) ──────────────────
# Cache key: "{tenant_id}:{corvin_home}" — same tenant under different homes
# is treated as a distinct entry (e.g. test isolation via CORVIN_HOME).

_l34_cache: dict[str, dict] = {}
_l34_cache_lock = threading.Lock()
_l35_cache: dict[str, dict] = {}
_l35_cache_lock = threading.Lock()
# L44 caches the per-tenant house-rules OVERLAY load only (mtime-keyed on
# tenant.corvin.yaml), mirroring L34/L35. The repo baseline policy is loaded
# uncached inside HouseRulesGate.from_repo on every call — that read+hash is
# TOCTOU-closed by design (ADR-0143) and must NOT be cached here.
_l44_overlay_cache: dict[str, dict] = {}
_l44_overlay_cache_lock = threading.Lock()


def _resolve_corvin_home(corvin_home: Path | None) -> Path:
    """Return a resolved corvin home path, expanding env vars and ~."""
    if corvin_home is not None:
        return Path(os.path.expanduser(os.path.expandvars(str(corvin_home))))
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    try:
        from forge.paths import corvin_home as _ch  # type: ignore
        return _ch()
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _load_l34_guard(tenant_id: str, corvin_home: Path | None):
    """Return a mtime-cached DataFlowGuard for *tenant_id*, or None.

    Cache is invalidated when ``tenant.corvin.yaml`` mtime changes.
    Operational errors (missing module, unreadable yaml) fail-open → None.
    """
    home = _resolve_corvin_home(corvin_home)
    cfg_path = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    try:
        mtime = cfg_path.stat().st_mtime if cfg_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    cache_key = f"{tenant_id}:{home}"
    with _l34_cache_lock:
        cached = _l34_cache.get(cache_key)
        if cached is not None and cached.get("mtime") == mtime:
            return cached.get("guard")

    guard = None
    load_error = False
    if mtime > 0.0:
        try:
            from data_classification import load_guard_for_tenant  # type: ignore
            guard = load_guard_for_tenant(tenant_id, corvin_home=home)
        except Exception as exc:  # noqa: BLE001
            _log.warning("spawn_gates: L34 guard load failed (%r) — re-evaluate next call", exc)
            guard = None
            load_error = True

    # Only cache a DEFINITIVE result. A transient load error (config present but
    # the loader raised) must NOT be cached as a permanent None-allow — otherwise
    # a single flaky read fails-open for every subsequent spawn until the yaml
    # mtime changes. Leave the cache untouched so the next call re-evaluates.
    if not load_error:
        with _l34_cache_lock:
            _l34_cache[cache_key] = {"mtime": mtime, "guard": guard}
    return guard


# ── Public API ────────────────────────────────────────────────────────────────


def check_l34(
    engine_id: str,
    tenant_id: str,
    *,
    classification: "str | None" = None,
    prompt: "str | None" = None,
    persona: "str | None" = None,
    channel: str = "",
    chat_key: str = "",
    corvin_home: "Path | None" = None,
    cc_local_mode: bool = False,
) -> "str | None":
    """ADR-0042 / L34 pre-spawn data-classification gate.

    Returns ``None`` when the spawn is permitted (gate passes, no tenant
    config, or operational error — fail-open).  Returns a user-facing
    refusal string when the gate explicitly denies.

    Two classification modes:

    * ``classification`` (str, e.g. ``"internal"``) — caller-declared
      level, used by acs_runtime / A2A spawn sites.
    * ``prompt`` + ``persona`` — heuristic classification via
      ``classify_task()``, used by the OS-turn adapter path.

    ``cc_local_mode=True`` remaps ``claude_code`` → ``claude_code_local``
    for ADR-0126 Ollama-redirect deployments.

    The ``DataFlowGuard.validate()`` call emits the ``data_flow.approved``
    or ``data_flow.blocked`` L16 audit event before this function returns.
    """
    if not isinstance(engine_id, str) or not engine_id:
        _log.debug("spawn_gates.check_l34: engine_id missing — fail-open")
        return None

    validate_id = engine_id
    if cc_local_mode and engine_id == "claude_code":
        validate_id = "claude_code_local"

    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    guard = _load_l34_guard(tenant, corvin_home)
    if guard is None:
        return None

    if classification is None:
        try:
            from data_classification import classify_task  # type: ignore
            cls_val = classify_task(prompt or "", persona=persona)
        except Exception as exc:  # noqa: BLE001
            # Fail CLOSED (round-2): a tenant policy IS present (guard is not
            # None) but the task could not be classified — we cannot prove it is
            # allowed against the residency matrix, so deny rather than wave it
            # through. Consistent with the validate handler below and check_l35.
            # (The legitimate no-policy opt-in case is the guard-is-None early
            # return above, which stays fail-open by design.)
            _log.warning("spawn_gates.check_l34: classify_task failed (%r) — fail-closed", exc)
            return (
                "[data-flow] Spawn rejected: task classification failed for engine "
                f"'{engine_id}' (gate error) — fail-closed per tenant data-"
                "classification policy. Operator: check the classifier / "
                "tenant.corvin.yaml::spec.data_classification."
            )
    else:
        cls_val = classification

    try:
        decision = guard.validate(
            classification=cls_val,
            engine_id=validate_id,
            persona=persona,
            channel=channel or None,
            chat_key=chat_key or None,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail CLOSED (M8): a broken data-flow guard must not wave a spawn through
        # unchecked. This now matches the sibling check_l35 validate handler
        # (~538-547), which also refuses on a validate error — the two gates agree.
        _log.warning("spawn_gates.check_l34: validate failed (%r) — fail-closed", exc)
        cls_name = getattr(cls_val, "name", str(cls_val))
        return (
            f"[data-flow] Spawn rejected: classification {cls_name} could not be "
            f"validated for engine '{engine_id}' (gate error). Fail-closed per "
            f"tenant data-classification policy. "
            f"Operator: check tenant.corvin.yaml::spec.data_classification."
        )

    if decision.allowed:
        return None

    cls_name = getattr(cls_val, "name", str(cls_val))
    _log.info(
        "[spawn-gate/L34] denied engine=%s classification=%s reason=%s "
        "channel=%s chat=%s",
        engine_id, cls_name, decision.reason, channel, chat_key,
    )
    return (
        f"[data-flow] Spawn rejected: Classification {cls_name} is not "
        f"allowed with engine '{engine_id}'. {decision.reason} "
        f"Operator policy: tenant.corvin.yaml::spec.data_classification."
    )


# ── L44 acceptable-use (house-rules) — ADR-0143, MANDATORY, fail-CLOSED ───────


def _l44_audit_path(tenant_id: str, corvin_home: "Path | None") -> Path:
    """Per-tenant L16 forge audit chain — ``<tenant_home>/global/forge/audit.jsonl``.

    ``VOICE_AUDIT_PATH`` wins when set (test isolation / explicit operator
    override), matching the adapter + console. Otherwise resolve the
    authenticated tenant's forge chain via ``forge.paths.tenant_global_dir``
    (the console is multi-tenant, so L44 audits land on the caller's tenant
    chain, not a single global one)."""
    env_p = os.environ.get("VOICE_AUDIT_PATH")
    if env_p:
        return Path(env_p)
    from forge import paths as _forge_paths  # type: ignore
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def _load_l44_overlay(tenant_id: str, corvin_home: "Path | None"):
    """Return a mtime-cached house-rules tenant overlay for *tenant_id*, or None.

    Mirrors ``_load_l34_guard``/``_load_l35_gate`` — same mtime-key, cached on
    ``tenant.corvin.yaml`` mtime. The overlay can only STRENGTHEN the repo
    baseline (``policy.merge_stricter``), so an unreadable/malformed overlay
    fail-soft → None (the repo baseline still applies — the policy as a whole
    remains fail-closed because ``from_repo`` is uncached and re-verified)."""
    home = _resolve_corvin_home(corvin_home)
    cfg_path = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    try:
        mtime = cfg_path.stat().st_mtime if cfg_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    cache_key = f"{tenant_id}:{home}"
    with _l44_overlay_cache_lock:
        cached = _l44_overlay_cache.get(cache_key)
        if cached is not None and cached.get("mtime") == mtime:
            return cached.get("overlay")

    overlay = None
    if mtime > 0.0:
        try:
            import house_rules as _hr  # type: ignore
            overlay = _hr.load_tenant_overlay(tenant_id, corvin_home=home)
        except Exception as exc:  # noqa: BLE001 — overlay can only strengthen → fail-soft
            _log.warning("spawn_gates: L44 overlay load failed (%r) — repo baseline only", exc)
            overlay = None

    with _l44_overlay_cache_lock:
        _l44_overlay_cache[cache_key] = {"mtime": mtime, "overlay": overlay}
    return overlay


def _safe_classify(task: str, rules: dict, auth: dict, _hr, audit_write_fn, tenant_id: str):
    """Classifier wrapper — a thin passthrough that PROPAGATES errors.

    It must NOT swallow a classifier failure into a conservative *allow*: the L44
    acceptable-use gate is fail-CLOSED (CLAUDE.md compliance red-line + the
    check_l44 docstring — "ANY gate / classifier error all REFUSE the turn").
    Returning allowed=True here previously defeated that contract: gate.classify
    got a benign decision, so check_l44's classify-error handler never fired and
    the turn was waved through unchecked.

    By re-raising, the exception reaches check_l44's Step-5 handler, which emits
    house_rules.escalated(reason=classifier_error) and returns the neutral
    "couldn't be safety-checked" escalate message — blocking the turn.
    """
    return _hr._house_rules_classifier(
        task, rules, auth, audit_write=audit_write_fn, tenant_id=tenant_id
    )


def check_l44(
    prompt: "str | None",
    tenant_id: str,
    *,
    persona: str = "assistant",
    channel: str = "",
    chat_key: str = "",
    engine_id: str = "claude_code",
    corvin_home: "Path | None" = None,
) -> "str | None":
    """ADR-0143 / L44 acceptable-use (house-rules) pre-spawn gate — MANDATORY.

    Returns ``None`` when the task is PERMITTED (``allow`` / ``warn``). Returns
    a user-facing refusal string on ``deny`` or ``escalate``. This is the SINGLE
    L44 implementation shared by the bridge adapter, acs_runtime and the owner
    console (which delegates here).

    Fail-CLOSED contract (unlike L34/L35): a missing ``house_rules`` /
    ``egress_gate`` module, a tampered/unparseable policy, or ANY gate /
    classifier error all REFUSE the turn — an acceptable-use guarantee must
    never fail-open. (The ADR-0141 Tier-3 capability gate asserts house-rules
    presence independently.)

    Audit-first: ``gate.classify`` emits exactly one ``house_rules.{allowed,
    warned,escalated,denied}`` L16 event synchronously (via the injected
    per-tenant forge writer) BEFORE this returns, so the deny/escalate event
    lands on the chain before the refusal string.

    Metadata-only: the emitted event carries rule_id / action / reason-code /
    confidence — NEVER the task text (GDPR/PII floor).

    Two-way escalate wording: a transient ``classifier_error`` /
    ``clear_low_confidence`` verdict is NOT a finding against the user's content
    — it gets a neutral "couldn't be safety-checked just now, try again"
    message. A genuine borderline/violation verdict gets the operator-approval
    wording. Either way the turn stays BLOCKED.
    """
    task = prompt or ""
    if not task.strip():
        return None  # nothing to classify (status pings, empty resumes) — defensive

    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"

    # ── Robust import with degradation ────────────────────────────────────────
    try:
        import house_rules as _hr  # type: ignore
        from egress_gate import make_forge_audit_writer as _mk_writer  # type: ignore
    except Exception as _imp_exc:  # noqa: BLE001 — mandatory layer absent → fail closed
        _log.error("[house-rules] module import failed (%s) — fail-closed deny",
                   type(_imp_exc).__name__)
        return ("[house-rules] Acceptable-use gate unavailable — request blocked "
                "(fail-closed). Contact the operator.")

    # ── Robust classification with fallbacks ──────────────────────────────────
    try:
        # Step 1: Load overlay (best-effort)
        try:
            overlay = _load_l44_overlay(tenant, corvin_home)
        except Exception as _overlay_exc:  # noqa: BLE001 — overlay is optional
            _log.debug("[house-rules] overlay load failed (%s) — continuing with baseline",
                      type(_overlay_exc).__name__)
            overlay = None

        # Step 2: Setup audit writer (must succeed)
        try:
            _audit_write = _mk_writer(_l44_audit_path(tenant, corvin_home))
        except Exception as _audit_exc:  # noqa: BLE001 — fallback to null writer
            _log.warning("[house-rules] audit writer creation failed (%s) — using fallback",
                        type(_audit_exc).__name__)
            def _audit_write(event_type: str, severity: str, details: dict) -> None:
                pass  # Silent fallback — audit disabled but gate continues

        # Step 3: Bridge audit arity (2-arg vs 3-arg)
        def _hr_classifier_audit(event_type: str, details: dict) -> None:
            try:
                from forge.security_events import EVENT_SEVERITY as _ev_sev  # type: ignore
                severity = _ev_sev.get(event_type, "INFO")
            except Exception:  # noqa: BLE001 — severity lookup is best-effort
                severity = "INFO"
            try:
                _audit_write(event_type, severity, details)
            except Exception:  # noqa: BLE001 — audit write is best-effort
                pass

        # Step 4: Create gate (with fallback classifier)
        try:
            gate = _hr.HouseRulesGate.from_repo(
                audit_writer=_audit_write,
                classifier=lambda task, rules, auth: _safe_classify(
                    task, rules, auth, _hr, _hr_classifier_audit, tenant
                ),
                tenant_overlay=overlay,
            )
        except Exception as _gate_create_exc:  # noqa: BLE001 — gate creation failed
            # FAIL-CLOSED (ADR-0143 + CLAUDE.md red-line): the acceptable-use gate
            # is MANDATORY. If it cannot be constructed we have NO classification —
            # returning None here (the old "conservative allow") waved the turn
            # through unchecked, defeating the module's fail-closed contract
            # (lines 15-18). DENY the spawn, mirroring the shape of a normal L44
            # policy denial, and emit a metadata-only audit event first.
            _log.error("[house-rules] gate creation failed (%s) — fail-closed deny",
                       type(_gate_create_exc).__name__)
            try:
                _hr_classifier_audit("house_rules.denied", {
                    "reason": "gate_construction_error",
                    "error_type": type(_gate_create_exc).__name__,
                })
            except Exception:  # noqa: BLE001 — audit is best-effort, never blocks the deny
                pass
            return (
                "[house-rules] Acceptable-use gate unavailable — request blocked "
                "(fail-closed). Contact the operator."
            )

        # Step 5: Classify (with degradation fallback)
        try:
            decision = gate.classify(
                task, persona=persona or "", channel=channel, chat_key=chat_key,
                engine_id=engine_id,
            )
        except Exception as _classify_exc:  # noqa: BLE001 — classify failed
            _log.warning("[house-rules] classify() failed (%s) — escalating",
                        type(_classify_exc).__name__)
            # Return transient escalate (user gets try-again, not hard deny)
            try:
                _hr_classifier_audit("house_rules.escalated", {
                    "reason": "classifier_error",
                    "error_type": type(_classify_exc).__name__,
                })
            except Exception:  # noqa: BLE001
                pass
            return (
                "[house-rules] This request couldn't be safety-checked just now — "
                "try again in a moment."
            )

    except Exception as _outer_exc:  # noqa: BLE001 — catch-all safety net
        _log.error("[house-rules] unexpected outer error (%s) — fail-closed deny",
                   type(_outer_exc).__name__)
        return ("[house-rules] Acceptable-use gate error — request blocked "
                "(fail-closed). Restart the bridge/console if this persists.")

    if decision.allowed:
        return None
    # Metadata-only log: rule_id + action + reason code + confidence — NEVER the
    # task text (decision.reason is a controlled vocabulary code, not free text).
    _log.warning("[house-rules] %s rule=%s reason_code=%s conf=%.2f channel=%s chat=%s",
                 decision.action, decision.rule_id or "-", decision.reason,
                 decision.confidence, channel, chat_key)
    rid = decision.rule_id or "acceptable-use"
    if decision.action == "escalate":
        # A transient classifier failure / low-confidence CLEAR is NOT a finding
        # against the user's content — give it a neutral try-again message.
        # Reserve operator-approval wording for a genuine borderline/violation
        # verdict. Either way the turn is still blocked (non-None return).
        if decision.reason in ("classifier_error", "clear_low_confidence"):
            # M4: track classifier_error in the degradation window (ADR-0157 M4).
            # clear_low_confidence is intentionally NOT tracked — it is a verdict-
            # quality signal (model uncertain but healthy), not a health failure.
            if decision.reason == "classifier_error":
                try:
                    _hr._house_rules_track_degradation(audit_write=_hr_classifier_audit)
                except Exception:  # noqa: BLE001 — observability never blocks the gate
                    pass
                return (
                    "[house-rules] This request couldn't be safety-checked just now — "
                    "the automated acceptable-use check was inconclusive (it did not "
                    "flag your request). Please send it again in a moment; if it keeps "
                    "happening an operator will review it."
                )
            # clear_low_confidence: classifier ran successfully and did NOT flag the
            # request — it was merely uncertain. Allow through silently so normal
            # questions are never blocked by classifier confidence noise.
            return None
        return (
            f"[house-rules] This request needs operator approval before it can run "
            f"(rule '{rid}'). It touches a restricted or uncertain area. "
            f"An operator must approve it."
        )
    return (
        f"[house-rules] This request is not permitted by the operator's "
        f"acceptable-use policy (rule '{rid}')."
    )


def _load_l35_gate(tenant_id: str, corvin_home: "Path | None"):
    """Return a mtime-cached EgressGate for *tenant_id*, or None.

    Mirrors ``_load_l34_guard`` — same mtime-key, same fail-open contract.
    Resolves the performance regression identified in the M1 code review
    (``load_egress_gate_for_tenant`` was called uncached on every spawn).
    """
    home = _resolve_corvin_home(corvin_home)
    cfg_path = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    try:
        mtime = cfg_path.stat().st_mtime if cfg_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    cache_key = f"{tenant_id}:{home}"
    with _l35_cache_lock:
        cached = _l35_cache.get(cache_key)
        if cached is not None and cached.get("mtime") == mtime:
            return cached.get("gate")

    gate = None
    if mtime > 0.0:
        try:
            from egress_gate import load_egress_gate_for_tenant  # type: ignore
            gate = load_egress_gate_for_tenant(tenant_id, corvin_home=home)
        except Exception as exc:  # noqa: BLE001
            _log.warning("spawn_gates: L35 gate load failed (%r) — fail-open", exc)
            gate = None

    # ADR-0167 — attach the ELR ratchet when this tenant has issued an egress
    # descriptor AND an instance-bound license is active. Strictly
    # fail-open-to-static: any miss (no license, no descriptor, import/parse
    # error) leaves the gate exactly as before — the static L35 policy. Only an
    # opted-in tenant (one that ran the issuer) gets ratchet enforcement, so a
    # tenant without a descriptor is byte-identical to pre-ADR-0167 behaviour.
    # NOTE (cache staleness, bounded): the gate (with its ratchet) is cached by
    # tenant.corvin.yaml mtime only. A license change that does NOT touch the YAML
    # (expiry, instance rebind, reload-from-disk) leaves the prior ratchet until
    # the YAML mtime changes or invalidate_cache() is called — callers that reload
    # the license should invalidate. Impact is bounded: the feature is
    # fail-open-to-static and the anchor is a stable per-instance value.
    if gate is not None and mtime > 0.0:
        try:
            import sys as _sys
            # operator/bridges/shared/ → parents[2] == operator/, then /license.
            # (parents[1] would be operator/bridges, which has no license/ — that
            # import-fail would silently cache a ratchet-less gate.)
            _lic_dir = str(Path(__file__).resolve().parents[2] / "license")
            if _lic_dir not in _sys.path:
                _sys.path.insert(0, _lic_dir)
            import yaml as _yaml  # type: ignore
            from elr_issuer import (  # type: ignore
                build_egress_registry_and_ratchet_for_tenant as _build_rr,
            )
            _raw = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            _rr = _build_rr(_raw if isinstance(_raw, dict) else None)
            if _rr is not None:
                _ratchet, _registry = _rr
                gate.ratchet = _ratchet
                gate.set_capability_registry(_registry)
                _log.info("spawn_gates: L35 ELR ratchet attached for tenant=%s", tenant_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug("spawn_gates: ELR ratchet attach skipped (%r) — static policy", exc)

    with _l35_cache_lock:
        _l35_cache[cache_key] = {"mtime": mtime, "gate": gate}
    return gate


def check_l35(
    engine_id: str,
    tenant_id: str,
    *,
    persona: "str | None" = None,
    channel: str = "",
    chat_key: str = "",
    corvin_home: "Path | None" = None,
) -> "str | None":
    """ADR-0043 / L35 pre-spawn network-egress gate.

    Returns ``None`` when the spawn is permitted (policy disabled, gate
    passes, or operational error — fail-open).  Returns a user-facing
    refusal string when the active egress policy explicitly denies.

    Uses ``_load_l35_gate`` (mtime-cached EgressGate) + ``gate.validate()``
    for the same host-resolution + audit-emit contract as
    ``egress_gate.check_engine_egress()``, but without loading the YAML
    on every call.
    """
    if not isinstance(engine_id, str) or not engine_id:
        _log.debug("spawn_gates.check_l35: engine_id missing — fail-open")
        return None

    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    gate = _load_l35_gate(tenant, corvin_home)
    if gate is None:
        return None

    try:
        from egress_gate import DEFAULT_ENGINE_HOSTS  # type: ignore
        host = DEFAULT_ENGINE_HOSTS.get(engine_id, "unknown")
        # ADR-0181 M3 — provider assignment redirects egress to the provider/proxy
        # host; validate that, not the engine default (else a cloud provider on a
        # localhost-default engine would bypass the deny policy).
        try:
            from engine_models import resolve_engine_egress_host  # type: ignore
            _phost = resolve_engine_egress_host(tenant, engine_id)
            if _phost:
                host = _phost
        except Exception:  # noqa: BLE001
            pass
        decision = gate.validate(
            host,
            engine_id=engine_id,
            persona=persona,
            channel=channel or None,
            chat_key=chat_key or None,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail CLOSED (ADR-0043): a broken egress gate must not wave a spawn
        # through unchecked. This matches egress_gate.check_engine_egress, which
        # also refuses on validate error — the two L35 checks now agree.
        _log.warning("spawn_gates.check_l35: validate failed (%r) — fail-closed", exc)
        return (
            f"[egress] Spawn rejected: engine '{engine_id}' egress could not be "
            f"validated (gate error). Fail-closed per tenant egress policy. "
            f"Operator: check tenant.corvin.yaml::spec.egress."
        )

    if decision.allowed:
        return None

    _log.info(
        "[spawn-gate/L35] denied engine=%s host=%s reason=%s channel=%s chat=%s",
        engine_id, host, decision.reason, channel, chat_key,
    )
    return (
        f"[egress] Spawn rejected: Engine '{engine_id}' is not allowed to reach "
        f"host '{host}' per tenant egress policy. "
        f"{decision.reason} "
        f"Operator policy: tenant.corvin.yaml::spec.egress."
    )


def invalidate_cache(tenant_id: str | None = None) -> None:
    """Invalidate the L34 + L35 + L44 caches for *tenant_id*, or all tenants if None.

    Useful in tests and after an explicit tenant.corvin.yaml write.
    """
    with _l34_cache_lock:
        if tenant_id is None:
            _l34_cache.clear()
        else:
            for k in [k for k in _l34_cache if k.startswith(f"{tenant_id}:")]:
                del _l34_cache[k]
    with _l35_cache_lock:
        if tenant_id is None:
            _l35_cache.clear()
        else:
            for k in [k for k in _l35_cache if k.startswith(f"{tenant_id}:")]:
                del _l35_cache[k]
    with _l44_overlay_cache_lock:
        if tenant_id is None:
            _l44_overlay_cache.clear()
        else:
            for k in [k for k in _l44_overlay_cache if k.startswith(f"{tenant_id}:")]:
                del _l44_overlay_cache[k]
