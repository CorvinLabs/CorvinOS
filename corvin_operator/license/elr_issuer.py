"""ADR-0167 — ELR descriptor issuer + shared ratchet builder (production activation).

This closes the two "remaining" items from the 2026-06-27 security review:

  (a) a SHARED ratchet builder both the issuer and the live spawn-path consumer
      use, so a stored descriptor unwraps deterministically; and
  (b) a local ISSUER that wraps an egress policy into a descriptor and writes its
      ``wrapped_bytes_b64`` into ``tenant.corvin.yaml::spec.elr.capabilities``.

Epoch-anchor decision (load-bearing). A stored descriptor must unwrap later, but
the ratchet tile is bound to ``epoch_input``. If the consumer used the LIVE,
per-event audit-chain head, the tile would change every spawn and a stored
descriptor could NEVER unwrap. So for stored descriptors we anchor BOTH sides to
a STABLE per-instance value (``stable_anchor``) at epoch 0:

    anchor = SHA256("elr-egress-anchor-v1:" + instance_id)

This preserves entanglement (root from the signed token), instance binding (root
+ anchor both bound to instance_id), fail-closed unwrap, and a tamper-evident
commitment — what it does NOT add is the per-event "non-precomputable future
tile" property (an attacker holding the root can compute this stable tile, same
as the ADR-0139 ceiling). The live-head, per-epoch-fresh mode is the dynamic /
networked tier (M3 external entropy) and remains future work; it cannot back a
static config file. This trade-off is intentional and documented.

Both issuer and consumer read the token bytes + instance_id from the SAME source
— the in-process active license claims — so they always agree without
coordination.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Make the sibling elr modules importable (operator/license on sys.path), exactly
# like the egress_gate consumer does. `operator` is the stdlib module, so a
# `from operator.license...` import would never work.
_LIC_DIR = Path(__file__).resolve().parent
if str(_LIC_DIR) not in sys.path:
    sys.path.insert(0, str(_LIC_DIR))

EGRESS_LABEL = "egress-paid-preset"
_ANCHOR_DOMAIN = b"elr-egress-anchor-v1:"


def stable_anchor(instance_id: str) -> bytes:
    """Deterministic 32-byte epoch anchor for stored egress descriptors.

    Shared by issuer and consumer so the derived tile matches. Per-instance, so a
    descriptor issued for one instance cannot be unwrapped by another.
    """
    return hashlib.sha256(_ANCHOR_DOMAIN + instance_id.encode("utf-8")).digest()


def build_egress_ratchet(token: bytes, instance_id: str):
    """Build the epoch-0 ratchet used by BOTH issuer and consumer for the stored
    egress descriptor. Root is instance-bound; anchor is the stable per-instance
    value. The ratchet is intentionally NOT advanced (stored-descriptor mode)."""
    from elr import EntangledRatchet, make_root_from_license_token  # type: ignore
    root = make_root_from_license_token(token, instance_id=instance_id)
    return EntangledRatchet(root, stable_anchor(instance_id))


def active_license_material() -> "tuple[bytes, str] | None":
    """Return ``(token_bytes, instance_id)`` from the in-process active license,
    or ``None`` when no instance-bound license is active.

    token_bytes = canonical JSON of the validated claims (the SOB plaintext the
    ELR root is derived from). instance_id = ``limits.instance_id_bound`` — the
    instance the license is cryptographically bound to, so issuer and consumer
    always agree and the descriptor is unusable off its bound host. Read-only;
    never mutates the validator's frozen active-license state.
    """
    try:
        from license import validator as _V  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            import validator as _V  # type: ignore
        except Exception:  # noqa: BLE001
            return None

    # Prefer the canary-checked accessor; fall back to the raw active-license name.
    claims = None
    _getter = getattr(_V, "_verified_license", None)
    if callable(_getter):
        try:
            claims = _getter()
        except Exception:  # noqa: BLE001
            claims = None
    if claims is None:
        claims = getattr(_V, "_ACTIVE_LICENSE", None)
    if claims is None:
        return None

    # CRITICAL: _ACTIVE_LICENSE is a RECURSIVE MappingProxyType (validator
    # freezes dicts→proxies and lists→tuples). json.dumps cannot serialize a
    # mappingproxy, so without un-proxying this raised TypeError → swallowed →
    # always None → permanent silent fail-open in production. Materialise back to
    # plain dict/list first (validator._unproxy).
    _unproxy = getattr(_V, "_unproxy", None)
    plain = _unproxy(claims) if callable(_unproxy) else claims
    if not isinstance(plain, dict):
        return None
    limits = plain.get("limits")
    instance_id = limits.get("instance_id_bound") if isinstance(limits, dict) else None
    if not instance_id or not isinstance(instance_id, str):
        return None
    try:
        token = json.dumps(plain, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(token) < 32:
        return None
    return token, instance_id


def issue_egress_descriptor(token: bytes, instance_id: str,
                            policy: dict[str, Any]) -> "tuple[str, str]":
    """Wrap an egress policy into a descriptor. Returns ``(wrapped_bytes_b64,
    commitment_hex)``. ``commitment_hex`` is a one-way hash of the tile, safe to
    log to the audit chain as a tamper-evident transcript entry (never the key)."""
    from elr import CapabilityEnvelope  # type: ignore
    ratchet = build_egress_ratchet(token, instance_id)
    tile = ratchet.derive_tile(EGRESS_LABEL)
    wrapped = CapabilityEnvelope.wrap(policy, tile)
    b64 = base64.b64encode(wrapped.to_bytes()).decode("ascii")
    commitment = hashlib.sha256(b"elr-commit-v1|" + tile).hexdigest()
    return b64, commitment


def build_egress_registry_and_ratchet_for_tenant(
    tenant_config: dict[str, Any] | None,
) -> "tuple[Any, Any] | None":
    """Consumer helper: build ``(ratchet, registry)`` for the live egress gate,
    or ``None`` to fall back to the static policy.

    Fail-open-to-static by design: returns ``None`` (→ static gate, unchanged
    behaviour) when there is no active instance-bound license OR the tenant has
    issued no egress descriptor. Only a tenant that has opted in (issued a
    descriptor) gets ratchet enforcement.
    """
    mat = active_license_material()
    if mat is None:
        return None
    token, instance_id = mat
    try:
        from elr import CapabilityRegistry  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    registry = CapabilityRegistry(tenant_config or {})
    if EGRESS_LABEL not in registry.all_labels():
        return None  # no descriptor → static fallback
    try:
        ratchet = build_egress_ratchet(token, instance_id)
    except Exception:  # noqa: BLE001
        return None
    return ratchet, registry


# ── Issuer CLI (self-hosting) ────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # type: ignore
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_descriptor_to_tenant_config(config_path: Path, label: str,
                                      wrapped_bytes_b64: str, *,
                                      version: int = 1) -> None:
    """Write/replace ``spec.elr.capabilities[label].wrapped_bytes_b64`` in
    tenant.corvin.yaml, preserving everything else. Atomic temp-file replace."""
    import yaml  # type: ignore
    cfg = _load_yaml(config_path)
    spec = cfg.setdefault("spec", {})
    if not isinstance(spec, dict):
        raise ValueError("tenant config 'spec' is not a mapping")
    elr = spec.setdefault("elr", {})
    caps = elr.setdefault("capabilities", {})
    caps[label] = {"wrapped_bytes_b64": wrapped_bytes_b64, "version": version}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(config_path)


def main(argv: "list[str] | None" = None) -> int:
    """CLI: issue an egress descriptor from the LOCAL active license and write it
    into the tenant config. Self-hosting: the instance issues its own descriptor
    from its own bound token — no external issuer needed.

    Usage:
      python elr_issuer.py --config <tenant.corvin.yaml> \\
          --allow host1,host2 --forbid host3 --default-action deny
    """
    import argparse
    ap = argparse.ArgumentParser(description="ELR egress descriptor issuer (self-hosting)")
    ap.add_argument("--config", required=True, help="path to tenant.corvin.yaml")
    ap.add_argument("--allow", default="", help="comma-separated allowed hosts")
    ap.add_argument("--forbid", default="", help="comma-separated forbidden hosts")
    ap.add_argument("--default-action", choices=["allow", "deny"], default="deny")
    ap.add_argument("--expires-epoch", type=int, default=0,
                    help="0 = never (stable-anchor mode stays at epoch 0)")
    args = ap.parse_args(argv)

    # The CLI is a standalone process: the active license is not loaded yet, so
    # load it from the environment/disk first (no-op if already loaded).
    try:
        from license import validator as _V  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            import validator as _V  # type: ignore
        except Exception:  # noqa: BLE001
            _V = None
    if _V is not None and hasattr(_V, "load_license_from_env"):
        try:
            _V.load_license_from_env()
        except Exception as _e:  # noqa: BLE001
            print(f"ELR issuer: license load failed ({type(_e).__name__}).", file=sys.stderr)

    mat = active_license_material()
    if mat is None:
        print("ELR issuer: no active instance-bound license — cannot issue.", file=sys.stderr)
        return 2
    token, instance_id = mat

    from elr_capabilities_m2 import EgressPaidPresetCapability  # type: ignore
    cap = EgressPaidPresetCapability(
        allowed_hosts=[h for h in args.allow.split(",") if h],
        forbidden_hosts=[h for h in args.forbid.split(",") if h],
        default_action=args.default_action,
        expires_at_epoch_k=args.expires_epoch,
    )
    b64, commitment = issue_egress_descriptor(token, instance_id, cap.to_dict())
    write_descriptor_to_tenant_config(Path(args.config), EGRESS_LABEL, b64)
    print(f"ELR issuer: wrote {EGRESS_LABEL} descriptor for instance "
          f"{instance_id!r} (commitment {commitment[:16]}…) → {args.config}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
