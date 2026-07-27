"""Plugin provenance and operator consent (ADR-0249).

The property tested hardest here is the one that makes `vetted` mean anything:
a signature that verifies against a key carried inside the manifest proves the
artifact is self-consistent and proves NOTHING about who produced it. `awpkg`'s
verifier stops there, which is correct for tamper-detection and insufficient for
provenance. Anyone can generate a keypair and self-sign, so the key must also be
pinned to a maintainer anchor.

The second-hardest is ship-dark behaviour: with the flag off, an existing install
carrying community plugins must boot exactly as it did before.

And a call-site test, because this whole session's recurring finding is that a
mechanism can be implemented, unit-tested and never invoked.
"""
from __future__ import annotations

import base64

import pytest
from corvin_plugins import trust
from corvin_plugins.trust import Verdict


def _keypair():
    crypto = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    priv = Ed25519PrivateKey.generate()
    der = priv.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return priv, base64.urlsafe_b64encode(der).decode().rstrip("=")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _record(**over) -> dict:
    base = {
        "plugin_id": "com.example.r",
        "plugin_type": "router_backend",
        "version": "1.0.0",
        "origin": "community",
    }
    base.update(over)
    return base


def _sign(record: dict, priv, pub_b64: str) -> dict:
    signed = dict(record)
    sig = priv.sign(trust.manifest_signing_digest(signed))
    signed["signature"] = {
        "algorithm": "ed25519",
        "public_key": pub_b64,
        "value": _b64(sig),
    }
    return signed


# ── Digest + signature ───────────────────────────────────────────────────────

def test_digest_excludes_the_signature_field():
    """A signature must never cover itself."""
    a = _record()
    b = dict(a, signature={"algorithm": "ed25519", "value": "x", "public_key": "y"})
    assert trust.manifest_signing_digest(a) == trust.manifest_signing_digest(b)


def test_digest_changes_when_content_changes():
    assert trust.manifest_signing_digest(_record()) != trust.manifest_signing_digest(
        _record(version="2.0.0")
    )


def test_valid_signature_from_a_pinned_key_verifies():
    priv, pub = _keypair()
    assert trust.verify_signature(_sign(_record(), priv, pub), trust_anchors=[pub])


def test_self_signed_key_that_is_not_pinned_is_refused():
    """THE hole this module exists to close.

    The signature is cryptographically valid and the manifest is intact. It still
    must not count as vetted, because the key is the author's own — otherwise
    'signed' means 'signed by someone', which is not a provenance claim.
    """
    priv, pub = _keypair()
    signed = _sign(_record(), priv, pub)
    assert trust.verify_signature(signed, trust_anchors=[pub]) is True
    _, other_pub = _keypair()
    assert trust.verify_signature(signed, trust_anchors=[other_pub]) is False


def test_empty_anchor_set_vets_nothing():
    """Ships with no anchor. Nothing may reach vetted until one is deposited."""
    priv, pub = _keypair()
    assert trust.verify_signature(_sign(_record(), priv, pub), trust_anchors=[]) is False


def test_tampered_manifest_fails_verification():
    priv, pub = _keypair()
    signed = _sign(_record(), priv, pub)
    signed["version"] = "9.9.9"
    assert trust.verify_signature(signed, trust_anchors=[pub]) is False


@pytest.mark.parametrize(
    "sig",
    [
        None,
        "not-a-dict",
        {},
        {"algorithm": "rsa", "public_key": "a", "value": "b"},
        {"algorithm": "ed25519", "public_key": 5, "value": "b"},
        {"algorithm": "ed25519", "public_key": "!!!", "value": "!!!"},
    ],
)
def test_malformed_signatures_fail_closed(sig):
    priv, pub = _keypair()  # noqa: F841
    rec = _record()
    if sig is not None:
        rec["signature"] = sig
    assert trust.verify_signature(rec, trust_anchors=[pub]) is False


# ── Anchors ──────────────────────────────────────────────────────────────────

def test_anchors_read_from_file_ignoring_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("CORVIN_PLUGIN_TRUST_ANCHORS", raising=False)
    d = tmp_path / "global"
    d.mkdir(parents=True)
    (d / "plugin_trust_anchors.txt").write_text(
        "# maintainer key\nAAAA\n\nBBBB\n", encoding="utf-8"
    )
    assert trust.load_trust_anchors(tmp_path) == ("AAAA", "BBBB")


def test_anchors_default_to_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("CORVIN_PLUGIN_TRUST_ANCHORS", raising=False)
    assert trust.load_trust_anchors(tmp_path) == ()


def test_env_anchors_win(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_PLUGIN_TRUST_ANCHORS", "XXX, YYY")
    assert trust.load_trust_anchors(tmp_path) == ("XXX", "YYY")


# ── Decisions ────────────────────────────────────────────────────────────────

def test_builtin_is_trusted(tmp_path):
    d = trust.evaluate(_record(origin="builtin"), corvin_home=tmp_path, enforcement=True)
    assert d.verdict is Verdict.BUILTIN and d.allowed


def test_vetted_with_pinned_signature_is_allowed(tmp_path):
    priv, pub = _keypair()
    signed = _sign(_record(origin="vetted"), priv, pub)
    d = trust.evaluate(
        signed, corvin_home=tmp_path, enforcement=True, trust_anchors=[pub]
    )
    assert d.verdict is Verdict.VETTED and d.allowed


def test_vetted_without_signature_is_forged_and_refused(tmp_path):
    """Refused, never downgraded to community.

    Downgrading would let a stripped signature turn a hard failure into a quiet
    one — the plugin would still load, just under a weaker label.
    """
    d = trust.evaluate(
        _record(origin="vetted"), corvin_home=tmp_path, enforcement=True
    )
    assert d.verdict is Verdict.FORGED and d.refused


def test_community_without_consent_is_refused_under_enforcement(tmp_path):
    d = trust.evaluate(_record(), corvin_home=tmp_path, enforcement=True)
    assert d.verdict is Verdict.COMMUNITY and d.refused


def test_community_with_consent_is_allowed(tmp_path):
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice")
    d = trust.evaluate(_record(), corvin_home=tmp_path, enforcement=True)
    assert d.allowed


def test_consent_is_per_plugin_not_global(tmp_path):
    """A blanket switch would turn a per-artifact decision into a one-time one."""
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice")
    other = trust.evaluate(
        _record(plugin_id="com.example.other"), corvin_home=tmp_path, enforcement=True
    )
    assert other.refused


def test_consent_grant_emits_an_audit_event(tmp_path):
    """An operator deciding to run unreviewed in-process code IS an Art. 30 event.

    (Unlike the build-time validator of ADR-0247, which must never touch the chain.)
    """
    seen = []
    trust.grant_consent(
        "com.example.r",
        corvin_home=tmp_path,
        operator="alice",
        digest="abc",
        audit_emit=lambda e, d: seen.append((e, d)),
    )
    assert seen and seen[0][0] == "plugin.consent_granted"
    assert seen[0][1]["operator"] == "alice"


def test_corrupt_consent_file_denies(tmp_path):
    p = tmp_path / "tenants" / "_default" / "global"
    p.mkdir(parents=True)
    (p / "plugin_consent.json").write_text("{ not json", encoding="utf-8")
    assert trust.consent_granted("com.example.r", corvin_home=tmp_path) is False


# ── Ship dark ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("origin", ["community", "vetted"])
def test_enforcement_off_refuses_nothing(tmp_path, origin):
    """The load-bearing ship-dark property (CLAUDE.md § Feature Flags).

    An install carrying community plugins must keep booting exactly as before
    until the operator turns the flag on deliberately.
    """
    d = trust.evaluate(
        _record(origin=origin), corvin_home=tmp_path, enforcement=False
    )
    assert d.allowed, "flag off must never refuse a plugin"


def test_verdict_is_still_computed_when_enforcement_is_off(tmp_path):
    """Off is a quiet path, not a blind one — the Console still shows the truth."""
    d = trust.evaluate(_record(origin="vetted"), corvin_home=tmp_path, enforcement=False)
    assert d.verdict is Verdict.FORGED and d.allowed


def test_flag_defaults_to_off():
    """Absent config must read as off, never as on."""
    assert trust.enforcement_enabled("_nonexistent_tenant_") is False


def test_flag_is_registered_so_the_console_can_toggle_it():
    """An unregistered flag resolves False forever and has no Settings row —
    the feature would be unreachable rather than merely off."""
    from corvin_console import feature_flags

    ids = {f.id for f in feature_flags.REGISTRY}
    assert trust.TRUST_ENFORCEMENT_FLAG in ids


# ── Call site ────────────────────────────────────────────────────────────────

def test_bootstrap_consults_trust_before_importing_the_plugin():
    """Call-site test — the recurring failure of this whole subsystem.

    A mechanism can be implemented, unit-tested and never invoked; that is what
    six of eleven plugin types turned out to be. This asserts `_load_one` actually
    calls the gate, and that it does so BEFORE `load_from_class_path` — a check
    placed after the import asks "may we run this?" about code already running.
    """
    import inspect

    from corvin_plugins import bootstrap

    src = inspect.getsource(bootstrap._load_one)
    assert "_trust_permits" in src, "_load_one does not consult the trust gate"
    assert src.index("_trust_permits") < src.index("load_from_class_path("), (
        "the trust gate runs AFTER the plugin module is imported — by then its "
        "top-level code has already executed"
    )


def test_trust_failure_degrades_to_allowing_rather_than_breaking_boot(monkeypatch, tmp_path):
    """A broken trust config must not cost the platform its boot."""
    from corvin_plugins import bootstrap

    def _boom(*a, **kw):
        raise RuntimeError("trust store on fire")

    monkeypatch.setattr(trust, "evaluate", _boom)

    class _Rec:
        plugin_id = "com.example.r"

        def to_dict(self):
            return _record()

    assert bootstrap._trust_permits(
        _Rec(), tenant_id="_default", corvin_home=tmp_path
    ) is True
