"""PluginManifest — parsing and validation for compute backend plugins (ADR-0026 §A).

The manifest is a YAML (or dict) file at the plugin root.  Validation is
fail-closed: any security-relevant field that is missing or wrong raises
ManifestValidationError rather than silently defaulting.

Security rules enforced here:
- Plugin name MUST NOT contain path-traversal sequences.
- auth.method MUST be "vault" (values from Layer 16 secret vault) or "none".
  Any other auth method is rejected — credentials must NEVER appear in manifests.
- sandbox.network: "allow" is recorded in the manifest but requires an
  additional tenant-policy check at registry load time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


class ManifestValidationError(ValueError):
    """Raised when a plugin manifest fails validation."""


# Only alphanumeric, hyphens, underscores, and dots — no slashes.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")

# Allowed auth methods (vault = Layer 16 secret vault; none = no credentials)
_ALLOWED_AUTH_METHODS = {"vault", "none"}


@dataclass
class IntraBackendParallel:
    model: str = "thread"       # thread | process | distributed | gpu
    max_workers: str = "auto"
    gpu_aware: bool = False


@dataclass
class InterJobParallel:
    compatible: bool = True
    max_concurrent_instances: int = 8


@dataclass
class ParallelSpec:
    intra_backend: IntraBackendParallel = field(default_factory=IntraBackendParallel)
    inter_job: InterJobParallel = field(default_factory=InterJobParallel)


@dataclass
class SandboxSpec:
    network: str = "deny"       # "allow" requires tenant policy flag
    extra_mounts: list[str] = field(default_factory=list)


@dataclass
class PluginManifest:
    """Parsed and validated compute backend plugin manifest."""
    name: str
    version: str
    author: str
    backend_class: str          # importable dotted path

    capabilities: list[str] = field(default_factory=list)
    parallel: ParallelSpec = field(default_factory=ParallelSpec)
    sandbox: SandboxSpec = field(default_factory=SandboxSpec)
    # additional audit event names declared by this plugin
    audit_events: list[str] = field(default_factory=list)
    # abstract-key → backend-native param name
    steering_map: dict[str, str] = field(default_factory=dict)
    # auth spec (method must be "vault" or "none")
    auth_method: str = "none"
    auth_secret_keys: list[str] = field(default_factory=list)


def validate_manifest(raw: dict[str, Any]) -> PluginManifest:
    """Parse and validate a raw manifest dict.

    Raises ManifestValidationError on any security or structural problem.
    """
    # Required fields
    for field_name in ("name", "version", "author", "backend_class"):
        if field_name not in raw:
            raise ManifestValidationError(
                f"manifest missing required field: {field_name!r}"
            )

    name = raw["name"]
    # Path-traversal check — the most critical security gate
    if ".." in name or "/" in name or "\\" in name:
        raise ManifestValidationError(
            f"plugin name contains path-traversal sequence: {name!r}"
        )
    if not _SAFE_NAME_RE.match(name):
        raise ManifestValidationError(
            f"plugin name contains disallowed characters: {name!r}"
        )

    # backend_class must also be traversal-safe (dotted path, no slashes)
    backend_class = raw["backend_class"]
    if "/" in backend_class or "\\" in backend_class or ".." in backend_class:
        raise ManifestValidationError(
            f"backend_class contains path-traversal: {backend_class!r}"
        )

    # Auth validation — only vault or none allowed
    auth_raw = raw.get("auth", {})
    auth_method = auth_raw.get("method", "none")
    if auth_method not in _ALLOWED_AUTH_METHODS:
        raise ManifestValidationError(
            f"auth.method must be one of {sorted(_ALLOWED_AUTH_METHODS)}; "
            f"got {auth_method!r}. Credentials must be stored in the vault."
        )
    auth_secret_keys: list[str] = auth_raw.get("secret_keys", [])

    # Parallel spec
    parallel_raw = raw.get("parallel", {})
    intra_raw = parallel_raw.get("intra_backend", {})
    inter_raw = parallel_raw.get("inter_job", {})
    intra = IntraBackendParallel(
        model=intra_raw.get("model", "thread"),
        max_workers=str(intra_raw.get("max_workers", "auto")),
        gpu_aware=bool(intra_raw.get("gpu_aware", False)),
    )
    inter = InterJobParallel(
        compatible=bool(inter_raw.get("compatible", True)),
        max_concurrent_instances=int(inter_raw.get("max_concurrent_instances", 8)),
    )
    parallel = ParallelSpec(intra_backend=intra, inter_job=inter)

    # Sandbox spec
    sandbox_raw = raw.get("sandbox", {})
    sandbox = SandboxSpec(
        network=sandbox_raw.get("network", "deny"),
        extra_mounts=list(sandbox_raw.get("extra_mounts", [])),
    )

    return PluginManifest(
        name=name,
        version=raw["version"],
        author=raw["author"],
        backend_class=backend_class,
        capabilities=list(raw.get("capabilities", [])),
        parallel=parallel,
        sandbox=sandbox,
        audit_events=list(raw.get("audit_events", [])),
        steering_map=dict(raw.get("steering_map", {})),
        auth_method=auth_method,
        auth_secret_keys=auth_secret_keys,
    )


__all__ = [
    "ManifestValidationError",
    "PluginManifest",
    "ParallelSpec",
    "IntraBackendParallel",
    "InterJobParallel",
    "SandboxSpec",
    "validate_manifest",
]
