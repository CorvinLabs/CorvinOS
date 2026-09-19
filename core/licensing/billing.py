"""Billing schema for Model Access Control - Phase 2 k=1 (ADR-0700).

Migrated from core.license.models.billing to consolidate licensing subsystems.
Defines ModelTier (model access levels), ModelPricing, and BillingSchema.

License: Apache-2.0
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from decimal import Decimal


class ModelTier(str, Enum):
    """Model capability tier (ADR-0700 §3)."""
    COMMUNITY = "community"      # Free, limited capability
    MEMBER = "member"            # Paid subscription, full capability


class ModelPricingModel(str, Enum):
    """Pricing structure per model (ADR-0700 §8)."""
    PER_TOKEN = "per_token"      # Price per input + output token
    PER_REQUEST = "per_request"  # Flat fee per request
    PER_HOUR = "per_hour"        # Subscription model


@dataclass(frozen=True)
class ModelPricing:
    """Immutable pricing entry for a single model."""

    model_id: str                 # e.g., "claude-opus-4", "gpt-4"
    tier: ModelTier               # COMMUNITY or MEMBER
    pricing_model: ModelPricingModel  # How pricing works

    input_token_cost: Decimal = field(default=Decimal("0"))    # Cost per input token
    output_token_cost: Decimal = field(default=Decimal("0"))   # Cost per output token
    cache_read_cost: Decimal = field(default=Decimal("0"))     # Cost per cached token read
    cache_write_cost: Decimal = field(default=Decimal("0"))    # Cost per cache write

    request_cost: Decimal = field(default=Decimal("0"))        # Fixed cost per request
    hourly_cost: Decimal = field(default=Decimal("0"))         # Subscription cost per hour

    # Limits (0 = unlimited)
    requests_per_day_limit: int = field(default=0)             # Community tier limit
    tokens_per_day_limit: int = field(default=0)               # Community tier limit

    active: bool = field(default=True)                          # Whether model is available
    change_date: str = field(default="")                        # When pricing changed (ISO 8601)

    def to_dict(self) -> Dict:
        """Serialize to dict for config/audit."""
        return {
            "model_id": self.model_id,
            "tier": self.tier.value,
            "pricing_model": self.pricing_model.value,
            "input_token_cost": str(self.input_token_cost),
            "output_token_cost": str(self.output_token_cost),
            "cache_read_cost": str(self.cache_read_cost),
            "cache_write_cost": str(self.cache_write_cost),
            "request_cost": str(self.request_cost),
            "hourly_cost": str(self.hourly_cost),
            "requests_per_day_limit": self.requests_per_day_limit,
            "tokens_per_day_limit": self.tokens_per_day_limit,
            "active": self.active,
            "change_date": self.change_date,
        }


@dataclass
class BillingSchema:
    """Canonical billing model for all models (ADR-0700, source of truth)."""

    # Model pricing table (frozen at Phase 0)
    pricing_by_model: Dict[str, ModelPricing] = field(default_factory=dict)

    # Free tier allowance
    free_tier_daily_requests: int = 50              # Free users get 50 requests/day
    free_tier_daily_tokens: int = 100_000           # Free users get 100k tokens/day

    # Member tier allowance (subscription: €10/month/seat, ADR-0700 §5)
    member_tier_daily_requests: int = 0             # Unlimited
    member_tier_daily_tokens: int = 0               # Unlimited
    member_tier_monthly_cost: Decimal = Decimal("10")  # Per seat, per month (EUR)

    def add_model_pricing(self, pricing: ModelPricing):
        """Add or update model pricing (k=2 gates this)."""
        self.pricing_by_model[pricing.model_id] = pricing

    def get_model_pricing(self, model_id: str) -> Optional[ModelPricing]:
        """Get pricing for a model (returns None if not found)."""
        return self.pricing_by_model.get(model_id)

    def is_model_available(self, model_id: str, tier: ModelTier) -> bool:
        """Check if model is available for a tier."""
        pricing = self.get_model_pricing(model_id)
        if pricing is None:
            return False
        return pricing.active and pricing.tier == tier

    def calculate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> Decimal:
        """
        Calculate cost for a single inference.

        Returns:
            Total cost in EUR (as Decimal for precision)
        """
        pricing = self.get_model_pricing(model_id)
        if pricing is None:
            raise ValueError(f"Model {model_id} not in billing schema")

        if pricing.pricing_model == ModelPricingModel.PER_TOKEN:
            return (
                Decimal(input_tokens) * pricing.input_token_cost +
                Decimal(output_tokens) * pricing.output_token_cost +
                Decimal(cache_read_tokens) * pricing.cache_read_cost +
                Decimal(cache_write_tokens) * pricing.cache_write_cost
            )
        elif pricing.pricing_model == ModelPricingModel.PER_REQUEST:
            return pricing.request_cost
        elif pricing.pricing_model == ModelPricingModel.PER_HOUR:
            # Amortized hourly cost (k=3+ will refine this)
            return pricing.hourly_cost / Decimal("3600")  # Per second
        else:
            raise ValueError(f"Unknown pricing model: {pricing.pricing_model}")

    def check_quota(
        self,
        tier: ModelTier,
        request_count: int,
        token_count: int,
    ) -> bool:
        """
        Check if usage is within quota.

        Args:
            tier: COMMUNITY or MEMBER
            request_count: Daily request count so far
            token_count: Daily token count so far

        Returns:
            True if within quota, False if exceeded
        """
        if tier == ModelTier.COMMUNITY:
            if self.free_tier_daily_requests > 0 and request_count >= self.free_tier_daily_requests:
                return False
            if self.free_tier_daily_tokens > 0 and token_count >= self.free_tier_daily_tokens:
                return False
        # MEMBER has no limits (k=3 will enforce subscription validity)

        return True

    def to_dict(self) -> Dict:
        """Serialize billing schema for audit/config."""
        return {
            "pricing_by_model": {
                model_id: pricing.to_dict()
                for model_id, pricing in self.pricing_by_model.items()
            },
            "free_tier_daily_requests": self.free_tier_daily_requests,
            "free_tier_daily_tokens": self.free_tier_daily_tokens,
            "member_tier_daily_requests": self.member_tier_daily_requests,
            "member_tier_daily_tokens": self.member_tier_daily_tokens,
            "member_tier_monthly_cost": str(self.member_tier_monthly_cost),
        }


def create_default_billing_schema() -> BillingSchema:
    """
    Create default billing schema for k=1 (frozen).

    Prices as of 2026-09-16 (ADR-0700):
    - Opus 4: €0.015 / 1K input, €0.045 / 1K output (member only)
    - Sonnet 3: €0.003 / 1K input, €0.015 / 1K output (community + member)
    - Haiku 4.5: €0.0008 / 1K input, €0.004 / 1K output (community + member)
    """
    schema = BillingSchema()

    # Opus 4 - Member only (ADR-0701)
    schema.add_model_pricing(ModelPricing(
        model_id="claude-opus-4",
        tier=ModelTier.MEMBER,
        pricing_model=ModelPricingModel.PER_TOKEN,
        input_token_cost=Decimal("0.000015"),      # €0.015 per 1K tokens
        output_token_cost=Decimal("0.000045"),
        cache_read_cost=Decimal("0.0000045"),      # 30% discount
        cache_write_cost=Decimal("0.000045"),      # Same as output
        change_date="2026-09-16",
    ))

    # Sonnet 3 - Community + Member
    schema.add_model_pricing(ModelPricing(
        model_id="claude-sonnet-3",
        tier=ModelTier.COMMUNITY,
        pricing_model=ModelPricingModel.PER_TOKEN,
        input_token_cost=Decimal("0.000003"),      # €0.003 per 1K tokens
        output_token_cost=Decimal("0.000015"),
        cache_read_cost=Decimal("0.0000009"),
        cache_write_cost=Decimal("0.000015"),
        change_date="2026-09-16",
    ))

    schema.add_model_pricing(ModelPricing(
        model_id="claude-sonnet-3",
        tier=ModelTier.MEMBER,
        pricing_model=ModelPricingModel.PER_TOKEN,
        input_token_cost=Decimal("0.000003"),      # Same as community (no discount)
        output_token_cost=Decimal("0.000015"),
        cache_read_cost=Decimal("0.0000009"),
        cache_write_cost=Decimal("0.000015"),
        change_date="2026-09-16",
    ))

    # Haiku 4.5 - Community + Member
    schema.add_model_pricing(ModelPricing(
        model_id="claude-haiku-4-5",
        tier=ModelTier.COMMUNITY,
        pricing_model=ModelPricingModel.PER_TOKEN,
        input_token_cost=Decimal("0.0000008"),     # €0.0008 per 1K tokens
        output_token_cost=Decimal("0.000004"),
        cache_read_cost=Decimal("0.00000024"),
        cache_write_cost=Decimal("0.000004"),
        requests_per_day_limit=50,                 # Community limits
        tokens_per_day_limit=100_000,
        change_date="2026-09-16",
    ))

    schema.add_model_pricing(ModelPricing(
        model_id="claude-haiku-4-5",
        tier=ModelTier.MEMBER,
        pricing_model=ModelPricingModel.PER_TOKEN,
        input_token_cost=Decimal("0.0000008"),     # Same as community
        output_token_cost=Decimal("0.000004"),
        cache_read_cost=Decimal("0.00000024"),
        cache_write_cost=Decimal("0.000004"),
        change_date="2026-09-16",
    ))

    return schema
