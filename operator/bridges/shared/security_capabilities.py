"""security_capabilities.py — ADR-0141 Tier 3: SecurityCapabilityRegistry.

Each mandatory security layer self-registers at *import time* into a central
process-local registry. Before every engine spawn the adapter asserts that all
mandatory capabilities are present. The cascade this creates:

    delete path_gate.py  ->  ``import path_gate`` fails
                         ->  ``register_capability("path_gate", ...)`` never runs
                         ->  capability absent
                         ->  ``assert_capabilities_present()`` raises
                         ->  spawn blocked / adapter refuses to serve

This is **one tier** of the Layer Integrity Protocol (ADR-0141). It is NOT a
standalone security boundary: a determined operator who deletes a layer can also
patch *this* file. That residual is closed by Tier 1 (the RS256-signed layer
manifest hashes this file too) and Tier 2 (the network attestation carries the
``layer_integrity_hash`` so peers reject a tampered fork). Tier 3's job is the
cheap, structural, in-process guard that makes accidental or casual removal
fail-fast and loud.

CI lint contract:
  * MUST NOT ``import anthropic`` (network-membership enforcement, not an LLM
    code path).

Test contract:
  * Production code registers at import; test fixtures MUST reset state via
    :func:`_clear_registry`, never by registering fake capabilities that then
    leak into a real ``assert_capabilities_present`` call.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# ── Canonical capability names ──────────────────────────────────────────────
#
# These names are the SINGLE source of truth shared by Tier 1 (the manifest's
# ``mandatory_layers`` keys) and Tier 3 (this registry). Keep the two in lockstep
# — a name only present in one tier is a silent gap.

CAP_PATH_GATE = "path_gate"
CAP_AUDIT = "audit"
CAP_CONSENT_GATE = "consent_gate"
CAP_DATA_CLASSIFICATION = "data_classification"
CAP_EGRESS_GATE = "egress_gate"
CAP_ERASURE_ORCHESTRATOR = "erasure_orchestrator"
CAP_SELF_TEST = "self_test"
CAP_REMOTE_TRIGGER_RECEIVER = "remote_trigger_receiver"
CAP_HOUSE_RULES = "house_rules"

MANDATORY_CAPABILITIES: tuple[str, ...] = (
    CAP_PATH_GATE,
    CAP_AUDIT,
    CAP_CONSENT_GATE,
    CAP_DATA_CLASSIFICATION,
    CAP_EGRESS_GATE,
    CAP_ERASURE_ORCHESTRATOR,
    CAP_SELF_TEST,
    CAP_REMOTE_TRIGGER_RECEIVER,
    CAP_HOUSE_RULES,
)

# Single source of truth for capability versions. The per-layer registration
# blocks AND :func:`bootstrap_core_capabilities` both read from here, so a
# version bump happens in exactly one place — no drift between a layer's
# self-registration and the boot bootstrap.
CAP_VERSIONS: dict[str, str] = {
    CAP_PATH_GATE: "2.1",
    CAP_AUDIT: "3.0",
    CAP_CONSENT_GATE: "1.4",
    CAP_DATA_CLASSIFICATION: "1.0",
    CAP_EGRESS_GATE: "1.1",
    CAP_ERASURE_ORCHESTRATOR: "1.0",
    CAP_SELF_TEST: "1.0",
    CAP_REMOTE_TRIGGER_RECEIVER: "1.0",
    CAP_HOUSE_RULES: "1.0",
}

# (import-module-name, capability-name) for the in-process core layers.
# path_gate is excluded — it is an out-of-process hook, registered by file
# presence in :func:`_register_path_gate_by_presence`.
_INPROC_LAYERS: tuple[tuple[str, str], ...] = (
    ("audit", CAP_AUDIT),
    ("consent", CAP_CONSENT_GATE),
    ("data_classification", CAP_DATA_CLASSIFICATION),
    ("egress_gate", CAP_EGRESS_GATE),
    ("erasure_orchestrator", CAP_ERASURE_ORCHESTRATOR),
    ("self_test", CAP_SELF_TEST),
    ("remote_trigger_receiver", CAP_REMOTE_TRIGGER_RECEIVER),
    ("house_rules", CAP_HOUSE_RULES),
)


# ── Types ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CapabilityRecord:
    name: str
    version: str
    file_hash: str = ""


class CapabilityMissingError(RuntimeError):
    """Raised when one or more mandatory security capabilities are absent.

    ``missing`` is the sorted list of capability names that failed to register.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = sorted(missing)
        super().__init__(
            "mandatory security capabilities not registered: "
            + ", ".join(self.missing)
        )


_REGISTRY: dict[str, CapabilityRecord] = {}


# ── Registration / assertion ────────────────────────────────────────────────


def register_capability(name: str, *, version: str, file_hash: str = "") -> None:
    """Register a security capability. Called at module-import time by each layer.

    Idempotent: re-importing a module (or re-registering after a reload) simply
    overwrites the record. The ``file_hash`` is advisory at Tier 3 — Tier 1 owns
    the authoritative cryptographic hash of the layer files.
    """
    _REGISTRY[name] = CapabilityRecord(name=name, version=str(version),
                                       file_hash=str(file_hash or ""))


def assert_capabilities_present(
    mandatory: "tuple[str, ...] | list[str]" = MANDATORY_CAPABILITIES,
) -> None:
    """Raise :class:`CapabilityMissingError` if any mandatory capability is absent.

    Cheap (dict membership) — safe to call before every spawn.
    """
    missing = [cap for cap in mandatory if cap not in _REGISTRY]
    if missing:
        raise CapabilityMissingError(missing)


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def get_capability(name: str) -> "CapabilityRecord | None":
    return _REGISTRY.get(name)


def all_capabilities() -> dict[str, CapabilityRecord]:
    """Return a copy of the registry (for diagnostics / `corvin-layer list`)."""
    return dict(_REGISTRY)


def _clear_registry() -> None:
    """Test-only: reset registry state between cases. Never call from production."""
    _REGISTRY.clear()


# ── Hashing helper (shared with Tier 1) ─────────────────────────────────────


def module_self_hash(file_path: "str | Path") -> str:
    """Return ``sha256:<hex>`` of a file's bytes, or ``""`` if unreadable.

    Used by layers to self-report their on-disk hash at registration, and by
    Tier 1 (layer_integrity) to hash the same files for manifest comparison.
    """
    try:
        data = Path(file_path).read_bytes()
    except OSError:
        return ""
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ── Boot bootstrap ──────────────────────────────────────────────────────────


def bootstrap_core_capabilities() -> dict[str, bool]:
    """Best-effort import every in-process core layer so its top-level
    ``register_capability(...)`` runs, then register the out-of-process
    ``path_gate`` hook by file presence.

    Returns a ``{capability_name: registered_bool}`` map for diagnostics. A
    capability that fails to import (deleted/renamed/syntax-broken file) is left
    absent on purpose — a later :func:`assert_capabilities_present` then blocks.

    Idempotent and exception-safe: importing an already-imported module is a
    no-op, and an import failure never propagates out of bootstrap (it must not
    crash boot before the self-test can classify it).
    """
    # In-process layers: ensure each module is importable, then register its
    # capability deterministically from the canonical version map. This does NOT
    # rely on import side-effects (which only fire on the *first* import), so it
    # is idempotent and survives a registry reset — a module already cached in
    # sys.modules is still (re-)registered here.
    import sys as _sys

    for modname, capname in _INPROC_LAYERS:
        mod = _sys.modules.get(modname)
        if mod is None:
            try:
                __import__(modname)
                mod = _sys.modules.get(modname)
            except Exception:
                # Deleted / renamed / syntax-broken layer file: leave the
                # capability absent so the spawn-gate and Tier-1 check block.
                mod = None
        if mod is None:
            continue
        register_capability(
            capname,
            version=CAP_VERSIONS.get(capname, "unknown"),
            file_hash=module_self_hash(getattr(mod, "__file__", "") or ""),
        )

    # path_gate runs as an out-of-process PreToolUse hook, so it is never
    # imported into the adapter process. Verify its presence structurally:
    # the file must exist on disk where hooks.json points.
    _register_path_gate_by_presence()

    return {cap: (cap in _REGISTRY) for cap in MANDATORY_CAPABILITIES}


def _repo_root() -> Path:
    # operator/bridges/shared/security_capabilities.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def _register_path_gate_by_presence() -> None:
    """Register the ``path_gate`` capability iff the hook file exists on disk.

    The adapter never imports path_gate (it is a subprocess hook), so import-time
    self-registration cannot reach this process. We instead confirm the file is
    present at the canonical location and register it with its on-disk hash. If
    the file is missing, the capability stays absent and the spawn-gate blocks.
    """
    hook = _repo_root() / "operator" / "voice" / "hooks" / "path_gate.py"
    if not hook.is_file():
        return
    version = CAP_VERSIONS.get(CAP_PATH_GATE, "unknown")
    try:
        text = hook.read_text(encoding="utf-8", errors="replace")
        # path_gate.py declares CAPABILITY_VERSION at module level (see Tier 3
        # registration); parse it without importing (avoids running hook code).
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("CAPABILITY_VERSION"):
                version = stripped.split("=", 1)[1].strip().strip("\"'")
                break
    except OSError:
        pass
    register_capability(
        CAP_PATH_GATE,
        version=version,
        file_hash=module_self_hash(hook),
    )
