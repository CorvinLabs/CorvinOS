"""Adversarial Compliance Tests — Hybrid Context Model (Phase 4, k=3).

Tests verify:
- GDPR Art. 5 (minimization): no PII leakage
- GDPR Art. 17 (erasure): complete cascade delete
- GDPR Art. 32 (security): hash-chain integrity
- Fail-closed: no silent errors
- Tenant isolation: no cross-tenant leakage
"""

import pytest
import json
from core.learning.hybrid_context import HybridContextModel, CascadeDeleteResult


@pytest.fixture(autouse=True)
def _core_audit_sandbox(tmp_path, monkeypatch):
    """Hybrid-context audit is fail-closed through the CORE writer: redirect the
    chain to a temp file (never the live chain) and bind the tenant context."""
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_1")



class TestGDPRMinimization:
    """GDPR Art. 5: Data minimization — no PII in layers."""

    def test_pii_patterns_blocked(self):
        """All PII patterns rejected (fail-closed)."""
        model = HybridContextModel("tenant_1")

        pii_payloads = [
            {"email": "user@example.com"},  # email
            {"phone": "+1-555-1234"},  # phone
            {"ssn": "123-45-6789"},  # SSN
            {"password": "secret123"},  # password
            {"api_key": "sk_test_abc123"},  # API key
            {"token": "eyJhbGciOiJIUzI1NiJ9"},  # token
            {"credit_card": "4111-1111-1111-1111"},  # credit card
            {"health_data": "blood_type_O"},  # health
        ]

        for i, payload in enumerate(pii_payloads):
            with pytest.raises(ValueError, match="Potential PII"):
                model.inject_layer(
                    user_id=f"user-{i}",
                    layer_name=f"bad_layer_{i}",
                    data=payload,
                    lom="test.py:L1",
                )

    def test_false_positive_allowed(self):
        """Non-PII containing keywords allowed."""
        model = HybridContextModel("tenant_1")

        # "password" inside prose is not a PII VALUE and "note" is not a PII
        # key: value regexes + whole-token key matching accept it (L-06).
        safe_payload = {"note": "password strength: high"}
        model.inject_layer(
            user_id="user-1",
            layer_name="notes",
            data=safe_payload,
            lom="test.py:L1",
        )

        # Better approach: sanitize by field name
        safe_payload2 = {"model_accuracy": 0.95, "learning_rate": 0.001}
        # This should pass (no PII keywords)
        model.inject_layer(
            user_id="user-1",
            layer_name="model_config",
            data=safe_payload2,
            lom="test.py:L1",
        )


class TestGDPRAnonymization:
    """GDPR Art. 5: Anonymize user IDs in layer data."""

    def test_layers_sanitized_on_export(self):
        """Layers exported without user_id (sanitized)."""
        model = HybridContextModel("tenant_1")

        # Inject layer (contains only non-PII data)
        model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"tone": "verbose", "detail_level": 5},
            lom="test.py:L1",
        )

        # Export layer data
        layers = model.injected_layers.get("user-1", [])
        layer_json = json.dumps([l.__dict__ for l in layers])

        # Verify no user_id in exported data (only in dict key)
        # The layer objects contain user_id in lom or other fields, but not exposed
        # This is architecture-level anonymization
        assert "user-1" not in layer_json or layer_json.count("user-1") == 0


class TestGDPRErasure:
    """GDPR Art. 17: Complete cascade delete with no remnants."""

    def test_cascade_delete_complete(self):
        """Cascade delete removes ALL user data."""
        model = HybridContextModel("tenant_1")

        # Create context for user-1
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d1": "chosen"}],
            profile={"pref": "verbose"},
            success_rate=0.75,
            attention_budget=1000,
        )

        model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"tone": "professional"},
            lom="test.py:L1",
        )

        # Verify user-1 data exists
        assert "user-1:s1" in model.base_snapshots
        assert "user-1" in model.injected_layers

        # Delete user-1
        result = model.delete_user_context("user-1")
        assert result.total > 0
        assert result.verification_complete is True

        # Verify complete removal
        assert not any(k.startswith("user-1") for k in model.base_snapshots)
        assert "user-1" not in model.injected_layers

        # Verify other users unaffected (if any)
        model.snapshot_base_context(
            user_id="user-2",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=500,
        )
        assert "user-2:s1" in model.base_snapshots  # user-2 still there

    def test_cascade_delete_idempotent(self):
        """Cascade delete is idempotent (safe to call twice)."""
        model = HybridContextModel("tenant_1")

        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        # First delete
        result1 = model.delete_user_context("user-1")
        count1 = result1.total

        # Second delete (should return 0)
        result2 = model.delete_user_context("user-1")
        count2 = result2.total

        assert count1 > 0
        assert count2 == 0  # Idempotent
        assert result2.verification_complete is True


class TestHashChainIntegrity:
    """GDPR Art. 32: Hash-chain immutability and verification."""

    def test_layer_chain_unbroken(self):
        """Layer chain maintains prev_hash references."""
        model = HybridContextModel("tenant_1")

        # Inject three layers
        hash1 = model.inject_layer(
            user_id="user-1",
            layer_name="layer1",
            data={"v": 1},
            lom="test.py:L1",
        )

        hash2 = model.inject_layer(
            user_id="user-1",
            layer_name="layer2",
            data={"v": 2},
            lom="test.py:L2",
        )

        hash3 = model.inject_layer(
            user_id="user-1",
            layer_name="layer3",
            data={"v": 3},
            lom="test.py:L3",
        )

        layers = model.injected_layers["user-1"]

        # Verify chain
        assert layers[0].prev_hash == ""
        assert layers[1].prev_hash == hash1
        assert layers[2].prev_hash == hash2
        assert layers[2].hash == hash3

    def test_base_chain_unbroken(self):
        """Base snapshots maintain chain links."""
        model = HybridContextModel("tenant_1")

        # Snapshot base twice for same user+session (unlikely, but possible)
        hash1 = model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": 1}],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        # Get base
        base1 = model.base_snapshots["user-1:s1"]
        assert base1.base_hash == hash1
        assert base1.prev_base_hash == ""  # First snapshot

        # Update snapshot (creates new entry if we modify the flow)
        hash2 = model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": 2}],  # Changed
            profile={},
            success_rate=0.6,  # Changed
            attention_budget=1000,
        )

        base2 = model.base_snapshots["user-1:s1"]
        assert base2.base_hash == hash2
        assert base2.prev_base_hash == hash1  # Links to prior


class TestFailClosedMerge:
    """Merge never silently fails — always returns a result."""

    def test_merge_partial_failure_continues(self):
        """Merge succeeds even if some layers fail."""
        model = HybridContextModel("tenant_1")

        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        base = model.base_snapshots["user-1:s1"]

        # Mix of good and bad layers — all correctly hash-chained (the merge
        # verifies the chain, L-04), so only the PII layers are dropped.
        specs = [
            ("good1", {"v": 1}),
            ("bad1", {"email": "test@example.com"}),
            ("good2", {"v": 2}),
            ("bad2", {"phone": "555-1234"}),
        ]
        layers, prev = [], ""
        for name, data in specs:
            h = model._compute_hash("1.0", data, prev)
            layers.append({"layer_name": name, "version": "1.0", "data": data,
                           "hash": h, "prev_hash": prev, "status": "injected"})
            prev = h

        # Merge should NOT raise, should drop bad layers
        merged = model.merge_with_fallback(base, layers)

        # Verify result
        assert merged is not None
        assert merged["attention_budget_remaining"] == 1000  # Base present

        # Verify good layers present
        assert "good1" in merged
        assert "good2" in merged

        # Verify bad layers dropped (not present)
        assert "bad1" not in merged
        assert "bad2" not in merged

    def test_merge_all_layers_fail_returns_base(self):
        """If all layers fail, merge returns base (no exception)."""
        model = HybridContextModel("tenant_1")

        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        base = model.base_snapshots["user-1:s1"]

        # All bad layers
        layers = [
            {"layer_name": "bad1", "data": {"email": "test@example.com"}, "status": "injected"},
            {"layer_name": "bad2", "data": {"ssn": "123-45-6789"}, "status": "injected"},
        ]

        # Merge should succeed, return base only
        merged = model.merge_with_fallback(base, layers)

        assert merged is not None
        assert merged["attention_budget_remaining"] == 1000  # Base preserved
        assert merged["success_rate"] == 0.5
        # No extra layers (all failed)
        assert not any(k.startswith("bad") for k in merged.keys())


class TestTenantIsolation:
    """Tenant isolation: no cross-tenant leakage."""

    def test_tenant_isolation_base(self, monkeypatch):
        """Base snapshots isolated by tenant."""
        model1 = HybridContextModel("tenant_1")
        model2 = HybridContextModel("tenant_2")

        # Create same user+session in different tenants
        model1.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": "tenant_1"}],
            profile={"t": 1},
            success_rate=0.7,
            attention_budget=1000,
        )

        # The core audit writer only commits for the process tenant
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_2")
        model2.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": "tenant_2"}],
            profile={"t": 2},
            success_rate=0.8,
            attention_budget=2000,
        )

        # Verify isolation
        base1 = model1.base_snapshots["user-1:s1"]
        base2 = model2.base_snapshots["user-1:s1"]

        assert base1.tenant_id == "tenant_1"
        assert base2.tenant_id == "tenant_2"
        assert base1.success_rate == 0.7
        assert base2.success_rate == 0.8

    def test_tenant_isolation_layers(self, monkeypatch):
        """Injected layers isolated by tenant."""
        model1 = HybridContextModel("tenant_1")
        model2 = HybridContextModel("tenant_2")

        # Inject same layer in different tenants
        model1.inject_layer(
            user_id="user-1",
            layer_name="style",
            data={"t": 1},
            lom="test.py:L1",
        )

        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_2")
        model2.inject_layer(
            user_id="user-1",
            layer_name="style",
            data={"t": 2},
            lom="test.py:L1",
        )

        # Verify isolation
        layers1 = model1.injected_layers["user-1"]
        layers2 = model2.injected_layers["user-1"]

        assert layers1[0].data["t"] == 1
        assert layers2[0].data["t"] == 2


class TestValidationErrorHandling:
    """Validation errors are fail-closed (exceptions raised, state unchanged)."""

    def test_invalid_user_id_rejected(self):
        """Empty user_id rejected."""
        model = HybridContextModel("tenant_1")

        with pytest.raises(ValueError):
            model.inject_layer(
                user_id="",
                layer_name="layer",
                data={"v": 1},
                lom="test.py:L1",
            )

    def test_invalid_session_id_rejected(self):
        """Empty session_id rejected in get_context."""
        model = HybridContextModel("tenant_1")

        with pytest.raises(ValueError):
            model.get_context(user_id="user-1", session_id="")

    def test_user_not_found(self):
        """get_context fails if user not found."""
        model = HybridContextModel("tenant_1")

        with pytest.raises(ValueError, match="No base context"):
            model.get_context(user_id="unknown-user", session_id="s1")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestEdgeCases:
    """Edge cases for production-ready behavior."""

    def test_empty_user_snapshot(self):
        """Edge case 1: Empty user (no decisions/outcomes) still snapshots."""
        model = HybridContextModel("tenant_1")

        # Snapshot with no decisions, empty profile
        base_hash = model.snapshot_base_context(
            user_id="empty-user",
            session_id="s1",
            decisions=[],  # Empty
            profile={},    # Empty
            success_rate=0.5,  # Default suppression
            attention_budget=0,  # No budget
        )

        assert base_hash is not None
        base = model.base_snapshots["empty-user:s1"]
        assert len(base.recent_decisions) == 0
        assert base.user_profile == {}
        assert base.success_rate == 0.5

    def test_concurrent_layer_isolation(self):
        """Edge case 2: Concurrent layer injection, per-user isolation."""
        model = HybridContextModel("tenant_1")

        # User 1 injects "user_style"
        hash1 = model.inject_layer(
            user_id="user-1",
            layer_name="user_style",
            data={"tone": "verbose"},
            lom="test.py:L1",
        )

        # User 2 injects same layer name
        hash2 = model.inject_layer(
            user_id="user-2",
            layer_name="user_style",
            data={"tone": "concise"},
            lom="test.py:L2",
        )

        # Verify isolation: different hashes, different data
        assert hash1 != hash2
        assert model.injected_layers["user-1"][0].data["tone"] == "verbose"
        assert model.injected_layers["user-2"][0].data["tone"] == "concise"

    def test_merge_preserves_small_n_suppression(self):
        """Edge case 3: Merge preserves small-n suppression from Phase 3."""
        model = HybridContextModel("tenant_1")

        # Snapshot with suppressed success_rate (< 10 outcomes)
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": 1}],
            profile={"learned": True},
            success_rate=0.5,  # Suppressed (from Phase 3)
            attention_budget=1000,
        )

        base = model.base_snapshots["user-1:s1"]
        layers = []

        # Merge should preserve suppressed rate
        merged = model.merge_with_fallback(base, layers)

        assert merged["success_rate"] == 0.5  # Not modified
        assert merged["user_profile"]["learned"] is True

    def test_delete_with_pending_layers_complete(self):
        """Edge case 4: Delete user with layers, guarantee complete removal."""
        model = HybridContextModel("tenant_1")

        # Create base
        model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[],
            profile={},
            success_rate=0.5,
            attention_budget=1000,
        )

        # Inject multiple layers
        for i in range(5):
            model.inject_layer(
                user_id="user-1",
                layer_name=f"layer_{i}",
                data={"v": i},
                lom=f"test.py:L{i}",
            )

        # Verify data exists
        assert len(model.injected_layers.get("user-1", [])) == 5

        # Delete
        result = model.delete_user_context("user-1")

        # Verify COMPLETE removal
        assert result.verification_complete is True
        assert result.deleted_layers == 5
        assert len(model.injected_layers.get("user-1", [])) == 0

    def test_snapshot_overwrite_chain_validity(self):
        """Edge case 5: Snapshot overwrite preserves chain validity."""
        model = HybridContextModel("tenant_1")

        # First snapshot
        hash1 = model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": 1}],
            profile={"v": 1},
            success_rate=0.5,
            attention_budget=1000,
        )

        base1 = model.base_snapshots["user-1:s1"]
        assert base1.base_hash == hash1

        # Overwrite with different data
        hash2 = model.snapshot_base_context(
            user_id="user-1",
            session_id="s1",
            decisions=[{"d": 1}, {"d": 2}],  # Different
            profile={"v": 2},  # Different
            success_rate=0.6,  # Different
            attention_budget=2000,  # Different
        )

        base2 = model.base_snapshots["user-1:s1"]

        # Verify overwrite
        assert hash1 != hash2  # Different hashes
        assert len(base2.recent_decisions) == 2  # New data
        assert base2.base_hash == hash2
        assert base2.prev_base_hash == hash1  # Chain link preserved


# ── Adversarial review 2026-09-03: L-03 / L-04 / L-05 / L-06 / L-12 ──────────


def _chain(tmp_path):
    import json as _json
    p = tmp_path / "audit.jsonl"
    if not p.exists():
        return []
    return [_json.loads(l) for l in p.read_text().splitlines() if l.strip()]


class TestL03Tier1LayerNameShadowing:
    """A layer NAMED like a Tier 1 field must not replace it in the merge."""

    @pytest.mark.parametrize("name", ["user_profile", "success_rate", "recent_decisions",
                                      "attention_budget_remaining", "tenant_id"])
    def test_inject_rejects_tier1_layer_name(self, name, tmp_path):
        model = HybridContextModel("tenant_1")
        with pytest.raises(ValueError, match="immutable Tier 1"):
            model.inject_layer(user_id="u1", layer_name=name, data={"style": "EVIL"}, lom="t:1")
        assert "u1" not in model.injected_layers
        events = [r["event_type"] for r in _chain(tmp_path)]
        assert "tier1_immutable_violation_attempted" in events

    def test_merge_drops_tier1_layer_name(self, tmp_path):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[{"d": 1}],
                                    profile={"style": "GENUINE"}, success_rate=0.9,
                                    attention_budget=100)
        base = model.base_snapshots["u1:s1"]
        data = {"style": "EVIL"}
        forged = {"layer_name": "user_profile", "version": "1.0", "data": data,
                  "hash": model._compute_hash("1.0", data, ""), "prev_hash": ""}
        merged = model.merge_with_fallback(base, [forged])
        assert merged["user_profile"] == {"style": "GENUINE"}
        assert merged["success_rate"] == 0.9


class TestL04ChainVerificationOnMerge:
    def test_forged_hash_dropped(self):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[], profile={},
                                    success_rate=0.5, attention_budget=1)
        base = model.base_snapshots["u1:s1"]
        merged = model.merge_with_fallback(base, [
            {"layer_name": "forged", "version": "1.0", "data": {"x": 1},
             "hash": "deadbeef", "prev_hash": "nope"},
        ])
        assert "forged" not in merged

    def test_broken_chain_link_dropped_but_genuine_kept(self):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[], profile={},
                                    success_rate=0.5, attention_budget=1)
        base = model.base_snapshots["u1:s1"]
        d1, d2 = {"a": 1}, {"b": 2}
        h1 = model._compute_hash("1.0", d1, "")
        h2 = model._compute_hash("1.0", d2, h1)
        # a self-consistent layer whose prev_hash does not link to the chain
        rogue = {"layer_name": "rogue", "version": "1.0", "data": {"r": 1},
                 "hash": model._compute_hash("1.0", {"r": 1}, "unrelated"), "prev_hash": "unrelated"}
        merged = model.merge_with_fallback(base, [
            {"layer_name": "l1", "version": "1.0", "data": d1, "hash": h1, "prev_hash": ""},
            rogue,
            {"layer_name": "l2", "version": "1.0", "data": d2, "hash": h2, "prev_hash": h1},
        ])
        assert "l1" in merged and "l2" in merged and "rogue" not in merged

    def test_tampered_content_dropped(self):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[], profile={},
                                    success_rate=0.5, attention_budget=1)
        model.inject_layer(user_id="u1", layer_name="style", data={"tone": "brief"}, lom="t:1")
        ctx_layers = [dict(l) for l in model.get_context("u1", "s1")["layers"]]
        ctx_layers[0]["data"] = {"tone": "TAMPERED"}
        merged = model.merge_with_fallback(model.base_snapshots["u1:s1"], ctx_layers)
        assert "style" not in merged

    def test_genuine_chain_from_get_context_merges(self):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[], profile={},
                                    success_rate=0.5, attention_budget=1)
        model.inject_layer(user_id="u1", layer_name="a", data={"x": 1}, lom="t:1")
        model.inject_layer(user_id="u1", layer_name="b", data={"y": 2}, lom="t:1")
        merged = model.get_context("u1", "s1")["merged"]
        assert merged["a"] == {"x": 1} and merged["b"] == {"y": 2}


class TestL05IngestionSnapshots:
    def test_layer_data_is_copied_on_inject(self):
        model = HybridContextModel("tenant_1")
        data = {"style": "a", "nested": [1]}
        h = model.inject_layer(user_id="u1", layer_name="style", data=data, lom="t:1")
        data["style"] = "MUTATED"
        data["nested"].append(2)
        stored = model.injected_layers["u1"][0]
        assert stored.data == {"style": "a", "nested": [1]}
        assert model._compute_hash(stored.version, stored.data, stored.prev_hash) == h

    def test_base_is_copied_on_snapshot(self):
        model = HybridContextModel("tenant_1")
        decs = [{"d": 1}]
        prof = {"p": 1}
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=decs, profile=prof,
                                    success_rate=0.5, attention_budget=1)
        decs.append({"d": 2})
        prof["p"] = 2
        base = model.base_snapshots["u1:s1"]
        assert base.recent_decisions == [{"d": 1}] and base.user_profile == {"p": 1}

    def test_merged_output_does_not_alias_base(self):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[{"d": 1}], profile={"p": 1},
                                    success_rate=0.5, attention_budget=1)
        merged = model.get_context("u1", "s1")["merged"]
        merged["recent_decisions"].append({"d": "X"})
        merged["user_profile"]["p"] = "X"
        base = model.base_snapshots["u1:s1"]
        assert base.recent_decisions == [{"d": 1}] and base.user_profile == {"p": 1}


class TestL06PiiValueDetection:
    @pytest.mark.parametrize("payload", [
        {"contact": "john.doe@example.com"},
        {"note": "call +49 171 1234567"},
        {"note": "call +1-555-1234"},
        {"ref": "DE89 3704 0044 0532 0130 00"},
        {"card": "4111-1111-1111-1111"},
        {"id": "123-45-6789"},
        {"auth": "sk_test_abcdefghijklmnop"},
        {"auth": "Bearer abcdefghijklmnopqrstuvwxyz"},
        {"auth": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcdefghijk"},
        {"where": "Silvio Jurk, Berliner Str. 5"},
        {"nested": {"deep": ["fine", {"x": "a@b.io"}]}},
        {"home_phone": "unknown"},
        {"apiKey": "x"},
        {"credit_card": "x"},
        {"health_data": "x"},
    ])
    def test_pii_values_and_keys_rejected(self, payload):
        model = HybridContextModel("tenant_1")
        with pytest.raises(ValueError, match="Potential PII"):
            model.inject_layer(user_id="u1", layer_name="l", data=payload, lom="t:1")

    @pytest.mark.parametrize("payload", [
        {"healthy_status": 1},
        {"tokens_used": 5},
        {"phonetic": "x"},
        {"note": "password strength: high"},
        {"latency_ms": 1725000000123},      # 13-digit timestamp, not Luhn-valid
        {"version": "2026.09.03"},
        {"tool_name": "phone_book_formatter"},
        {"model_accuracy": 0.95, "learning_rate": 0.001},
    ])
    def test_benign_payloads_accepted(self, payload):
        model = HybridContextModel("tenant_1")
        model.inject_layer(user_id="u1", layer_name="l", data=payload, lom="t:1")


class TestL12CoreAuditFailClosed:
    def test_every_operation_lands_on_core_chain(self, tmp_path):
        model = HybridContextModel("tenant_1")
        model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[], profile={},
                                    success_rate=0.5, attention_budget=1)
        model.inject_layer(user_id="u1", layer_name="a", data={"x": 1}, lom="t:1")
        model.get_context("u1", "s1")
        model.delete_user_context("u1")
        types = [r["event_type"] for r in _chain(tmp_path)]
        for expected in ("tier1_base_snapshotted", "tier2_layer_injected",
                         "hybrid_context_merge", "user_context_cascade_deleted"):
            assert expected in types, types
        for r in _chain(tmp_path):
            assert r["details"]["tenant_id"] == "tenant_1"
            assert "hash" in r and "audit_ref" in r["details"]

    def test_unavailable_writer_raises_and_nothing_is_stored(self, monkeypatch):
        from core.learning import event_persistence as ep

        def _down():
            raise RuntimeError("core audit writer unavailable")

        monkeypatch.setattr(ep, "_resolve_core_audit", _down)
        model = HybridContextModel("tenant_1")
        with pytest.raises(RuntimeError, match="core audit writer unavailable"):
            model.inject_layer(user_id="u1", layer_name="a", data={"x": 1}, lom="t:1")
        assert "u1" not in model.injected_layers
        with pytest.raises(RuntimeError):
            model.snapshot_base_context(user_id="u1", session_id="s1", decisions=[], profile={},
                                        success_rate=0.5, attention_budget=1)
        assert not model.base_snapshots

    def test_non_committed_write_is_not_silent(self, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")  # process tenant != model tenant
        model = HybridContextModel("tenant_1")
        with pytest.raises(RuntimeError, match="did not commit"):
            model.inject_layer(user_id="u1", layer_name="a", data={"x": 1}, lom="t:1")
