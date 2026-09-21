"""Token Manager — Generate, Validate, and Cache Dispatcher Tokens (M1)

Manages lifecycle of dispatcher tokens:
- Generate unique tokens for renderers
- Validate token integrity and expiration
- Cache tokens for performance
- Track token usage metrics

ADR-0742: 3-Tier Animation Architecture (Phase 5)
"""

from typing import Optional, Dict, Set
from uuid import uuid4
from datetime import datetime, timedelta
import hashlib
import logging

from .dispatcher_token_schema import (
    DispatcherToken,
    RendererTokenMetadata,
    RendererTier,
    RendererCapability,
    TokenValidationError,
    TokenExpiredError,
    TokenChecksumError,
    generate_token_checksum,
    encode_token_string,
    decode_token_string,
)

logger = logging.getLogger(__name__)


class TokenCache:
    """Simple in-memory cache for tokens.

    Caches generated tokens to avoid regeneration overhead.
    Thread-safe with basic TTL support.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """Initialize token cache.

        Args:
            max_size: Maximum number of cached tokens
            ttl_seconds: Time-to-live for cached entries
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple] = {}  # {token_string: (token, timestamp)}
        self._metrics = {"hits": 0, "misses": 0, "evictions": 0}

    def get(self, token_string: str) -> Optional[DispatcherToken]:
        """Retrieve token from cache.

        Args:
            token_string: Token string to look up

        Returns:
            DispatcherToken if found and not expired, None otherwise
        """
        if token_string not in self._cache:
            self._metrics["misses"] += 1
            return None

        token, timestamp = self._cache[token_string]

        # Check TTL
        age_seconds = (datetime.utcnow() - timestamp).total_seconds()
        if age_seconds > self.ttl_seconds:
            del self._cache[token_string]
            self._metrics["misses"] += 1
            return None

        self._metrics["hits"] += 1
        return token

    def set(self, token_string: str, token: DispatcherToken) -> None:
        """Store token in cache.

        Args:
            token_string: Token string (key)
            token: DispatcherToken to cache
        """
        if len(self._cache) >= self.max_size:
            # Evict oldest entry
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            self._metrics["evictions"] += 1

        self._cache[token_string] = (token, datetime.utcnow())

    def clear(self) -> None:
        """Clear all cached tokens."""
        self._cache.clear()

    def get_metrics(self) -> dict:
        """Get cache performance metrics."""
        total_requests = self._metrics["hits"] + self._metrics["misses"]
        hit_rate = (self._metrics["hits"] / total_requests * 100) if total_requests > 0 else 0.0

        return {
            "cache_size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._metrics["hits"],
            "misses": self._metrics["misses"],
            "evictions": self._metrics["evictions"],
            "hit_rate": hit_rate,
        }


class TokenManager:
    """Generate, validate, and manage dispatcher tokens.

    Responsibilities:
    - Generate unique tokens for new renderers
    - Validate token integrity and expiration
    - Cache tokens for performance
    - Track token usage and lifecycle
    - Audit token operations
    """

    def __init__(self, cache_enabled: bool = True, cache_ttl_seconds: int = 3600):
        """Initialize token manager.

        Args:
            cache_enabled: Enable token caching
            cache_ttl_seconds: Cache TTL in seconds
        """
        self.cache_enabled = cache_enabled
        self.cache = TokenCache(ttl_seconds=cache_ttl_seconds) if cache_enabled else None

        self._generated_tokens: Dict[str, DispatcherToken] = {}  # Audit trail
        self._validation_results: list = []  # Validation audit trail

        logger.info("TokenManager initialized (cache=%s)", cache_enabled)

    def generate_token(
        self,
        renderer_id: str,
        version: str,
        tier: RendererTier,
        capabilities: Optional[Set[RendererCapability]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> DispatcherToken:
        """Generate a new dispatcher token for a renderer.

        Args:
            renderer_id: Unique renderer identifier (e.g., "quick_renderer")
            version: Semantic version (e.g., "5.1.0")
            tier: Tier classification
            capabilities: Set of supported capabilities (default: empty set)
            ttl_seconds: Token lifetime in seconds (default: no expiration)

        Returns:
            DispatcherToken with unique token_id and checksum

        Raises:
            ValueError: If renderer_id or version format is invalid
        """
        if not renderer_id or not isinstance(renderer_id, str):
            raise ValueError("renderer_id must be non-empty string")

        if not version or not isinstance(version, str):
            raise ValueError("version must be non-empty string")

        capabilities = capabilities or set()

        # Generate components
        token_id = str(uuid4())
        checksum = generate_token_checksum(renderer_id, version, tier)
        token_string = encode_token_string(renderer_id, version, tier, checksum)

        issued_at = datetime.utcnow()
        expires_at = (issued_at + timedelta(seconds=ttl_seconds)) if ttl_seconds else None

        # Create token
        token = DispatcherToken(
            token_id=token_id,
            renderer_id=renderer_id,
            version=version,
            tier=tier,
            capabilities=capabilities,
            issued_at=issued_at,
            expires_at=expires_at,
            token_string=token_string,
            checksum=checksum,
        )

        # Store in audit trail
        self._generated_tokens[token_string] = token

        # Cache if enabled
        if self.cache_enabled:
            self.cache.set(token_string, token)

        logger.info(
            "Generated token: renderer_id=%s, version=%s, tier=%s, token=%s",
            renderer_id, version, tier.value, token_string
        )

        return token

    def validate_token(self, token: DispatcherToken) -> bool:
        """Validate token integrity and expiration.

        Args:
            token: DispatcherToken to validate

        Returns:
            True if token is valid

        Raises:
            TokenExpiredError: If token has expired
            TokenChecksumError: If checksum doesn't match
            TokenValidationError: For other validation failures
        """
        # Check expiration
        if not token.is_valid():
            error_msg = f"Token expired: {token.token_string}"
            logger.warning(error_msg)
            self._validation_results.append({"token_string": token.token_string, "valid": False, "reason": "expired"})
            raise TokenExpiredError(error_msg)

        # Recalculate checksum
        expected_checksum = generate_token_checksum(token.renderer_id, token.version, token.tier)
        if token.checksum != expected_checksum:
            error_msg = f"Checksum mismatch: {token.token_string}"
            logger.error(error_msg)
            self._validation_results.append({"token_string": token.token_string, "valid": False, "reason": "checksum_mismatch"})
            raise TokenChecksumError(error_msg)

        # Verify token string format
        try:
            decoded = decode_token_string(token.token_string)
            if decoded["renderer_id"] != token.renderer_id:
                raise TokenValidationError(f"Token string mismatch: {token.token_string}")
        except Exception as e:
            logger.error("Token validation error: %s", e)
            self._validation_results.append({"token_string": token.token_string, "valid": False, "reason": str(e)})
            raise TokenValidationError(f"Invalid token format: {str(e)}")

        self._validation_results.append({"token_string": token.token_string, "valid": True})
        logger.debug("Token validation passed: %s", token.token_string)

        return True

    def validate_token_string(self, token_string: str) -> bool:
        """Validate token by string (convenience method).

        Args:
            token_string: Token string to validate

        Returns:
            True if token is valid

        Raises:
            TokenValidationError: If token is invalid
        """
        # Check cache first
        if self.cache_enabled:
            cached_token = self.cache.get(token_string)
            if cached_token:
                return self.validate_token(cached_token)

        # Check audit trail
        if token_string in self._generated_tokens:
            return self.validate_token(self._generated_tokens[token_string])

        # Try to decode and validate
        try:
            decoded = decode_token_string(token_string)
            # Note: We can validate format but not expiration without full token object
            logger.debug("Token string decoded successfully: %s", token_string)
            return True
        except Exception as e:
            logger.error("Token string validation failed: %s", e)
            raise TokenValidationError(f"Invalid token string: {str(e)}")

    def get_token_by_renderer_id(self, renderer_id: str) -> Optional[DispatcherToken]:
        """Retrieve most recent token for a renderer.

        Args:
            renderer_id: Renderer identifier to look up

        Returns:
            Most recent DispatcherToken for renderer, None if not found
        """
        matching_tokens = [
            token for token in self._generated_tokens.values()
            if token.renderer_id == renderer_id
        ]

        if not matching_tokens:
            return None

        # Return most recent by issued_at
        return max(matching_tokens, key=lambda t: t.issued_at)

    def get_tokens_by_tier(self, tier: RendererTier) -> list:
        """Get all tokens for a specific tier.

        Args:
            tier: Tier to filter by

        Returns:
            List of DispatcherToken objects for tier
        """
        return [
            token for token in self._generated_tokens.values()
            if token.tier == tier
        ]

    def get_metrics(self) -> dict:
        """Get token manager metrics.

        Returns:
            Dictionary with token usage statistics
        """
        validation_stats = {
            "valid_count": sum(1 for r in self._validation_results if r["valid"]),
            "invalid_count": sum(1 for r in self._validation_results if not r["valid"]),
        }

        result = {
            "total_generated_tokens": len(self._generated_tokens),
            "validation_stats": validation_stats,
        }

        if self.cache_enabled:
            result["cache_metrics"] = self.cache.get_metrics()

        return result

    def get_validation_audit_trail(self) -> list:
        """Get validation audit trail.

        Returns:
            List of validation results for debugging/compliance
        """
        return self._validation_results.copy()

    def clear_audit_trail(self) -> None:
        """Clear audit trail (for testing only)."""
        self._validation_results.clear()
        self._generated_tokens.clear()
        if self.cache_enabled:
            self.cache.clear()
        logger.info("Audit trail cleared")
