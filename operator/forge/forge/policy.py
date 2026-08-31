"""Workflow-level security policy for the forge.

The policy is the **safety envelope** Claude (the manager) cannot widen at
runtime. It lives at ``<workspace>/policy.json`` and is loaded once when the
runner / MCP server starts. All meta-level requests from the manager
(``meta.budget``, ``meta.allow_imports``, ``meta.network``) can only narrow
the envelope, never widen it.

If ``policy.json`` is absent, strict built-in defaults apply:

  - 10s CPU, 30s wall, 4 MiB stdout cap, 64 MiB artifact cap
  - rate limit 60 calls/min/tool
  - circuit breaker on (5 failures → 60s reset)
  - forbidden imports: socket, subprocess, ctypes, multiprocessing
  - no namespace allowlist (all names ok unless on forbidden list)
  - network: deny by default
  - audit hash-chain: on
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Budget:
    cpu_seconds: int = 10
    wall_seconds: int = 30
    output_bytes: int = 4 * 1024 * 1024
    artifact_bytes: int = 64 * 1024 * 1024

    def to_dict(self) -> dict[str, int]:
        return {
            "cpu_seconds":    self.cpu_seconds,
            "wall_seconds":   self.wall_seconds,
            "output_bytes":   self.output_bytes,
            "artifact_bytes": self.artifact_bytes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Budget":
        if not d:
            return cls()
        return cls(
            cpu_seconds    = int(d.get("cpu_seconds", 10)),
            wall_seconds   = int(d.get("wall_seconds", 30)),
            output_bytes   = int(d.get("output_bytes", 4 * 1024 * 1024)),
            artifact_bytes = int(d.get("artifact_bytes", 64 * 1024 * 1024)),
        )


@dataclass
class Policy:
    version: int = 1
    default_budget: Budget = field(default_factory=Budget)
    max_budget: Budget = field(default_factory=lambda: Budget(
        cpu_seconds=60, wall_seconds=300,
        output_bytes=16 * 1024 * 1024, artifact_bytes=256 * 1024 * 1024,
    ))
    forbidden_imports: list[str] = field(default_factory=lambda: [
        "socket", "subprocess", "ctypes", "multiprocessing",
    ])
    forbidden_tool_names: list[str] = field(default_factory=lambda: [
        "shell.*", "system.*",
    ])
    allowed_namespaces: list[str] | None = None  # None = all (subject to forbidden)
    rate_limit_default_per_minute: int = 60
    rate_limit_per_tool: dict[str, int] = field(default_factory=dict)
    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_reset_timeout: float = 60.0
    circuit_breaker_half_open_max: int = 2
    network_default: bool = False
    audit_hash_chain: bool = True
    # persona_namespaces — per-persona registration prefix. ``coder`` may only
    # register tool names that start with ``code.`` etc. Missing persona name
    # OR empty / missing prefix → wildcard (no gate, legacy behaviour).
    # The bundle default at operator/forge/forge/policy.json defines the
    # standard cowork personas; a workspace-level policy.json can override.
    persona_namespaces: dict[str, str] = field(default_factory=dict)
    # persona_secret_allow — per-persona allow-list of vault keys the persona's
    # forged tools may declare in meta.secrets. Fail-closed: a persona missing
    # from the map (or with an empty list) cannot use ANY secret. Operator sets
    # this explicitly per persona; the bundle default is empty so no persona
    # gets secrets without a deliberate operator decision in workspace policy.
    # Example workspace policy.json:
    #   "persona_secret_allow": {
    #       "research": ["OPENAI_API_KEY"],
    #       "browser":  []
    #   }
    persona_secret_allow: dict[str, list[str]] = field(default_factory=dict)
    # persona_sandbox_overrides — relax the strict default sandbox per persona.
    # Today the only configurable axis is `network`:
    #   {"browser": {"network": "allow"}}
    # means the browser persona's forged tools share the host network
    # namespace (loopback + outbound). Default = deny for any persona without
    # an entry. The bundle default at operator/forge/forge/policy.json lists
    # browser+research; workspace-level policy.json can append/override.
    persona_sandbox_overrides: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def _bundle_default_path(cls) -> Path:
        """Bundle-level policy.json shipped with the forge plugin. Provides
        the default ``persona_namespaces`` mapping for all standard cowork
        personas — any field set in the workspace-level policy.json wins."""
        return Path(__file__).resolve().parent / "policy.json"

    @classmethod
    def _load_bundle_defaults(cls) -> dict:
        """Read the bundle-default policy.json. Returns {} if absent or
        unreadable so the package still works without the bundle file."""
        p = cls._bundle_default_path()
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def load(cls, workspace_root: Path) -> "Policy":
        """Load <workspace>/policy.json with bundle-default fallback. Missing
        workspace file → strict built-ins plus bundle ``persona_namespaces``.
        """
        bundle = cls._load_bundle_defaults()

        p = Path(workspace_root) / "policy.json"
        if not p.exists():
            d: dict = {}
        else:
            try:
                d = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                raise ValueError(f"policy.json malformed: {e}") from e

        # Bundle merge: every key the workspace doesn't set falls back to the
        # bundle default. We only do this for the new persona_namespaces field
        # (the safe additive case) — the rest of the policy keeps the existing
        # "missing → built-in default" semantics so behaviour is unchanged for
        # any deployment that already had a policy.json.
        merged_persona_ns = dict(bundle.get("persona_namespaces") or {})
        merged_persona_ns.update(
            (d.get("persona_namespaces") or {})
            if isinstance(d.get("persona_namespaces"), dict) else {}
        )

        # persona_secret_allow merges per-persona: workspace replaces bundle
        # entry wholesale (operator who lists "research": ["X"] in workspace
        # gets exactly ["X"], not unioned with any bundle default).
        merged_persona_secret_allow: dict[str, list[str]] = {}
        for src in (bundle.get("persona_secret_allow") or {},
                    d.get("persona_secret_allow") or {}):
            if isinstance(src, dict):
                for k, v in src.items():
                    if isinstance(v, list) and all(
                        isinstance(x, str) for x in v
                    ):
                        merged_persona_secret_allow[k] = list(v)

        # Same merge semantics for persona_sandbox_overrides — bundle defines
        # the standard cowork map, workspace policy.json can append or
        # override. Workspace entries replace bundle entries wholesale (per
        # persona, not per axis) so an operator that wants to switch a
        # persona's network back to deny can simply add
        # {"browser": {"network": "deny"}} to their workspace policy.
        merged_persona_sandbox: dict[str, dict] = {}
        for src in (bundle.get("persona_sandbox_overrides") or {},
                    d.get("persona_sandbox_overrides") or {}):
            if isinstance(src, dict):
                for k, v in src.items():
                    if isinstance(v, dict):
                        merged_persona_sandbox[k] = dict(v)

        cb = d.get("circuit_breaker") or {}
        rl = d.get("rate_limit") or {}
        net = d.get("network") or {}
        audit = d.get("audit") or {}
        return cls(
            version=int(d.get("version", bundle.get("version", 1))),
            default_budget=Budget.from_dict(d.get("default_budget")),
            max_budget=Budget.from_dict(d.get("max_budget")) if d.get("max_budget")
                       else cls().max_budget,
            forbidden_imports=list(d.get("forbidden_imports",
                                          cls().forbidden_imports)),
            forbidden_tool_names=list(d.get("forbidden_tool_names",
                                             cls().forbidden_tool_names)),
            allowed_namespaces=list(d["allowed_namespaces"])
                if d.get("allowed_namespaces") is not None else None,
            rate_limit_default_per_minute=int(
                rl.get("default_calls_per_minute", 60)),
            rate_limit_per_tool=dict(rl.get("per_tool", {})),
            circuit_breaker_enabled=bool(cb.get("enabled", True)),
            circuit_breaker_failure_threshold=int(cb.get("failure_threshold", 5)),
            circuit_breaker_reset_timeout=float(cb.get("reset_timeout", 60)),
            circuit_breaker_half_open_max=int(cb.get("half_open_max", 2)),
            network_default=bool(net.get("default", False)),
            audit_hash_chain=bool(audit.get("hash_chain", True)),
            persona_namespaces=merged_persona_ns,
            persona_secret_allow=merged_persona_secret_allow,
            persona_sandbox_overrides=merged_persona_sandbox,
        )

    # -- persona sandbox overrides -----------------------------------------

    def network_for_persona(self, persona: str | None) -> bool:
        """True if *persona* may run forged tools with the host network
        namespace shared. Default: False (no entry → strict deny).

        Lookup is case-sensitive and exact — there is no fallback to a
        ``"default"`` entry; the safe default lives in code, not in the
        policy file, so an empty / missing policy.json keeps the historic
        no-network behaviour."""
        if not persona:
            return False
        entry = self.persona_sandbox_overrides.get(persona)
        if not isinstance(entry, dict):
            return False
        return entry.get("network") == "allow"

    def deny_loopback_for_persona(self, persona: str | None) -> bool:
        """True if *persona* should have loopback (127.0.0.0/8 + ::1 +
        IMDS 169.254.169.254) blocked even though host network is shared.

        Layer-16 v2 D — Loopback-Deny. Default: True for any persona that
        currently has ``network: allow``. The operator can opt back into
        loopback via ``persona_sandbox_overrides[<persona>][\"loopback\"] =
        \"allow\"``; setting ``\"loopback\": \"deny\"`` is the no-op default.

        For personas with ``network: deny`` (the historical no-network
        default), this returns False — there is no loopback to deny when
        the namespace is fully unshared.
        """
        if not persona:
            return False
        entry = self.persona_sandbox_overrides.get(persona)
        if not isinstance(entry, dict):
            return False
        if entry.get("network") != "allow":
            return False
        # Default: deny loopback. Only an explicit "allow" lifts it.
        return entry.get("loopback") != "allow"

    # -- persona secret allow-list -----------------------------------------

    def secrets_for_persona(self, persona: str | None) -> list[str]:
        """Return the env-var names *persona* may reference in
        ``meta.secrets``. Empty list = no secrets allowed (fail-closed
        default for any persona without an entry).

        Lookup is case-sensitive and exact — there is no fallback to a
        ``"default"`` entry. The whole point of the gate is that the
        operator sets it deliberately per persona.
        """
        if not persona:
            return []
        entry = self.persona_secret_allow.get(persona)
        if not isinstance(entry, list):
            return []
        return [k for k in entry if isinstance(k, str) and k]

    def secret_check(
        self, persona: str | None, requested: list[str],
    ) -> tuple[bool, list[str]]:
        """Gate a tool's ``meta.secrets`` against the persona's allow-list.

        Returns ``(allowed, denied)``. ``allowed=True`` and ``denied=[]``
        when every requested key is on the persona's allow-list (or
        ``requested`` is empty). Otherwise ``denied`` lists the keys the
        persona is not permitted to use.
        """
        if not requested:
            return True, []
        allowed_set = set(self.secrets_for_persona(persona))
        denied = [r for r in requested if r not in allowed_set]
        return (not denied), denied

    # -- namespace gate -----------------------------------------------------

    def namespace_for(self, persona: str | None) -> str | None:
        """Return the registration-prefix this persona owns, or None when
        the persona has no entry (= wildcard, no gate). Empty string also
        means wildcard so a deliberate ``""`` opt-out works."""
        if not persona:
            return None
        prefix = self.persona_namespaces.get(persona)
        if not prefix:
            return None
        return str(prefix)

    def namespace_check(
        self, persona: str | None, tool_name: str,
    ) -> tuple[bool, str]:
        """Gate a tool name against the persona's allowed prefix.

        Returns (allowed, reason). ``reason`` is empty when allowed=True; on
        denial it names the gate that fired so the audit event can record it.
        Wildcard cases (no persona env, persona not in map, empty prefix)
        always return ``(True, "")``.
        """
        prefix = self.namespace_for(persona)
        if prefix is None:
            return True, ""  # wildcard
        # Allow exact name == prefix (rare but useful) AND prefix-dot pattern.
        if tool_name == prefix or tool_name.startswith(prefix + "."):
            return True, ""
        return False, (
            f"namespace-gate: persona {persona!r} may only register tools "
            f"starting with {prefix + '.'!r}"
        )

    # -- envelope enforcement helpers ---------------------------------------

    def clamp_budget(self, requested: Budget | dict | None) -> tuple[Budget, dict]:
        """Apply the envelope: per-call budget can only narrow vs. max_budget,
        and falls back to default_budget if nothing requested.

        Returns (clamped_budget, clamp_info). ``clamp_info`` is a dict of
        {field: (requested, applied)} for fields that were actually clamped.
        Empty dict = no clamping happened.
        """
        if requested is None:
            return self.default_budget, {}
        if isinstance(requested, dict):
            requested = Budget.from_dict(requested)

        clamp_info: dict[str, tuple[int, int]] = {}
        applied = Budget()
        for fld in ("cpu_seconds", "wall_seconds",
                    "output_bytes", "artifact_bytes"):
            req_v = getattr(requested, fld)
            max_v = getattr(self.max_budget, fld)
            new_v = min(req_v, max_v)
            setattr(applied, fld, new_v)
            if new_v != req_v:
                clamp_info[fld] = (req_v, new_v)
        return applied, clamp_info

    def name_allowed(self, name: str) -> tuple[bool, str]:
        """Check tool name against forbidden globs and namespace allowlist.

        Returns (allowed, reason). When allowed=False, reason names the rule
        that fired ('forbidden:<glob>' or 'namespace:<ns>').
        """
        # 1. Forbidden globs (absolute deny)
        for glob in self.forbidden_tool_names:
            if fnmatch.fnmatch(name, glob):
                return False, f"forbidden:{glob}"
        # 2. Namespace allowlist (if set)
        if self.allowed_namespaces is not None:
            ns = name.split(".", 1)[0] if "." in name else name
            if ns not in self.allowed_namespaces:
                return False, f"namespace_not_allowed:{ns}"
        return True, ""

    def rate_limit_for(self, name: str) -> int:
        """Resolve calls-per-minute for a specific tool name."""
        return int(self.rate_limit_per_tool.get(
            name, self.rate_limit_default_per_minute))
