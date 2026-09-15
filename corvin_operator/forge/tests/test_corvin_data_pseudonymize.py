"""Phase 12.6 E2E — pseudonymisation seed resolution.

Covers:
  * resolve_seed precedence: explicit > env > vault > derived
  * vault_loader called only when no env/explicit seed
  * allow_derived=False → returns (None, "none") when no real seed
  * derived seeds are per-tenant + deterministic
  * mcp_handlers' data_register pipeline uses the seed in pseudonymize
  * different tenants → different pseudo tokens (unlinkability)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    DataPolicy,
    DataRegistry,
    PSEUDO_SEED_VAULT_KEY,
    RedactionPolicy,
    call_data_register,
    derived_seed,
    pseudonymize,
    resolve_seed,
)


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _write_csv(content: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write(content)
    fh.close()
    return Path(fh.name)


def _clear_env() -> None:
    os.environ.pop(PSEUDO_SEED_VAULT_KEY, None)


# ---------------------------------------------------------------------------
# resolve_seed — precedence
# ---------------------------------------------------------------------------

def test_resolve_explicit_wins():
    print("\n[resolve: explicit seed beats everything]")
    _clear_env()
    seed, src = resolve_seed(explicit="my-seed")
    t("seed", seed == "my-seed")
    t("source", src == "explicit")


def test_resolve_env_when_no_explicit():
    print("\n[resolve: env var picked up when no explicit]")
    _clear_env()
    os.environ[PSEUDO_SEED_VAULT_KEY] = "env-seed"
    try:
        seed, src = resolve_seed()
        t("seed", seed == "env-seed")
        t("source", src == "env")
    finally:
        _clear_env()


def test_resolve_vault_when_no_env():
    print("\n[resolve: vault_loader consulted when no env]")
    _clear_env()

    def fake_vault() -> dict[str, str]:
        return {PSEUDO_SEED_VAULT_KEY: "vault-seed"}

    seed, src = resolve_seed(vault_loader=fake_vault)
    t("seed", seed == "vault-seed")
    t("source", src == "vault")


def test_resolve_vault_loader_exception_does_not_propagate():
    print("\n[resolve: vault_loader exception → fall through to derived]")
    _clear_env()

    def bad_vault():
        raise RuntimeError("vault unreadable")

    seed, src = resolve_seed(vault_loader=bad_vault, tenant_id="acme")
    t("falls to derived", src == "derived")
    t("seed non-empty", isinstance(seed, str) and len(seed) > 0)


def test_resolve_derived_per_tenant():
    print("\n[resolve: derived seed is per-tenant deterministic]")
    _clear_env()
    s1, _ = resolve_seed(tenant_id="acme", vault_loader=lambda: {})
    s2, _ = resolve_seed(tenant_id="acme", vault_loader=lambda: {})
    s3, _ = resolve_seed(tenant_id="globex", vault_loader=lambda: {})
    t("same tenant → same seed", s1 == s2)
    t("different tenant → different seed", s1 != s3)


def test_resolve_allow_derived_false_returns_none():
    print("\n[resolve: allow_derived=False → (None, 'none')]")
    _clear_env()
    seed, src = resolve_seed(
        vault_loader=lambda: {},
        allow_derived=False,
    )
    t("seed None", seed is None)
    t("source none", src == "none")


# ---------------------------------------------------------------------------
# derived_seed
# ---------------------------------------------------------------------------

def test_derived_seed_stable():
    print("\n[derived_seed: stable per tenant]")
    a = derived_seed("acme")
    b = derived_seed("acme")
    t("stable", a == b)


def test_derived_seed_distinct_per_tenant():
    print("\n[derived_seed: different tenants → different seeds]")
    a = derived_seed("acme")
    b = derived_seed("globex")
    t("different", a != b)


# ---------------------------------------------------------------------------
# Integration: data_register pipeline uses the seed
# ---------------------------------------------------------------------------

def test_pseudonymize_strategy_yields_pseudo_tokens():
    print("\n[integration: pseudonymize strategy yields ***pseudo:*** tokens]")
    _clear_env()
    os.environ[PSEUDO_SEED_VAULT_KEY] = "test-seed-1"
    try:
        with tempfile.TemporaryDirectory() as td:
            reg = DataRegistry(Path(td))
            p = _write_csv(
                "email\nalice@example.com\nbob@example.com\nalice@example.com\n"
            )
            # Policy with pseudonymize for email
            policy = DataPolicy(
                default_strategy="redact",
                class_strategies={"email": "pseudonymize"},
            )
            try:
                result = call_data_register(
                    reg, {"path": str(p)},
                    tenant_id="acme",
                    policy=policy,
                )
                sample = result["snapshot"]["sample"]
                # All values should be pseudo-tokens
                for row in sample:
                    v = row.get("email", "")
                    t(f"pseudo prefix in {v!r}", v.startswith("***pseudo:"))
                # alice (rows 0+2) maps to same token
                t("alice consistent across rows",
                  sample[0]["email"] == sample[2]["email"])
                # alice ≠ bob
                t("alice != bob",
                  sample[0]["email"] != sample[1]["email"])
            finally:
                p.unlink()
    finally:
        _clear_env()


def test_different_tenants_yield_different_tokens():
    print("\n[integration: different tenants → unlinkable pseudo tokens]")
    _clear_env()
    # Drop env var so resolve_seed falls back to per-tenant derived.
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("email\nalice@example.com\n")
        policy = DataPolicy(
            default_strategy="redact",
            class_strategies={"email": "pseudonymize"},
        )
        try:
            r_a = call_data_register(
                reg, {"path": str(p)},
                tenant_id="acme",
                policy=policy,
            )
            r_g = call_data_register(
                reg, {"path": str(p)},
                tenant_id="globex",
                policy=policy,
            )
            tok_a = r_a["snapshot"]["sample"][0]["email"]
            tok_g = r_g["snapshot"]["sample"][0]["email"]
            t("both pseudo-tokens",
              tok_a.startswith("***pseudo:") and tok_g.startswith("***pseudo:"))
            t("cross-tenant unlinkable", tok_a != tok_g,
              detail=f"acme={tok_a} globex={tok_g}")
        finally:
            p.unlink()


def test_pseudonymize_via_explicit_seed_arg():
    print("\n[pseudonymize: explicit seed argument]")
    a = pseudonymize("alice", "email", seed="s1")
    b = pseudonymize("alice", "email", seed="s1")
    c = pseudonymize("alice", "email", seed="s2")
    t("s1 deterministic", a == b)
    t("s1 vs s2 different", a != c)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    test_resolve_explicit_wins()
    test_resolve_env_when_no_explicit()
    test_resolve_vault_when_no_env()
    test_resolve_vault_loader_exception_does_not_propagate()
    test_resolve_derived_per_tenant()
    test_resolve_allow_derived_false_returns_none()

    test_derived_seed_stable()
    test_derived_seed_distinct_per_tenant()

    test_pseudonymize_strategy_yields_pseudo_tokens()
    test_different_tenants_yield_different_tokens()
    test_pseudonymize_via_explicit_seed_arg()

    print(f"\n{'=' * 50}")
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
