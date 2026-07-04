"""egress_gate.py — Layer 35: Network Egress Lockdown + ADR-0167 Ratchet.

ADR-0043 (companion to ADR-0041 / ADR-0042). Network-level analogue to
L10 path-gate: a declarative allow/forbid list of outbound hosts, checked
at every engine-spawn callsite. Pairs with Layer 34's
:class:`~data_classification.DataFlowGuard` — L34 reasons about the
classification × engine grade match, L35 reasons about which hosts a
given engine is allowed to talk to under the active tenant preset.

ADR-0167 M1: Entangled License Ratchet (ELR) integration. On paid licenses,
the egress-allowlist signature is derived from the offline ratchet (seeded
by license token, advanced by audit-chain head). Decryption with the wrong
tile key yields garbage, not an error — fail-closed by design. Fallback:
when ratchet derivation fails, the plain policy check applies (for local
development, license-free tier).

Honest scope (ADR-0041 § "Out of claude scope"):

  * **L35 ships the *policy* and the *audit*** — every refused host
    emits ``egress.blocked`` into the L16 hash chain.
  * **Real network isolation** belongs to the perimeter — operator
    iptables / docker network / cloud security-group rules. L35 is
    not a Python-monkeypatch firewall; it would not survive a
    determined `subprocess` evading the check.
  * What we *do* enforce in-process: pre-spawn host validation, which
    is sufficient to stop an engine from being spawned with an
    impossible target (e.g. ``opencode --provider anthropic`` under
    an EU_PRODUCTION preset).

Policy precedence (when ratchet succeeds):

  1. Paid-tier, ratchet derivation succeeds → unwrap capability → use derived policy.
  2. Fallback (ratchet unavailable or fails) → static policy, as before.

Tenant configuration::

    spec:
      egress:
        enabled: true               # opt-in, default false
        default_action: deny        # allow | deny
        allowed_hosts:
          - localhost
          - 127.0.0.1
          - ollama.lan
        forbidden_hosts:
          - api.anthropic.com
          - api.openai.com

Audit contract (L16 hash chain):

  * ``egress.approved`` (severity INFO) — every passing validation
  * ``egress.blocked``  (severity CRITICAL) — every refused validation
  * ``egress.preset_loaded`` (severity INFO) — emitted once by the
    boot self-test when a tenant preset is validated.
  * ``egress.ratchet_committed`` (severity INFO) — committed tile_k hash when ELR active.

Audit details allow-list: ``host``, ``engine_id``, ``persona``,
``channel``, ``chat_key``, ``reason``, ``matched_rule``. Never the URL
path, never the request body, never the response. Never tile keys, roots,
or plaintext capability material.

CI lint: module MUST NOT ``import anthropic``. Audit ``details`` keys
constrained at emission time.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("egress_gate", version="1.2", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass

# ----- types ---------------------------------------------------------

DefaultAction = Literal["allow", "deny"]
MatchedRule = Literal[
    "forbidden_explicit",
    "allowed_explicit",
    "default_allow",
    "default_deny",
    "egress_disabled",
]


@dataclass(frozen=True)
class EgressPolicy:
    """Parsed and validated tenant egress policy."""
    enabled: bool = False
    default_action: DefaultAction = "allow"
    allowed_hosts: tuple[str, ...] = ()
    forbidden_hosts: tuple[str, ...] = ()


# Canonical outbound host per engine_id.  Used by the adapter's
# _check_egress_or_fail() to look up which host to validate against the
# active egress policy.  "unknown" is a deliberate sentinel: policies
# with ``default_action=deny`` will refuse it; ``default_action=allow``
# will pass it through — so the sentinel never silently grants access.
DEFAULT_ENGINE_HOSTS: dict[str, str] = {
    "claude_code":       "api.anthropic.com",
    "codex_cli":         "api.openai.com",
    "opencode":          "unknown",          # provider not pinned at config time
    "opencode_ollama":   "localhost",        # local Ollama socket
    "opencode_http":     "localhost",        # self-hosted OpenCode HTTP on LAN
    "hermes":            "localhost",        # Ollama HTTP loopback — zero egress
    "claude_code_local": "localhost",        # Local ClaudeCode variant (L34: locality=local, egress=none)
    "copilot":           "github.com",       # GitHub Copilot CLI (not wired in adapter, defensive)
    "acs_worker":        "api.anthropic.com",  # ACS background worker — mirrors claude_code egress
    "anthropic_batch":   "api.anthropic.com",  # Anthropic batch API — named host for audit trail
}


@dataclass(frozen=True)
class EgressDecision:
    """Result of :meth:`EgressGate.validate`."""
    allowed: bool
    host: str
    reason: str
    matched_rule: MatchedRule


class EgressDenied(Exception):
    """Raised by :meth:`EgressGate.validate_or_raise` on deny."""
    def __init__(self, decision: EgressDecision):
        self.decision = decision
        super().__init__(
            f"egress denied: host={decision.host} reason={decision.reason}"
        )


# ----- audit allow-list ----------------------------------------------

_AUDIT_ALLOWED: frozenset[str] = frozenset({
    "host",
    "engine_id",
    "persona",
    "channel",
    "chat_key",
    "reason",
    "matched_rule",
})


def _validate_audit_details(details: dict[str, Any]) -> None:
    for k in details:
        if k not in _AUDIT_ALLOWED:
            raise ValueError(
                f"egress audit detail '{k}' not in allow-list "
                f"{sorted(_AUDIT_ALLOWED)}"
            )


AuditWriter = Callable[[str, str, dict[str, Any]], None]
# Signature: (event_type, severity, details) -> None


# ----- host canonicalisation -----------------------------------------

# Liberal hostname regex: alphanum + dot + dash. Includes IPv4
# (digit-only labels). Reject anything else — egress policy entries
# are operator-curated so a strict format is fine.
_HOST_RE = re.compile(r"^(\[[\da-fA-F:]+\]|[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,253}))$")


def canonicalise_host(host: str) -> str:
    """Lowercase + strip the host. Operators sometimes copy hostnames
    with trailing spaces or capital letters; this is a sieve for both.

    Raises ``ValueError`` on shape failure — the policy loader uses
    that to surface configuration errors loudly.
    """
    if not isinstance(host, str):
        raise ValueError(f"host must be a string, got {type(host).__name__}")
    h = host.strip().lower()
    if not h:
        raise ValueError("host must be non-empty")
    if not _HOST_RE.match(h):
        raise ValueError(f"host {h!r} fails shape check {_HOST_RE.pattern}")
    return h


# ----- gate ----------------------------------------------------------

@dataclass
class EgressGate:
    """Declarative egress policy enforcer, with ADR-0167 ratchet integration.

    Construct one per tenant. Call :meth:`validate` before any
    engine-spawn that talks to a remote host; call
    :meth:`validate_or_raise` for fail-closed semantics.

    When the policy is disabled (``enabled=False``), :meth:`validate`
    always returns allow with ``matched_rule="egress_disabled"`` — no
    audit emission, no overhead. This is the back-compat default for
    pre-L35 tenants.

    For paid licenses with ELR enabled, the ratchet attempts to derive
    a tile key and unwrap a capability descriptor. If successful, the
    descriptor's policy (allowed/forbidden hosts) is used. If the ratchet
    is unavailable or fails, fallback to the static policy. This is
    fail-closed: a decryption failure yields None, which triggers the
    fallback (never silently allows).
    """
    policy: EgressPolicy = field(default_factory=EgressPolicy)
    audit_writer: AuditWriter | None = None
    ratchet: Any = None  # Optional[EntangledRatchet], to avoid circular import
    capability_label: str = "egress-paid-preset"  # capability label for tile derivation

    # ----- factories -------------------------------------------------

    @classmethod
    def from_tenant_config(
        cls,
        tenant_config: dict[str, Any] | None,
        *,
        audit_writer: AuditWriter | None = None,
    ) -> "EgressGate":
        """Parse + validate ``spec.egress`` from a tenant.corvin.yaml
        dict. Missing fields yield the disabled-default policy.

        Raises ``ValueError`` on malformed entries — operator should
        see configuration errors loudly.
        """
        if not tenant_config or not isinstance(tenant_config, dict):
            return cls(audit_writer=audit_writer)

        spec = tenant_config.get("spec")
        if not isinstance(spec, dict):
            return cls(audit_writer=audit_writer)

        raw = spec.get("egress")
        if not isinstance(raw, dict):
            return cls(audit_writer=audit_writer)

        enabled = bool(raw.get("enabled", False))
        default_action = raw.get("default_action", "allow")
        if default_action not in ("allow", "deny"):
            raise ValueError(
                f"egress.default_action must be 'allow' or 'deny', got {default_action!r}"
            )

        allowed_raw = raw.get("allowed_hosts") or []
        if not isinstance(allowed_raw, list):
            raise ValueError("egress.allowed_hosts must be a list")
        forbidden_raw = raw.get("forbidden_hosts") or []
        if not isinstance(forbidden_raw, list):
            raise ValueError("egress.forbidden_hosts must be a list")

        try:
            allowed = tuple(canonicalise_host(h) for h in allowed_raw)
            forbidden = tuple(canonicalise_host(h) for h in forbidden_raw)
        except ValueError as e:
            raise ValueError(f"egress host parse: {e}") from e

        # Symmetric-difference sanity check — operator that lists a
        # host in BOTH lists is almost certainly confused; the forbid
        # wins anyway, but surface the inconsistency.
        overlap = set(allowed) & set(forbidden)
        if overlap:
            raise ValueError(
                f"egress: host(s) in both allowed_hosts and "
                f"forbidden_hosts: {sorted(overlap)}"
            )

        policy = EgressPolicy(
            enabled=enabled,
            default_action=default_action,
            allowed_hosts=allowed,
            forbidden_hosts=forbidden,
        )
        return cls(policy=policy, audit_writer=audit_writer)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        audit_writer: AuditWriter | None = None,
    ) -> "EgressGate | None":
        """Load + validate a standalone ``egress_policy.json`` file.

        Returns ``None`` when the file is missing (legacy / pre-L35
        deployments). Raises ``ValueError`` on malformed JSON or
        invalid schema — the adapter should treat that as a
        configuration error and surface it loudly.
        """
        p = Path(path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"egress_policy at {p}: {e}") from e
        # Allow either {enabled, …} flat shape OR {spec: {egress: {…}}} —
        # operators sometimes copy the tenant.yaml block as-is.
        if isinstance(data, dict) and isinstance(data.get("spec"), dict):
            return cls.from_tenant_config(data, audit_writer=audit_writer)
        wrapped = {"spec": {"egress": data}} if isinstance(data, dict) else None
        return cls.from_tenant_config(wrapped, audit_writer=audit_writer)

    # ----- core API --------------------------------------------------

    def validate(
        self,
        host: str,
        *,
        engine_id: str | None = None,
        persona: str | None = None,
        channel: str | None = None,
        chat_key: str | None = None,
    ) -> EgressDecision:
        """Return an :class:`EgressDecision`. Emits one audit event
        when policy is enabled, none when disabled.

        Disabled policy (the legacy / opt-in default) returns allow
        with ``matched_rule="egress_disabled"`` — semantically *not*
        an approval, just a pass-through. No audit emission.

        With ADR-0167 ratchet enabled: attempts to unwrap a capability
        descriptor from the ratchet. If successful, uses the derived policy.
        If the ratchet is unavailable or fails, falls back to the static
        policy (fail-closed: decryption failure returns None).
        """
        try:
            h = canonicalise_host(host)
        except ValueError as e:
            # Malformed host string treated as a deny (fail closed).
            decision = EgressDecision(
                allowed=False,
                host=str(host)[:128],
                reason=f"malformed host: {e}",
                matched_rule="default_deny",
            )
            self._emit("egress.blocked", "CRITICAL", decision,
                       engine_id=engine_id, persona=persona,
                       channel=channel, chat_key=chat_key)
            return decision

        if not self.policy.enabled:
            decision = EgressDecision(
                allowed=True,
                host=h,
                reason="egress policy disabled for tenant",
                matched_rule="egress_disabled",
            )
            # G-013 (ADR-0073): emit WARNING so unrestricted egress is visible in audit trail.
            # Policy disabled ≠ safe — operator must have explicitly opted out.
            self._emit("egress.policy_disabled", "WARNING", decision,
                       engine_id=engine_id, persona=persona,
                       channel=channel, chat_key=chat_key)
            return decision

        # Explicit forbid ALWAYS wins — even over a ratchet-derived allow.
        # forbidden_hosts is the operator's hard deny; a capability descriptor
        # (or its default_action=allow) must never override it (security review
        # 2026-06-27). This is evaluated BEFORE the ratchet so an issuer-signed
        # or misconfigured descriptor cannot re-permit a statically-forbidden
        # host (the documented "forbidden_hosts always wins" invariant).
        if h in self.policy.forbidden_hosts:
            decision = EgressDecision(
                allowed=False,
                host=h,
                reason="host on forbidden_hosts list",
                matched_rule="forbidden_explicit",
            )
            self._emit("egress.blocked", "CRITICAL", decision,
                       engine_id=engine_id, persona=persona,
                       channel=channel, chat_key=chat_key)
            return decision

        # ADR-0167 M1: attempt ratchet-derived policy check (forbidden already
        # rejected above, so the ratchet can only allow/deny non-forbidden hosts).
        ratchet_decision = self._try_ratchet_policy_check(h)
        if ratchet_decision is not None:
            self._emit("egress.ratchet_decision", "INFO", ratchet_decision,
                       engine_id=engine_id, persona=persona,
                       channel=channel, chat_key=chat_key)
            return ratchet_decision

        # Fallback to static policy (ratchet unavailable or failed).
        # Explicit allow.
        if h in self.policy.allowed_hosts:
            decision = EgressDecision(
                allowed=True,
                host=h,
                reason="host on allowed_hosts list",
                matched_rule="allowed_explicit",
            )
            self._emit("egress.approved", "INFO", decision,
                       engine_id=engine_id, persona=persona,
                       channel=channel, chat_key=chat_key)
            return decision

        # 3. Default action.
        if self.policy.default_action == "allow":
            decision = EgressDecision(
                allowed=True,
                host=h,
                reason="default_action=allow (unmatched host)",
                matched_rule="default_allow",
            )
            self._emit("egress.approved", "INFO", decision,
                       engine_id=engine_id, persona=persona,
                       channel=channel, chat_key=chat_key)
            return decision

        decision = EgressDecision(
            allowed=False,
            host=h,
            reason="default_action=deny (unmatched host)",
            matched_rule="default_deny",
        )
        self._emit("egress.blocked", "CRITICAL", decision,
                   engine_id=engine_id, persona=persona,
                   channel=channel, chat_key=chat_key)
        return decision

    def validate_or_raise(self, host: str, **kwargs: Any) -> EgressDecision:
        """Strict variant: raise :class:`EgressDenied` on deny."""
        decision = self.validate(host, **kwargs)
        if not decision.allowed:
            raise EgressDenied(decision)
        return decision

    # ----- preset validation (called by boot self-test) -------------

    def validate_preset_consistency(
        self,
        *,
        expected_engines: list[str] | None = None,
        engine_compliance: dict[str, Any] | None = None,
    ) -> list[str]:
        """Return human-readable consistency warnings.

        Useful checks an operator wants flagged at boot:

          * ``forbidden_hosts`` listed but ``enabled=False`` → policy
            won't actually block anything.
          * ``default_action="deny"`` with empty ``allowed_hosts`` →
            policy denies everything, including ``localhost``.
          * Optional cross-check: an engine in ``expected_engines``
            (e.g. the EU_PRODUCTION preset's allowed_engines) has
            network_egress == "external" — the egress gate cannot
            enforce that the engine actually stays on-host.

        Returns ``[]`` when everything checks out.
        """
        warnings: list[str] = []

        if self.policy.forbidden_hosts and not self.policy.enabled:
            warnings.append(
                "egress: forbidden_hosts listed but policy is disabled; "
                "no host will actually be blocked"
            )

        if self.policy.enabled and \
                self.policy.default_action == "deny" and \
                not self.policy.allowed_hosts:
            warnings.append(
                "egress: default_action=deny + empty allowed_hosts denies "
                "all hosts including localhost — likely a misconfiguration"
            )

        if expected_engines and engine_compliance:
            for eid in expected_engines:
                compl = engine_compliance.get(eid)
                if compl is None:
                    warnings.append(
                        f"egress: expected engine {eid!r} missing from "
                        f"compliance registry"
                    )
                    continue
                # Duck-typed access (avoid importing
                # data_classification just for the dataclass).
                egress_kind = getattr(compl, "network_egress", None)
                if egress_kind == "external":
                    warnings.append(
                        f"egress: engine {eid!r} has network_egress=external; "
                        f"L35 can refuse the spawn but cannot enforce "
                        f"runtime confinement — operator must add perimeter "
                        f"firewall rules"
                    )

        return warnings

    # ----- ADR-0167 ratchet integration (M2) ---------------------------

    def _try_ratchet_policy_check(self, host: str) -> EgressDecision | None:
        """Attempt to derive an egress policy from the ratchet (M2).

        Returns an EgressDecision if the ratchet successfully unwraps a
        capability descriptor, None if the ratchet is unavailable or
        decryption fails (triggering fallback to static policy).

        Decryption failure yields None, not an error — fail-closed by design.
        """
        if self.ratchet is None:
            return None

        try:
            # NOTE: `operator` is the Python stdlib module, NOT a package for the
            # repo's operator/ dir — `from operator.license.elr import …` ALWAYS
            # raised ModuleNotFoundError, so the ratchet could never load (dead
            # code; security review 2026-06-27). Resolve elr the way the rest of
            # the bridge does: operator/license on sys.path, then `import elr`.
            import sys as _sys
            from pathlib import Path as _Path
            _lic_dir = _Path(__file__).resolve().parents[2] / "license"
            if str(_lic_dir) not in _sys.path:
                _sys.path.insert(0, str(_lic_dir))
            from elr import CapabilityEnvelope, CapabilityRegistry  # noqa: F401
            from elr_capabilities_m2 import (
                EgressPaidPresetCapability,
                create_capability_from_dict,
            )
        except (ImportError, ModuleNotFoundError):
            return None

        try:
            # Derive the tile key for this capability
            tile_k = self.ratchet.derive_tile(self.capability_label)

            # Load the wrapped descriptor (M2: from tenant config via CapabilityRegistry)
            # For now, assume registry is set via set_capability_registry()
            if not hasattr(self, "_capability_registry"):
                return None

            wrapped = self._capability_registry.get_descriptor(self.capability_label)
            if wrapped is None:
                return None

            # Unwrap: if decryption fails, unwrap returns None (fail-closed)
            plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
            if plaintext is None:
                return None

            # Deserialize capability (fail-closed on invalid format)
            cap = create_capability_from_dict(self.capability_label, plaintext)
            if cap is None:
                return None

            # For egress-paid-preset: use the derived policy
            if isinstance(cap, EgressPaidPresetCapability):
                # Check if expired (epoch-based)
                if cap.expires_at_epoch_k < self.ratchet.current_state.epoch_k:
                    return None  # Expired, fall back to static policy

                # Apply the ratchet-derived policy
                return self._check_ratchet_derived_egress_policy(host, cap)

            return None
        except Exception:  # noqa: BLE001
            return None

    def set_capability_registry(self, registry: Any) -> None:
        """Set the CapabilityRegistry for M2 descriptor loading.

        Args:
            registry: operator.license.elr.CapabilityRegistry instance.
        """
        self._capability_registry = registry

    def _check_ratchet_derived_egress_policy(
        self,
        host: str,
        cap: Any,
    ) -> EgressDecision | None:
        """Apply the ratchet-derived egress policy (similar to static policy check).

        Returns an EgressDecision using the ratchet-derived allowed/forbidden hosts,
        or None if the capability format doesn't support egress checks.
        """
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _lic_dir = _Path(__file__).resolve().parents[2] / "license"
            if str(_lic_dir) not in _sys.path:
                _sys.path.insert(0, str(_lic_dir))
            from elr_capabilities_m2 import EgressPaidPresetCapability
            if not isinstance(cap, EgressPaidPresetCapability):
                return None

            # Same logic as static policy check, but from ratchet-derived cap
            if host in cap.forbidden_hosts:
                return EgressDecision(
                    allowed=False,
                    host=host,
                    reason="host on ratchet-derived forbidden_hosts list",
                    matched_rule="forbidden_explicit",
                )

            if host in cap.allowed_hosts:
                return EgressDecision(
                    allowed=True,
                    host=host,
                    reason="host on ratchet-derived allowed_hosts list",
                    matched_rule="allowed_explicit",
                )

            # Default action from capability
            if cap.default_action == "allow":
                return EgressDecision(
                    allowed=True,
                    host=host,
                    reason="ratchet-derived default_action=allow (unmatched host)",
                    matched_rule="default_allow",
                )

            return EgressDecision(
                allowed=False,
                host=host,
                reason="ratchet-derived default_action=deny (unmatched host)",
                matched_rule="default_deny",
            )
        except Exception:  # noqa: BLE001
            return None

    # ----- internals -------------------------------------------------

    def _emit(
        self,
        event_type: str,
        severity: str,
        decision: EgressDecision,
        *,
        engine_id: str | None,
        persona: str | None,
        channel: str | None,
        chat_key: str | None,
    ) -> None:
        if self.audit_writer is None:
            return
        details: dict[str, Any] = {
            "host": decision.host,
            "reason": decision.reason,
            "matched_rule": decision.matched_rule,
        }
        if engine_id is not None:
            details["engine_id"] = engine_id
        if persona is not None:
            details["persona"] = persona
        if channel is not None:
            details["channel"] = channel
        if chat_key is not None:
            details["chat_key"] = chat_key
        try:
            _validate_audit_details(details)
            self.audit_writer(event_type, severity, details)
        except Exception:  # noqa: BLE001
            # Best-effort, mirrors L34 + engine_switch._audit() pattern.
            pass


# ----- forge-backed audit writer (production wiring) -----------------

def make_forge_audit_writer(audit_path: Path) -> AuditWriter:
    """Build an :data:`AuditWriter` that appends to the unified forge
    chain via :func:`forge.security_events.write_event`.

    Best-effort: if forge isn't importable (standalone test env),
    returns a no-op writer.
    """
    try:
        import sys
        here = Path(__file__).resolve()
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception:  # noqa: BLE001
        def _noop(event_type: str, severity: str, details: dict[str, Any]) -> None:
            return
        return _noop

    def _writer(event_type: str, severity: str, details: dict[str, Any]) -> None:
        try:
            write_event(
                audit_path, event_type,
                severity=severity, details=details,
            )
        except Exception:  # noqa: BLE001
            pass

    return _writer


def load_egress_gate_for_tenant(tenant_id: str, *, corvin_home: "Path | None" = None) -> "EgressGate | None":
    """Build an EgressGate for a tenant, or None to fail-open.

    Mirror of ``data_classification.load_guard_for_tenant`` — the single
    opt-in primitive for the L35 spawn-site gates (A2A, ACS). No
    ``tenant.corvin.yaml`` → None (no enforcement). Validates tenant_id and
    confines the resolved path under ``<home>/tenants`` (no traversal)."""
    if not isinstance(tenant_id, str) or not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,62}", tenant_id):
        try:
            from forge.tenants import validate_tenant_id as _vti  # type: ignore
            tenant_id = _vti(tenant_id)
        except Exception:  # noqa: BLE001
            return None
    home = corvin_home
    if home is None:
        env = os.environ.get("CORVIN_HOME")
        if env:
            home = Path(os.path.expanduser(os.path.expandvars(env)))
        else:
            try:
                from forge.paths import corvin_home as _ch  # type: ignore
                home = _ch()
            except Exception:  # noqa: BLE001
                home = Path.home() / ".corvin"
    home = Path(os.path.expanduser(os.path.expandvars(str(home))))
    tenants_root = (home / "tenants").resolve()
    cfg_path = (home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml").resolve()
    if tenants_root not in cfg_path.parents or not cfg_path.is_file():
        return None
    audit_path = cfg_path.parent / "forge" / "audit.jsonl"
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(cfg_path.read_text("utf-8"))
    except Exception as _yaml_exc:  # noqa: BLE001
        # R2-10: the config file EXISTS (passed is_file above) but is
        # unparseable. Returning None here would silently discard an operator's
        # `egress.default_action: deny`, a fail-OPEN that is asymmetric with the
        # L34 loader (FND-07, which fail-closes to a restrictive matrix on a
        # broken config). A compliance gate must not vanish because its config
        # is malformed: fail CLOSED with a deny-all gate so spawns are blocked
        # until the operator fixes the YAML, and log CRITICAL.
        import logging as _log
        _log.getLogger(__name__).critical(
            "L35 egress config %s is present but unparseable (%s) — failing "
            "CLOSED with a deny-all gate (ADR-0043/0070). Fix the YAML to restore "
            "egress.", cfg_path, type(_yaml_exc).__name__,
        )
        return EgressGate.from_tenant_config(
            {"spec": {"egress": {"enabled": True, "default_action": "deny"}}},
            audit_writer=make_forge_audit_writer(audit_path),
        )
    try:
        return EgressGate.from_tenant_config(cfg, audit_writer=make_forge_audit_writer(audit_path))
    except Exception as _cfg_exc:  # noqa: BLE001
        # Parsed YAML but the egress spec itself is invalid (e.g. bad
        # default_action, overlap). Same reasoning — fail closed, not open.
        import logging as _log
        _log.getLogger(__name__).critical(
            "L35 egress spec in %s is invalid (%s) — failing CLOSED with a "
            "deny-all gate. Fix the spec to restore egress.",
            cfg_path, type(_cfg_exc).__name__,
        )
        return EgressGate.from_tenant_config(
            {"spec": {"egress": {"enabled": True, "default_action": "deny"}}},
            audit_writer=make_forge_audit_writer(audit_path),
        )


def check_engine_egress(engine_id: str, tenant_id: str, *,
                        corvin_home: "Path | None" = None,
                        persona: str | None = None,
                        channel: str = "", chat_key: str = "") -> "str | None":
    """L35 pre-spawn egress check for out-of-band spawn sites (A2A, ACS).

    Returns None when allowed (no policy / disabled / pass), else a
    user-facing refusal string. DataFlowGuard-style: the EgressGate emits
    its own ``egress.blocked``/``approved`` audit. Host resolved via
    ``DEFAULT_ENGINE_HOSTS`` (unknown → fail-closed under a deny policy)."""
    gate = load_egress_gate_for_tenant(tenant_id, corvin_home=corvin_home)
    if gate is None:
        return None
    host = DEFAULT_ENGINE_HOSTS.get(engine_id, "unknown")
    # ADR-0181 M3 — a per-tenant provider assignment redirects the engine's egress
    # to the provider (or its proxy) host; validate THAT host, not the engine
    # default. Else e.g. hermes→ollama_cloud would be checked against "localhost"
    # and slip past a deny policy.
    try:
        from engine_models import resolve_engine_egress_host  # type: ignore
        _phost = resolve_engine_egress_host(tenant_id, engine_id)
        if _phost:
            host = _phost
    except Exception:  # noqa: BLE001
        pass
    try:
        decision = gate.validate(host, persona=persona, channel=channel, chat_key=chat_key)
    except Exception as _gate_exc:  # noqa: BLE001
        # ADR-0043 fail-closed: a gate exception means policy cannot be
        # enforced — block the spawn rather than silently allowing it.
        import logging as _log
        _log.getLogger(__name__).error(
            "L35 egress gate raised unexpectedly (fail-closed): %s", _gate_exc
        )
        return (
            f"[egress] Spawn rejected: engine {engine_id!r} — "
            "egress gate check failed (internal error, fail-closed per ADR-0043)"
        )
    if decision.allowed:
        return None
    return (f"[egress] Spawn rejected: engine {engine_id!r} host {host!r} is "
            f"not permitted by the tenant egress policy. {decision.reason}")
