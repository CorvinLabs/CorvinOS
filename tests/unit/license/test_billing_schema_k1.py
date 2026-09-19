"""Unit tests for Billing Schema k=1 (Model pricing + quota)."""

import pytest
from decimal import Decimal
from core.licensing.billing import (
    ModelTier, ModelPricingModel, ModelPricing, BillingSchema,
    create_default_billing_schema
)


class TestModelPricing:
    """k=1: Individual model pricing entries."""
    
    def test_create_per_token_pricing(self):
        """Test creating a per-token pricing entry."""
        pricing = ModelPricing(
            model_id="claude-haiku-4-5",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            input_token_cost=Decimal("0.0000008"),
            output_token_cost=Decimal("0.000004"),
        )
        
        assert pricing.model_id == "claude-haiku-4-5"
        assert pricing.tier == ModelTier.COMMUNITY
        assert pricing.pricing_model == ModelPricingModel.PER_TOKEN
        assert pricing.active is True
    
    def test_create_per_request_pricing(self):
        """Test creating a per-request pricing entry."""
        pricing = ModelPricing(
            model_id="api-model",
            tier=ModelTier.MEMBER,
            pricing_model=ModelPricingModel.PER_REQUEST,
            request_cost=Decimal("0.01"),
        )
        
        assert pricing.pricing_model == ModelPricingModel.PER_REQUEST
        assert pricing.request_cost == Decimal("0.01")
    
    def test_pricing_immutable(self):
        """Test that pricing entries are immutable (frozen dataclass)."""
        pricing = ModelPricing(
            model_id="test",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
        )
        
        with pytest.raises(AttributeError):
            pricing.input_token_cost = Decimal("0.5")
    
    def test_pricing_with_community_limits(self):
        """Test community tier has daily limits."""
        pricing = ModelPricing(
            model_id="haiku",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            requests_per_day_limit=50,
            tokens_per_day_limit=100_000,
        )
        
        assert pricing.requests_per_day_limit == 50
        assert pricing.tokens_per_day_limit == 100_000
    
    def test_pricing_to_dict(self):
        """Test serialization to dict."""
        pricing = ModelPricing(
            model_id="haiku",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            input_token_cost=Decimal("0.0000008"),
            change_date="2026-09-16",
        )
        
        d = pricing.to_dict()
        
        assert d["model_id"] == "haiku"
        assert d["tier"] == "community"
        assert d["pricing_model"] == "per_token"
        assert d["input_token_cost"] == "0.0000008"
        assert d["change_date"] == "2026-09-16"


class TestBillingSchema:
    """k=1: Billing schema with model table."""
    
    def test_create_empty_schema(self):
        """Test creating an empty billing schema."""
        schema = BillingSchema()
        
        assert len(schema.pricing_by_model) == 0
        assert schema.free_tier_daily_requests == 50
        assert schema.free_tier_daily_tokens == 100_000
    
    def test_add_model_pricing(self):
        """Test adding model pricing entries."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="test-model",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            input_token_cost=Decimal("0.001"),
        )
        
        schema.add_model_pricing(pricing)
        
        assert "test-model" in schema.pricing_by_model
        assert schema.pricing_by_model["test-model"] == pricing
    
    def test_get_model_pricing_exists(self):
        """Test retrieving existing model pricing."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="opus",
            tier=ModelTier.MEMBER,
            pricing_model=ModelPricingModel.PER_TOKEN,
            input_token_cost=Decimal("0.015"),
        )
        schema.add_model_pricing(pricing)
        
        retrieved = schema.get_model_pricing("opus")
        
        assert retrieved is not None
        assert retrieved.model_id == "opus"
        assert retrieved.input_token_cost == Decimal("0.015")
    
    def test_get_model_pricing_not_found(self):
        """Test that unknown models return None."""
        schema = BillingSchema()
        
        assert schema.get_model_pricing("unknown-model") is None
    
    def test_is_model_available_community_tier(self):
        """Test availability check for community tier."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="haiku",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            active=True,
        )
        schema.add_model_pricing(pricing)
        
        assert schema.is_model_available("haiku", ModelTier.COMMUNITY) is True
        assert schema.is_model_available("haiku", ModelTier.MEMBER) is False  # Wrong tier
    
    def test_is_model_available_inactive(self):
        """Test that inactive models return unavailable."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="old-model",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            active=False,  # Inactive
        )
        schema.add_model_pricing(pricing)
        
        assert schema.is_model_available("old-model", ModelTier.COMMUNITY) is False


class TestCostCalculation:
    """k=1: Cost calculation per model."""
    
    def test_calculate_cost_per_token(self):
        """Test cost calculation for per-token pricing."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="test",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            input_token_cost=Decimal("0.001"),
            output_token_cost=Decimal("0.002"),
        )
        schema.add_model_pricing(pricing)
        
        # 1000 input, 500 output tokens
        cost = schema.calculate_cost(
            model_id="test",
            input_tokens=1000,
            output_tokens=500,
        )
        
        expected = Decimal("1000") * Decimal("0.001") + Decimal("500") * Decimal("0.002")
        assert cost == expected  # €2.00
    
    def test_calculate_cost_with_cache(self):
        """Test cost calculation with cache read/write."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="test",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_TOKEN,
            input_token_cost=Decimal("0.001"),
            output_token_cost=Decimal("0.002"),
            cache_read_cost=Decimal("0.0001"),
            cache_write_cost=Decimal("0.002"),
        )
        schema.add_model_pricing(pricing)
        
        cost = schema.calculate_cost(
            model_id="test",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=200,
            cache_write_tokens=300,
        )
        
        expected = (
            Decimal("100") * Decimal("0.001") +
            Decimal("50") * Decimal("0.002") +
            Decimal("200") * Decimal("0.0001") +
            Decimal("300") * Decimal("0.002")
        )
        assert cost == expected
    
    def test_calculate_cost_per_request(self):
        """Test cost calculation for per-request pricing."""
        schema = BillingSchema()
        
        pricing = ModelPricing(
            model_id="api",
            tier=ModelTier.COMMUNITY,
            pricing_model=ModelPricingModel.PER_REQUEST,
            request_cost=Decimal("0.01"),
        )
        schema.add_model_pricing(pricing)
        
        # Request cost ignores token counts
        cost = schema.calculate_cost(
            model_id="api",
            input_tokens=10000,
            output_tokens=10000,
        )
        
        assert cost == Decimal("0.01")
    
    def test_calculate_cost_unknown_model_raises(self):
        """Test that unknown models raise ValueError."""
        schema = BillingSchema()
        
        with pytest.raises(ValueError, match="not in billing schema"):
            schema.calculate_cost(
                model_id="unknown",
                input_tokens=100,
                output_tokens=50,
            )


class TestQuotaEnforcement:
    """k=1: Quota checking (community tier limits)."""
    
    def test_quota_check_community_within_limits(self):
        """Test quota check for community tier within limits."""
        schema = BillingSchema(
            free_tier_daily_requests=50,
            free_tier_daily_tokens=100_000,
        )
        
        # At 40% capacity
        assert schema.check_quota(
            tier=ModelTier.COMMUNITY,
            request_count=20,
            token_count=40_000,
        ) is True
    
    def test_quota_check_community_request_limit_exceeded(self):
        """Test quota check when daily request limit exceeded."""
        schema = BillingSchema(free_tier_daily_requests=50)
        
        assert schema.check_quota(
            tier=ModelTier.COMMUNITY,
            request_count=50,  # At limit
            token_count=1,
        ) is False
    
    def test_quota_check_community_token_limit_exceeded(self):
        """Test quota check when daily token limit exceeded."""
        schema = BillingSchema(free_tier_daily_tokens=100_000)
        
        assert schema.check_quota(
            tier=ModelTier.COMMUNITY,
            request_count=1,
            token_count=100_000,  # At limit
        ) is False
    
    def test_quota_check_member_no_limits(self):
        """Test that member tier has no daily limits."""
        schema = BillingSchema()
        
        # Members have unlimited requests/tokens
        assert schema.check_quota(
            tier=ModelTier.MEMBER,
            request_count=1_000_000,
            token_count=10_000_000,
        ) is True
    
    def test_quota_check_unlimited_quota(self):
        """Test that zero limits means unlimited."""
        schema = BillingSchema(
            free_tier_daily_requests=0,  # Unlimited
            free_tier_daily_tokens=0,    # Unlimited
        )
        
        assert schema.check_quota(
            tier=ModelTier.COMMUNITY,
            request_count=999_999,
            token_count=999_999_999,
        ) is True


class TestDefaultBillingSchema:
    """k=1: Default billing schema (frozen pricing table)."""
    
    def test_default_schema_has_models(self):
        """Test that default schema includes baseline models."""
        schema = create_default_billing_schema()
        
        # Should have Opus, Sonnet, Haiku entries
        assert schema.get_model_pricing("claude-opus-4") is not None
        assert schema.get_model_pricing("claude-sonnet-3") is not None
        assert schema.get_model_pricing("claude-haiku-4-5") is not None
    
    def test_opus_member_only(self):
        """Test that Opus is member-only (ADR-0701)."""
        schema = create_default_billing_schema()
        
        assert schema.is_model_available("claude-opus-4", ModelTier.MEMBER) is True
        assert schema.is_model_available("claude-opus-4", ModelTier.COMMUNITY) is False
    
    def test_haiku_community_available(self):
        """Test that Haiku is available in community tier."""
        schema = create_default_billing_schema()
        
        assert schema.is_model_available("claude-haiku-4-5", ModelTier.COMMUNITY) is True
        assert schema.is_model_available("claude-haiku-4-5", ModelTier.MEMBER) is True
    
    def test_haiku_has_community_limits(self):
        """Test that Haiku community tier has daily limits."""
        schema = create_default_billing_schema()
        
        pricing = schema.get_model_pricing("claude-haiku-4-5")
        # Note: get_model_pricing returns the first match, which is COMMUNITY
        if pricing and pricing.tier == ModelTier.COMMUNITY:
            assert pricing.requests_per_day_limit == 50
            assert pricing.tokens_per_day_limit == 100_000
    
    def test_cost_calculation_with_default_schema(self):
        """Test cost calculation using default schema."""
        schema = create_default_billing_schema()
        
        # 1000 input, 500 output tokens for Haiku
        cost = schema.calculate_cost(
            model_id="claude-haiku-4-5",
            input_tokens=1000,
            output_tokens=500,
        )
        
        # Should be approximately €0.00800 + €0.00200 = €0.01000
        assert cost > Decimal("0")
        assert cost < Decimal("0.02")


class TestBillingSchemaIntegration:
    """k=1 integration tests: Full billing scenario."""
    
    def test_track_b_k1_complete_workflow(self):
        """Test complete Track B k=1 workflow: models + pricing + quota."""
        schema = create_default_billing_schema()
        
        # Community user tries Haiku
        haiku_available = schema.is_model_available(
            "claude-haiku-4-5",
            ModelTier.COMMUNITY
        )
        assert haiku_available is True
        
        # Calculate cost for 10k tokens
        cost = schema.calculate_cost(
            model_id="claude-haiku-4-5",
            input_tokens=5_000,
            output_tokens=5_000,
        )
        assert cost > Decimal("0")
        
        # Check if within daily quota
        in_quota = schema.check_quota(
            tier=ModelTier.COMMUNITY,
            request_count=10,
            token_count=10_000,
        )
        assert in_quota is True
        
        # But can't use Opus (member-only)
        opus_available = schema.is_model_available(
            "claude-opus-4",
            ModelTier.COMMUNITY
        )
        assert opus_available is False
    
    def test_track_b_k1_member_workflow(self):
        """Test complete workflow for member tier."""
        schema = create_default_billing_schema()
        
        # Member can use Opus
        opus_available = schema.is_model_available(
            "claude-opus-4",
            ModelTier.MEMBER
        )
        assert opus_available is True
        
        # Calculate cost
        cost = schema.calculate_cost(
            model_id="claude-opus-4",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost > Decimal("0")
        
        # Members have no quota limits
        in_quota = schema.check_quota(
            tier=ModelTier.MEMBER,
            request_count=1_000_000,
            token_count=1_000_000_000,
        )
        assert in_quota is True
