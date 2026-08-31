"""ADR-0169 M1 — pre-dispatch gate pipelines (declarative source of truth).

The pre-dispatch compliance gates run in TWO hand-written sequences that differ
**by design**:

* ``adapter._run_pre_dispatch_gates`` (non-ClaudeCode engines):
  capabilities → license(engines_allowed) → engine-trust → L34 → L35 → L44.
* ``adapter._call_claude_streaming_via_engine`` (ClaudeCode, the base engine):
  engine-trust → L34 → L35 → capabilities → L44. There is **no** license
  engine-allowlist gate here — that gate only governs *non-base* engines, so the
  base engine legitimately omits it.

Because the two differ, a single linear list cannot represent both. This module
therefore records BOTH live orders verbatim (``GATE_PIPELINES``) and asserts the
**shared partial-order invariants** that BOTH must satisfy. A future edit that
reorders either sequence so a shared invariant breaks (e.g. egress before
classification) fails fast at boot + CI instead of silently shipping a
mis-ordered security chain.

Scope honesty (ADR-0169 M1): this is behaviour-preserving characterization +
invariant enforcement of the CURRENT orders, NOT a re-ordering and NOT an
execution-unification. Routing both call sites through one runner is the
deferred M2; reconciling the two into one shared partial order is part of it.
Layer numbers stay stable identifiers (ADR-0169 D1) — nothing is renumbered.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateSpec:
    """One pre-dispatch gate.

    ``key`` is the stable handle; ``layer_id`` ties it to the catalog;
    ``fail_mode`` DOCUMENTS the live contract (it is descriptive metadata, not
    runtime-enforced):
      * ``closed``   — any failure blocks the spawn (license, LIP, house-rules).
      * ``two-tier`` — fail-CLOSED when the spawn_gates module is absent, but
        fail-OPEN on an operational error *inside* the gate (the
        ``_check_compliance_or_fail`` / ``_check_egress_or_fail`` /
        ``_check_engine_trust_or_fail`` contract — see their docstrings).
    """
    key: str
    layer_id: str
    label: str
    fail_mode: str  # "closed" | "two-tier"


_DEFAULT = (
    GateSpec("capabilities", "LIP", "Layer-presence / capability gate (ADR-0141)", "closed"),
    GateSpec("license", "L92", "License engine-allowlist gate (ADR-0092)", "closed"),
    GateSpec("engine_trust", "L30.1b", "Engine-trust gate", "two-tier"),
    GateSpec("data_classification", "L34", "Data-classification + flow guard", "two-tier"),
    GateSpec("egress", "L35", "Network-egress lockdown", "two-tier"),
    GateSpec("house_rules", "L44", "Acceptable-use / house-rules gate (ADR-0143)", "closed"),
)

_CLAUDECODE = (
    GateSpec("engine_trust", "L30.1b", "Engine-trust gate", "two-tier"),
    GateSpec("data_classification", "L34", "Data-classification + flow guard", "two-tier"),
    GateSpec("egress", "L35", "Network-egress lockdown", "two-tier"),
    GateSpec("capabilities", "LIP", "Layer-presence / capability gate (ADR-0141)", "closed"),
    GateSpec("house_rules", "L44", "Acceptable-use / house-rules gate (ADR-0143)", "closed"),
)

# The two live sequences, keyed by engine class.
GATE_PIPELINES: dict[str, tuple[GateSpec, ...]] = {
    "default": _DEFAULT,        # adapter._run_pre_dispatch_gates
    "claudecode": _CLAUDECODE,  # adapter._call_claude_streaming_via_engine
}

# Path-gate (L10) is deliberately absent from both: it is a fail-closed HOOK at
# the filesystem-write boundary, a different enforcement point than pre-dispatch.

_VALID_FAIL_MODES = frozenset({"closed", "two-tier"})


def _pos(pipeline: tuple[GateSpec, ...], key: str) -> "int | None":
    for i, g in enumerate(pipeline):
        if g.key == key:
            return i
    return None


def assert_pipeline_invariants(pipeline: tuple[GateSpec, ...]) -> None:
    """Raise ``ValueError`` if a SHARED partial-order invariant is violated.

    Only invariants that BOTH live sequences satisfy are asserted, and each is
    applied only when its gates are present in *this* pipeline. These encode the
    load-bearing orderings; a future edit that breaks one fails fast rather than
    silently introducing a mis-ordered or fail-open security chain.
    """
    keys = [g.key for g in pipeline]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate gate keys: {keys}")
    for g in pipeline:
        if g.fail_mode not in _VALID_FAIL_MODES:
            raise ValueError(f"gate {g.key!r} has invalid fail_mode {g.fail_mode!r}")

    def before(a: str, b: str) -> bool:
        ia, ib = _pos(pipeline, a), _pos(pipeline, b)
        return ia is None or ib is None or ia < ib  # vacuously true if absent

    # I1: data-classification (L34) precedes egress (L35) — the class must be
    #     KNOWN before an egress decision. The single most load-bearing order.
    if not before("data_classification", "egress"):
        raise ValueError("data_classification (L34) must precede egress (L35)")
    # I2: engine-trust precedes data-classification (trust the engine before you
    #     route classified data through it).
    if not before("engine_trust", "data_classification"):
        raise ValueError("engine_trust must precede data_classification")
    # I3: where a license engine-allowlist gate is present, it precedes
    #     engine-trust (don't trust-check an unlicensed engine).
    if not before("license", "engine_trust"):
        raise ValueError("license must precede engine_trust when present")
    # I4: house-rules (acceptable-use) is the LAST gate before spawn in both.
    if "house_rules" in keys and keys[-1] != "house_rules":
        raise ValueError(f"house_rules must be last, got {keys[-1]!r}")


def gate_pipeline_self_test() -> "tuple[bool, str]":
    """Boot-time self-test over BOTH pipelines. Returns (ok, reason). Mirrors the
    path-gate self-test contract so the adapter can emit a CRITICAL audit event
    and surface a mis-ordered pipeline at boot."""
    try:
        for name, pl in GATE_PIPELINES.items():
            try:
                assert_pipeline_invariants(pl)
            except Exception as e:  # noqa: BLE001
                return False, f"{name}: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    return True, "verified"


# Fail fast at import: a mis-ordered registry is a developer error, caught here
# before any spawn path consumes it.
for _pl in GATE_PIPELINES.values():
    assert_pipeline_invariants(_pl)
