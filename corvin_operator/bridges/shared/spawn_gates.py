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
from dataclasses import dataclass
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


class _L34GuardLoadFailed:
    """Returned by ``_load_l34_guard`` when a tenant policy EXISTS but could not be loaded.

    Distinct from ``None`` (no policy, the operator's opt-in default, fail-open). A policy that
    is present but unreadable, or a ``data_classification`` module that cannot be imported,
    is a gate error: ``check_l34`` refuses the spawn (fail-closed, like its classify and
    validate handlers and like ``check_l44``). Until 2026-10-21 this case returned ``None``
    and every prompt — a secret included — reached the engine unchecked.
    """

    def __init__(self, error: str) -> None:
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"<L34 guard load failed: {self.error}>"


L34_GUARD_LOAD_FAILED = _L34GuardLoadFailed
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
    # CORVIN_HOME, else THIS checkout's repo-marker home. Resolved by the
    # sibling ``paths.py`` loaded by file path — never
    # ``corvin_operator.forge.forge.paths``, which the live venv's editable
    # ``.pth`` can resolve into a DIFFERENT checkout (whose home is that
    # checkout's live ``.corvin``; 2026-09-27 split-brain incident).
    return _sibling_paths().corvin_home()


_SIBLING_PATHS = None


def _sibling_paths():
    """``paths.py`` from this file's own directory, loaded by file path."""
    global _SIBLING_PATHS
    if _SIBLING_PATHS is None:
        import importlib.util as _ilu  # noqa: PLC0415

        spec = _ilu.spec_from_file_location(
            "_paths_spawn_gates", Path(__file__).resolve().parent / "paths.py",
        )
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _SIBLING_PATHS = mod
    return _SIBLING_PATHS


def _load_l34_guard(tenant_id: str, corvin_home: Path | None):
    """Return a mtime-cached DataFlowGuard for *tenant_id*, None, or a load-failure marker.

    Cache is invalidated when ``tenant.corvin.yaml`` mtime changes. ``None`` means the
    tenant has no policy file (no residency policy by design; ``check_l34`` still applies the
    SECRET floor, T-0254). A policy file that is present but
    cannot be loaded (missing module, unreadable yaml) yields an ``_L34GuardLoadFailed``
    marker — never cached — and ``check_l34`` refuses on it.
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
            _log.warning("spawn_gates: L34 guard load failed (%r) — fail-closed, re-evaluate next call", exc)
            guard = _L34GuardLoadFailed(type(exc).__name__)
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


def _provider_current_host(tenant: str, provider: str, corvin_home: "Path | None") -> "str | None":
    """The provider's CURRENT egress host (from its declaration and the live registry), or ``None``.

    ``None`` makes ``validate_provider`` deny (``endpoint_unknown``): an unknown host is never
    assumed to match.
    """
    try:
        import data_classification as _dc  # type: ignore  # noqa: PLC0415
        import sensitive_route as _sr  # type: ignore  # noqa: PLC0415
        from engine_models import load_providers  # type: ignore  # noqa: PLC0415
        declared, _rej = _dc.parse_provider_compliance(_tenant_yaml(tenant, corvin_home))
        decl, ps = declared.get(provider), load_providers().get(provider)
        if decl is None or ps is None:
            return None
        return _sr.current_provider_host(ps, decl) or None
    except Exception:  # noqa: BLE001
        return None


def detector_on(tenant_id: str, corvin_home: "Path | None" = None) -> bool:
    """Is the additive detector switched on for this tenant (ADR-2268)?

    True when the tenant's ``sensitive_route`` is enabled and well formed. Every caller that
    classifies text itself (the ADR-2102 history gates, the A2A worker) passes it as
    ``classify_task(..., detector=...)`` so the turn gate, the reroute and the history gate
    all see the same class. A tenant without a policy, or a guard that failed to load, is
    ``False``: those paths refuse or allow elsewhere.
    """
    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    try:
        guard = _load_l34_guard(tenant, corvin_home)
    except Exception:  # noqa: BLE001
        return False
    return bool(getattr(guard, "detector_on", False))


def _no_policy_secret_refusal(
    engine_id: str, tenant: str, *, classification: "str | None", prompt: "str | None",
    persona: "str | None", channel: str, chat_key: str, corvin_home: "Path | None",
) -> "str | None":
    """T-0254: a tenant with no ``tenant.corvin.yaml`` still refuses a SECRET prompt.

    ``None`` (nothing objects) for everything below SECRET and for any classifier error, so
    the zero-config default keeps working; a refusal string for a literal credential, with
    ``data_flow.blocked`` written by the default guard (see ``secret_floor_refusal``).
    """
    try:
        from data_classification import secret_floor_refusal  # type: ignore
    except Exception as exc:  # noqa: BLE001 — module unimportable: same fail-open as before
        _log.debug("spawn_gates.check_l34: secret floor unavailable (%r)", exc)
        return None
    decision = secret_floor_refusal(
        tenant, engine_id=engine_id, prompt=prompt, classification=classification,
        persona=persona, channel=channel or None, chat_key=chat_key or None,
        corvin_home=corvin_home)
    if decision is None:
        return None
    _log.info("[spawn-gate/L34] denied (no policy, SECRET floor) engine=%s reason=%s channel=%s chat=%s",
              engine_id, decision.reason, channel, chat_key)
    return (
        f"[data-flow] Spawn rejected: the prompt contains a credential (classification SECRET) "
        f"and engine '{engine_id}' is not local with zero network egress. {decision.reason} "
        "This holds even without a tenant.corvin.yaml. Remove the credential from the prompt."
    )


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
    provider: "str | None" = None,
) -> "str | None":
    """ADR-0042 / L34 pre-spawn data-classification gate.

    ``provider`` (ADR-2268, T-0219) names the provider of ONE rerouted turn. The pair
    (``engine_id``, ``provider``) is then judged by ``DataFlowGuard.validate_provider`` from the
    operator's ``provider_compliance`` and the provider's CURRENT egress host, never by the
    engine's own locality. The ADR-2102 history gates pass it too, so a rerouted turn's history
    is judged against the provider and not against ``claude_code``. ``None`` is today's gate.

    Returns ``None`` when the spawn is permitted (gate passes, or no tenant
    config — the opt-in default, fail-open — except for a SECRET prompt, which
    is refused even then, T-0254).  Returns a user-facing refusal
    string when the gate denies, AND when a present policy could not be loaded,
    classified or validated (gate error — fail-closed, like ``check_l44``).

    Two classification modes:

    * ``classification`` (str, e.g. ``"internal"``) — caller-declared
      level, used by acs_runtime / A2A spawn sites.
    * ``prompt`` + ``persona`` — heuristic classification via
      ``classify_task()``, used by the OS-turn adapter path.

    The engine is validated under the id it actually runs as. The former
    ``cc_local_mode`` remap (``claude_code`` → ``claude_code_local``, the
    ADR-0126 Ollama redirect) was removed with ADR-2091: a stale caller that
    still passes it gets a ``TypeError`` (refused by the caller's fail-closed
    wrapper), never a silently different locality.

    The ``DataFlowGuard.validate()`` call emits the ``data_flow.approved``
    or ``data_flow.blocked`` L16 audit event before this function returns.
    """
    if not isinstance(engine_id, str) or not engine_id:
        _log.debug("spawn_gates.check_l34: engine_id missing — fail-open")
        return None

    validate_id = engine_id

    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    guard = _load_l34_guard(tenant, corvin_home)
    if guard is None:
        return _no_policy_secret_refusal(
            engine_id, tenant, classification=classification, prompt=prompt, persona=persona,
            channel=channel, chat_key=chat_key, corvin_home=corvin_home)
    if isinstance(guard, _L34GuardLoadFailed):
        # Fail CLOSED: a tenant policy exists, so the operator asked for the gate, and
        # we cannot evaluate it. Waving the spawn through would send a possibly SECRET
        # prompt to an engine the policy may forbid.
        _log.warning("spawn_gates.check_l34: policy present but guard unavailable (%s) — fail-closed",
                     guard.error)
        return (
            "[data-flow] Spawn rejected: the tenant data-classification policy exists but "
            f"could not be loaded for engine '{engine_id}' (gate error: {guard.error}). "
            "Fail-closed per tenant data-classification policy. Operator: check the "
            "data_classification module and tenant.corvin.yaml::spec.data_classification."
        )

    if getattr(guard, "route_malformed", False) is True:
        # ADR-2268 R1a: a malformed sensitive_route block refuses EVERY turn of the tenant
        # until it is fixed (only a hand edit produces it; the PUT route validates).
        _log.warning("spawn_gates.check_l34: sensitive_route is malformed — refusing (fail-closed)")
        return (
            "[data-flow] Spawn rejected: the tenant's sensitive_route setting is malformed "
            f"(engine '{engine_id}'). Fail-closed per tenant data-classification policy. "
            "Operator: fix tenant.corvin.yaml::spec.data_classification.sensitive_route."
        )

    if classification is None:
        try:
            from data_classification import classify_task  # type: ignore
            cls_val = classify_task(prompt or "", persona=persona,
                                    detector=bool(getattr(guard, "detector_on", False)))
        except Exception as exc:  # noqa: BLE001
            # Fail CLOSED (round-2): a tenant policy IS present (guard is not
            # None) but the task could not be classified — we cannot prove it is
            # allowed against the residency matrix, so deny rather than wave it
            # through. Consistent with the validate handler below and check_l35.
            # (The legitimate no-policy opt-in case is the guard-is-None early
            # return above: no policy, so only the SECRET floor applies.)
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
        if provider:
            decision = guard.validate_provider(
                classification=cls_val,
                engine_id=validate_id,
                provider=provider,
                current_host=_provider_current_host(tenant, provider, corvin_home),
                persona=persona,
                channel=channel or None,
                chat_key=chat_key or None,
            )
        else:
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

    # ADR-0532 Phase 1 — os.flow_guard shadow comparison. Never changes the
    # return value below: this Skill accrues an audited trust record
    # (ADR-2092 G0) by watching the SAME decision the production gate just
    # made, it does not make one. A failure here must never affect the
    # spawn — shadow_compare() is fail-safe and swallows its own exceptions.
    try:
        from core.skills.os_skills.flow_guard_skill import shadow_compare as _fg_shadow

        _fg_shadow(
            prompt=prompt,
            engine_id=engine_id,
            tenant_id=tenant,
            production_allowed=decision.allowed,
        )
    except Exception as exc:  # noqa: BLE001 — shadow path must never affect L34
        _log.debug("spawn_gates.check_l34: os.flow_guard shadow compare failed (%r)", exc)

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
    ``egress_gate`` module or a tampered/unparseable policy REFUSES the turn —
    an acceptable-use guarantee must never fail-open. (The ADR-0141 Tier-3
    capability gate asserts house-rules presence independently.) A classifier
    BACKEND failure (cloud classifier unreachable) is the one deliberate
    exception (commit 254a5a6, maintainer decision): it degrades to the
    deterministic Tier-0 regex floor — prohibited-class patterns still block,
    benign requests still work — so a fresh zero-config install without a
    reachable classifier is not dead on arrival.

    Audit-first: ``gate.classify`` emits exactly one ``house_rules.{allowed,
    warned,escalated,denied}`` L16 event synchronously (via the injected
    per-tenant forge writer) BEFORE this returns, so the deny/escalate event
    lands on the chain before the refusal string.

    A ``floor_only`` tenant (ADR-2091 — egress denies the cloud classifier
    host) spawns no classifier at all: a deny-pattern match denies, every
    other task escalates (operator-approval wording), audited
    ``house_rules.floor_only`` + ``house_rules.{denied,escalated}``.

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


def check_l44_floor(
    prompt: "str | None",
    tenant_id: str,
    *,
    persona: str = "assistant",
    channel: str = "",
    chat_key: str = "",
    engine_id: str = "",
    corvin_home: "Path | None" = None,
) -> "str | None":
    """L44 evaluated on the DETERMINISTIC Tier-0 floor ONLY — no classifier spawn.

    Same policy, rules and audit machinery as :func:`check_l44`, but the gate is
    built with ``classifier=None``, so ``HouseRulesGate.classify`` returns the
    Tier-0 verdict INSTANTLY (regex over the prohibited-class patterns; military
    / offensive-cyber / disinformation still MATCH and BLOCK, a task matching no
    rule reaches the policy default). No classifier spawn, so it can never
    hang. (This is the classifier-TIMEOUT fallback; it is not the ADR-2091
    ``floor_only`` order, whose unmatched tasks escalate — see check_l44.)

    This is the exact degradation ``check_l44`` already documents for a
    classifier BACKEND failure, exposed as a callable so a caller that puts its
    OWN wall-clock bound around ``check_l44`` (the imagegen MCP server) can, on
    timeout, fall back to the floor instead of hard-refusing. Before this, a slow
    or unreachable cloud classifier made the imagegen's 100s wrapper fire first
    and refuse EVERY image — even a benign "queen bee" — with "couldn't be
    safety-checked in time", so image generation was dead on any box without a
    fast classifier. Fail-TO-FLOOR, never fail-open: a prohibited prompt is still
    blocked here.
    """
    task = prompt or ""
    if not task.strip():
        return None

    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    try:
        import house_rules as _hr  # type: ignore
        from egress_gate import make_forge_audit_writer as _mk_writer  # type: ignore
    except Exception as _imp_exc:  # noqa: BLE001 — mandatory layer absent → fail closed
        _log.error("[house-rules] floor: module import failed (%s) — fail-closed deny",
                   type(_imp_exc).__name__)
        return ("[house-rules] Acceptable-use gate unavailable — request blocked "
                "(fail-closed). Contact the operator.")

    try:
        try:
            overlay = _load_l44_overlay(tenant, corvin_home)
        except Exception:  # noqa: BLE001
            overlay = None
        try:
            _audit_write = _mk_writer(_l44_audit_path(tenant, corvin_home))
        except Exception:  # noqa: BLE001
            def _audit_write(event_type: str, severity: str, details: dict) -> None:
                pass

        gate = _hr.HouseRulesGate.from_repo(
            audit_writer=_audit_write,
            classifier=None,   # ← the whole point: instant deterministic floor
            tenant_overlay=overlay,
        )
        decision = gate.classify(
            task, persona=persona or "", channel=channel, chat_key=chat_key,
            engine_id=engine_id,
        )
    except Exception as _exc:  # noqa: BLE001 — even the floor failed to build → fail closed
        _log.error("[house-rules] floor: gate/classify failed (%s) — fail-closed deny",
                   type(_exc).__name__)
        return ("[house-rules] Acceptable-use gate unavailable — request blocked "
                "(fail-closed). Contact the operator.")

    if decision.action in ("allow", "warn"):
        return None
    rid = getattr(decision, "rule_id", "") or ""
    if decision.action == "escalate":
        return (
            f"[house-rules] This request needs operator approval before it can run "
            f"(rule '{rid}'). It touches a restricted or uncertain area."
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


def _l35_provider_unresolved(engine_id: str, provider: str) -> str:
    """Refusal for a rerouted turn whose provider host cannot be determined (fail-closed)."""
    return (
        f"[egress] Spawn rejected: provider '{provider}' for engine '{engine_id}' has no "
        f"resolvable egress host. A rerouted turn is never sent to the engine's default host. "
        f"Operator: check the provider on the Engines page."
    )


def check_l35(
    engine_id: str,
    tenant_id: str,
    *,
    persona: "str | None" = None,
    channel: str = "",
    chat_key: str = "",
    corvin_home: "Path | None" = None,
    provider: "str | None" = None,
) -> "str | None":
    """ADR-0043 / L35 pre-spawn network-egress gate.

    ``provider`` (ADR-2268, T-0228) names the provider of ONE rerouted turn: the gate then
    judges THAT provider's host, and a provider whose host cannot be resolved is refused,
    never replaced by the engine's default host.

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
        if provider and not _provider_current_host(tenant, provider, corvin_home):
            # A rerouted turn must reach a KNOWN host even when the tenant has no egress
            # policy (ADR-2268): with nothing to compare, an unresolvable host is a refusal.
            return _l35_provider_unresolved(engine_id, provider)
        return None

    try:
        from egress_gate import DEFAULT_ENGINE_HOSTS  # type: ignore
        host = DEFAULT_ENGINE_HOSTS.get(engine_id, "unknown")
        # ADR-0181 M3 — provider assignment redirects egress to the provider/proxy
        # host; validate that, not the engine default (else a cloud provider on a
        # localhost-default engine would bypass the deny policy).
        try:
            from engine_models import resolve_engine_egress_host  # type: ignore
            _phost = resolve_engine_egress_host(tenant, engine_id, provider)
            if _phost:
                host = _phost
            elif provider:
                return _l35_provider_unresolved(engine_id, provider)
        except Exception:  # noqa: BLE001
            if provider:
                return _l35_provider_unresolved(engine_id, provider)
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


# ── T-0219 / ADR-2268 — the reroute decision ─────────────────────────────────


@dataclass(frozen=True)
class RouteOutcome:
    """The pure decision for a spawn: run as chosen, run on the sensitive provider, or refuse.

    ``kind`` is ``"native"`` (today's behaviour), ``"reroute"`` (run the engine on ``provider``
    with ``model_id``) or ``"refuse"`` (``reason`` is a stable code, never the prompt text).
    """
    kind: str
    engine: str = ""
    classification: str = ""
    provider: str = ""
    model_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class L34Route:
    """What a spawn site must do (ADR-2268 Decision 3).

    ``refusal`` is a user-facing string and the site must NOT spawn. Otherwise the site spawns
    ``engine_id``; when ``rerouted`` it spawns it on ``provider`` with ``model_id`` and the strict
    provider environment, and must judge the turn's history against the provider as well.
    """
    refusal: "str | None"
    engine_id: str
    provider: "str | None" = None
    model_id: "str | None" = None
    rerouted: bool = False


def _tenant_yaml(tenant: str, home: "Path | None") -> "dict | None":
    """The parsed tenant mapping, or ``None`` when there is no file (the opt-in default).
    An unreadable file raises; the caller turns that into a gate-error refusal."""
    import yaml as _yaml  # type: ignore  # noqa: PLC0415
    import data_classification as _dc  # type: ignore  # noqa: PLC0415
    resolved = _dc._tenant_config_file(tenant, _resolve_corvin_home(home))
    if resolved is None:
        return None
    _tid, _home, cfg_path = resolved
    return _yaml.safe_load(cfg_path.read_text("utf-8")) or {}


def decide_l34_route(
    engine_id: str,
    tenant_id: str,
    *,
    classification: "str | None" = None,
    prompt: "str | None" = None,
    persona: "str | None" = None,
    corvin_home: "Path | None" = None,
) -> RouteOutcome:
    """Decide native / reroute / refuse for one spawn, WITHOUT auditing (ADR-2268, T-0219).

    Native exactly where ``check_l34`` allows today: no policy, a PUBLIC turn, or a turn the
    matrix admits. A refusal is returned where ``check_l34`` refuses today and no route is
    configured, or the route cannot take the turn. ``SECRET`` is never rerouted. The caller
    (:func:`resolve_l34_route`) audits and runs the second pass; this function only decides,
    on a guard copy with no audit writer, so a reroute does not leave a misleading
    ``data_flow.blocked`` record behind.
    """
    import dataclasses as _dcl  # noqa: PLC0415
    import data_classification as _dc  # type: ignore  # noqa: PLC0415
    import sensitive_route as _sr  # type: ignore  # noqa: PLC0415
    from engine_models import load_providers  # type: ignore  # noqa: PLC0415

    if not isinstance(engine_id, str) or not engine_id:
        return RouteOutcome(kind="native")
    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"

    loaded = _load_l34_guard(tenant, corvin_home)
    if loaded is None:
        return RouteOutcome(kind="native", engine=engine_id)
    if isinstance(loaded, _L34GuardLoadFailed):
        return RouteOutcome(kind="refuse", engine=engine_id, reason="gate_error")
    if not _dcl.is_dataclass(loaded):       # a guard double: no route to decide, today's verdict stands
        return RouteOutcome(kind="native", engine=engine_id)
    guard = _dcl.replace(loaded, audit_writer=None)     # silent: audit happens in resolve_l34_route

    if guard.route_malformed is True:
        return RouteOutcome(kind="refuse", engine=engine_id, reason="route_malformed")
    try:
        raw = _tenant_yaml(tenant, corvin_home)
    except Exception:  # noqa: BLE001
        return RouteOutcome(kind="refuse", engine=engine_id, reason="gate_error")
    route = _sr.parse_sensitive_route(raw)

    if classification is None:
        try:
            cls = _dc.classify_task(prompt or "", persona=persona, detector=guard.detector_on)
        except Exception:  # noqa: BLE001 — a policy exists and the turn cannot be judged
            return RouteOutcome(kind="refuse", engine=engine_id, reason="classifier_error")
    else:
        cls = _dc.DataClassification.parse(classification)
        if cls is None:
            return RouteOutcome(kind="refuse", engine=engine_id, reason="unknown_classification")
    cls_name = cls.name

    try:
        decision = guard.validate(classification=cls, engine_id=engine_id)
    except Exception:  # noqa: BLE001
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name, reason="gate_error")
    if decision.allowed:
        return RouteOutcome(kind="native", engine=engine_id, classification=cls_name)

    # The chosen engine is denied for this class. Only an enabled, well-formed route can help, and
    # only for the classes the ADR allows (never SECRET, never PUBLIC).
    if not route.on:
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name, reason="matrix")
    if cls not in (_dc.DataClassification.INTERNAL, _dc.DataClassification.CONFIDENTIAL):
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name, reason="matrix")
    if engine_id != "claude_code":
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name,
                            reason="engine_not_reroutable")

    target = route.route
    providers = load_providers()
    declared, _rejected = _dc.parse_provider_compliance(raw)
    ok, why = _sr.provider_eligibility(target.provider, declared=declared, providers=providers)
    if not ok:
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name,
                            provider=target.provider, reason=why)
    if not _sr.model_allowed(target.provider, target.model_id, declared=declared, providers=providers):
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name,
                            provider=target.provider, reason="target_not_eligible")
    host = _sr.current_provider_host(providers[target.provider], declared[target.provider])
    verdict = guard.validate_provider(classification=cls, engine_id=engine_id, provider=target.provider,
                                      current_host=host)
    if not verdict.allowed:
        return RouteOutcome(kind="refuse", engine=engine_id, classification=cls_name,
                            provider=target.provider, reason=verdict.matched_rule or "matrix")
    return RouteOutcome(kind="reroute", engine=engine_id, classification=cls_name,
                        provider=target.provider, model_id=target.model_id)


def _route_audit(tenant: str, home: "Path | None", event: str, out: RouteOutcome, *, reason: str,
                 persona: "str | None", channel: str, chat_key: str) -> None:
    """Best-effort ``data_flow.rerouted`` / ``data_flow.reroute_refused`` through the tenant's guard."""
    try:
        guard = _load_l34_guard(tenant, home)
        if guard is None or isinstance(guard, _L34GuardLoadFailed):
            return
        guard.emit_route_event(
            event, engine_id=out.engine, classification=out.classification or "UNKNOWN", reason=reason,
            provider=out.provider, model_id=out.model_id, persona=persona,
            channel=channel or None, chat_key=chat_key or None)
    except Exception:  # noqa: BLE001 — an audit hiccup never changes the decision
        pass


def resolve_l34_route(
    engine_id: str,
    tenant_id: str,
    *,
    prompt: "str | None" = None,
    persona: "str | None" = None,
    channel: str = "",
    chat_key: str = "",
    classification: "str | None" = None,
    switchable: bool = False,
    corvin_home: "Path | None" = None,
) -> L34Route:
    """The L34 + L35 verdict for one spawn, with the reroute of ADR-2268 (T-0219).

    Every behaviour that exists today is preserved: where nothing is rerouted this calls the
    unchanged :func:`check_l34` (same refusal text, same ``data_flow.approved`` / ``blocked``
    audit, same flow-guard shadow compare) and returns its answer. A turn is rerouted only when
    ``switchable`` (the site can start the engine on another endpoint: the bridge OS turn and the
    non-delegated console OS turn), an enabled route exists and :func:`decide_l34_route` says so.
    After a reroute the second pass judges the PAIR (L34 ``validate_provider`` on the provider's
    current host, L35 on that host); any refusal there is final: there is never a second reroute.
    """
    tenant = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
    common = dict(prompt=prompt, persona=persona, channel=channel, chat_key=chat_key,
                  classification=classification, corvin_home=corvin_home)
    out = decide_l34_route(engine_id, tenant, classification=classification, prompt=prompt,
                           persona=persona, corvin_home=corvin_home)
    audit_kw = dict(persona=persona, channel=channel, chat_key=chat_key)
    route_involved = out.reason in _ROUTE_REFUSAL_REASONS or out.kind == "reroute"

    if out.kind != "reroute":
        refusal = check_l34(engine_id, tenant, **common)
        if refusal is None and out.kind == "refuse":
            # check_l34 would let this through but the route logic refuses (never observed for
            # a non-route tenant): the refusal is the route's, in plain words, no prompt text.
            refusal = _route_refusal_text(out)
        if refusal is not None and route_involved:
            _route_audit(tenant, corvin_home, "data_flow.reroute_refused", out, reason=out.reason, **audit_kw)
        if refusal is None:
            refusal = check_l35(engine_id, tenant, persona=persona, channel=channel, chat_key=chat_key,
                                corvin_home=corvin_home)
        return L34Route(refusal=refusal, engine_id=engine_id)

    # decision == reroute
    if not switchable:
        # Row R7: the site cannot start the engine elsewhere. Refuse as today, record why.
        _route_audit(tenant, corvin_home, "data_flow.reroute_refused", out,
                     reason="site_not_switchable", **audit_kw)
        refusal = check_l34(engine_id, tenant, **common) or _route_refusal_text(out)
        return L34Route(refusal=refusal, engine_id=engine_id)

    second = check_l34(engine_id, tenant, provider=out.provider, **common)
    if second is None:
        second = check_l35(engine_id, tenant, persona=persona, channel=channel, chat_key=chat_key,
                           corvin_home=corvin_home, provider=out.provider)
    if second is not None:
        _route_audit(tenant, corvin_home, "data_flow.reroute_refused", out,
                     reason="second_pass_refused", **audit_kw)
        return L34Route(refusal=second, engine_id=engine_id)
    _route_audit(tenant, corvin_home, "data_flow.rerouted", out, reason="route_applied", **audit_kw)
    return L34Route(refusal=None, engine_id=engine_id, provider=out.provider,
                    model_id=out.model_id, rerouted=True)


#: Refusal reasons that come from the route (so a ``reroute_refused`` record is meaningful).
_ROUTE_REFUSAL_REASONS = frozenset({
    "route_malformed", "target_not_eligible", "not_declared", "unknown_provider", "native_provider",
    "aggregator", "proxy_provider", "locality_not_admissible", "platform_incomplete",
    "endpoint_changed", "provider_not_declared", "endpoint_unknown", "provider_engine",
    "secret_provider", "engine_not_reroutable", "classifier_error", "gate_error",
})


def _route_refusal_text(out: RouteOutcome) -> str:
    return (
        "[data-flow] Spawn rejected: the sensitive-data route cannot take this turn "
        f"({out.reason or 'refused'}) for engine '{out.engine}'. Fail-closed per tenant "
        "data-classification policy. Operator: check "
        "tenant.corvin.yaml::spec.data_classification.sensitive_route."
    )
