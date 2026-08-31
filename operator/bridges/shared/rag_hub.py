"""RAG Hub — Provider Marketplace Backend.

Allows operators to publish provider metadata (no credentials) so others can discover and import.
GDPR compliance: metadata-only storage, no sensitive information, all actions audited via L16.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────

@dataclass
class RAGHubProvider:
    """Provider metadata for the Hub (no credentials)."""

    id: str                      # Unique provider ID
    name: str                    # Display name
    description: str             # One-liner description
    author: str                  # Who published it
    version: str                 # Provider version
    data_classification: str     # PUBLIC, INTERNAL, CONFIDENTIAL, SECRET
    compliance_zone: str         # EU, US, APAC, HYBRID

    capabilities: list[str]      # keyword-search, semantic-search, etc.

    # Manifest metadata (no actual manifest stored)
    manifest_hash: str           # SHA256 of original manifest (for dedup)
    manifest_download_url: Optional[str] = None  # User-provided URL or None

    # Rating & feedback
    rating: float = 0.0          # 0.0-5.0, average of reviews
    review_count: int = 0
    download_count: int = 0

    # Timestamps
    published_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Search ranking
    trending_score: float = 0.0  # Computed weekly
    relevance_boost: float = 1.0 # For search ranking

    def to_dict(self) -> dict:
        """Export as dict for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "data_classification": self.data_classification,
            "compliance_zone": self.compliance_zone,
            "capabilities": self.capabilities,
            "rating": round(self.rating, 1),
            "review_count": self.review_count,
            "download_count": self.download_count,
            "published_at": self.published_at,
            "trending_score": round(self.trending_score, 2),
        }


@dataclass
class RAGHubReview:
    """User review of a provider."""

    provider_id: str
    author: str
    rating: int  # 1-5
    text: str    # Brief comment, max 500 chars
    created_at: float = field(default_factory=time.time)
    helpful_count: int = 0

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "author": self.author,
            "rating": self.rating,
            "text": self.text,
            "created_at": self.created_at,
            "helpful_count": self.helpful_count,
        }


# ── Hub Storage ────────────────────────────────────────────────

class RAGHub:
    """Marketplace backend for RAG providers."""

    def __init__(self, hub_dir: Path | str):
        """Initialize Hub storage."""
        self.hub_dir = Path(hub_dir)
        self.providers_file = self.hub_dir / "hub_providers.jsonl"
        self.reviews_file = self.hub_dir / "hub_reviews.jsonl"

        self.hub_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache (reload on startup)
        self.providers: dict[str, RAGHubProvider] = {}
        self.reviews: dict[str, list[RAGHubReview]] = {}

        self._load()

    def _load(self):
        """Load providers and reviews from disk."""
        if self.providers_file.exists():
            with open(self.providers_file, "r") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        self.providers[data["id"]] = RAGHubProvider(**data)

        if self.reviews_file.exists():
            with open(self.reviews_file, "r") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        review = RAGHubReview(**data)
                        if review.provider_id not in self.reviews:
                            self.reviews[review.provider_id] = []
                        self.reviews[review.provider_id].append(review)

    def _save_provider(self, provider: RAGHubProvider):
        """Append provider to disk."""
        with open(self.providers_file, "a") as f:
            f.write(json.dumps({
                "id": provider.id,
                "name": provider.name,
                "description": provider.description,
                "author": provider.author,
                "version": provider.version,
                "data_classification": provider.data_classification,
                "compliance_zone": provider.compliance_zone,
                "capabilities": provider.capabilities,
                "manifest_hash": provider.manifest_hash,
                "manifest_download_url": provider.manifest_download_url,
                "rating": provider.rating,
                "review_count": provider.review_count,
                "download_count": provider.download_count,
                "published_at": provider.published_at,
                "updated_at": provider.updated_at,
                "trending_score": provider.trending_score,
                "relevance_boost": provider.relevance_boost,
            }) + "\n")

    def _save_review(self, review: RAGHubReview):
        """Append review to disk."""
        with open(self.reviews_file, "a") as f:
            f.write(json.dumps(review.to_dict()) + "\n")

    # ── CRUD Operations ────────────────────────────────────────

    def publish(self, provider: RAGHubProvider) -> RAGHubProvider:
        """Publish a new provider to the Hub."""
        if provider.id in self.providers:
            raise ValueError(f"Provider '{provider.id}' already published")

        self.providers[provider.id] = provider
        self._save_provider(provider)
        logger.info(f"Published provider: {provider.id}")
        return provider

    def update(self, provider_id: str, **updates) -> RAGHubProvider:
        """Update provider metadata."""
        if provider_id not in self.providers:
            raise ValueError(f"Provider '{provider_id}' not found")

        provider = self.providers[provider_id]
        for key, value in updates.items():
            if hasattr(provider, key):
                setattr(provider, key, value)

        provider.updated_at = time.time()
        self._save_provider(provider)
        logger.info(f"Updated provider: {provider_id}")
        return provider

    def get(self, provider_id: str) -> Optional[RAGHubProvider]:
        """Get provider by ID."""
        return self.providers.get(provider_id)

    def list(self, limit: int = 100, offset: int = 0) -> list[RAGHubProvider]:
        """List all providers (paginated)."""
        providers = sorted(
            self.providers.values(),
            key=lambda p: (p.trending_score, p.rating, p.download_count),
            reverse=True
        )
        return providers[offset : offset + limit]

    def search(self, query: str, limit: int = 20) -> list[RAGHubProvider]:
        """Search providers by name/description."""
        query_lower = query.lower()
        results = []

        for provider in self.providers.values():
            score = 0.0

            # Exact match on ID
            if provider.id.lower() == query_lower:
                score = 1000.0

            # Match on name
            if query_lower in provider.name.lower():
                score += 100.0

            # Match on description
            if query_lower in provider.description.lower():
                score += 50.0

            # Match on capabilities
            if any(query_lower in cap.lower() for cap in provider.capabilities):
                score += 75.0

            # Weight by popularity
            score *= (1.0 + provider.download_count / 1000.0)

            if score > 0:
                results.append((score, provider))

        # Sort by relevance, return top N
        results.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in results[:limit]]

    def filter_by_zone(self, zone: str) -> list[RAGHubProvider]:
        """Get providers in a specific compliance zone."""
        return [p for p in self.providers.values() if p.compliance_zone == zone]

    def filter_by_classification(self, classification: str) -> list[RAGHubProvider]:
        """Get providers with a specific data classification."""
        return [p for p in self.providers.values() if p.data_classification == classification]

    # ── Review Operations ──────────────────────────────────────

    def add_review(self, provider_id: str, review: RAGHubReview) -> RAGHubReview:
        """Add a review to a provider."""
        if provider_id not in self.providers:
            raise ValueError(f"Provider '{provider_id}' not found")

        if provider_id not in self.reviews:
            self.reviews[provider_id] = []

        self.reviews[provider_id].append(review)
        self._save_review(review)

        # Update provider rating (average)
        provider = self.providers[provider_id]
        all_ratings = [r.rating for r in self.reviews[provider_id]]
        provider.rating = sum(all_ratings) / len(all_ratings)
        provider.review_count = len(all_ratings)
        self._save_provider(provider)

        logger.info(f"Added review to provider: {provider_id}")
        return review

    def get_reviews(self, provider_id: str) -> list[RAGHubReview]:
        """Get reviews for a provider."""
        return self.reviews.get(provider_id, [])

    # ── Analytics ──────────────────────────────────────────────

    def increment_download_count(self, provider_id: str):
        """Track a download."""
        if provider_id in self.providers:
            provider = self.providers[provider_id]
            provider.download_count += 1
            self._save_provider(provider)

    def compute_trending(self):
        """Compute trending score for all providers."""
        # Simple heuristic: recent downloads + high ratings
        current_time = time.time()
        week_in_seconds = 7 * 24 * 3600

        for provider in self.providers.values():
            days_old = (current_time - provider.published_at) / 86400
            recency_factor = 1.0 / (1.0 + days_old / 7)

            rating_factor = provider.rating / 5.0  # 0.0-1.0
            download_factor = min(provider.download_count / 100.0, 1.0)  # 0.0-1.0

            provider.trending_score = (
                recency_factor * 0.3 +
                rating_factor * 0.3 +
                download_factor * 0.4
            )
            self._save_provider(provider)

    def get_trending(self, limit: int = 10) -> list[RAGHubProvider]:
        """Get trending providers."""
        self.compute_trending()
        return sorted(
            self.providers.values(),
            key=lambda p: p.trending_score,
            reverse=True
        )[:limit]

    def get_top_rated(self, limit: int = 10) -> list[RAGHubProvider]:
        """Get highest-rated providers."""
        return sorted(
            self.providers.values(),
            key=lambda p: (p.rating, p.review_count),
            reverse=True
        )[:limit]

    def get_most_downloaded(self, limit: int = 10) -> list[RAGHubProvider]:
        """Get most-downloaded providers."""
        return sorted(
            self.providers.values(),
            key=lambda p: p.download_count,
            reverse=True
        )[:limit]
