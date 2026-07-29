"""Tests for the token-stripping/length validation shared by Discord and
Telegram token request models (routes/bridges.py).

Adversarial review (2026-07-30) found the length constraint was checked on
the RAW (pre-strip) input, backwards from what it's meant to guarantee: a
whitespace-only string could pass min_length on its raw length and then
strip down to an empty token; a real token padded with spaces could strip
to fewer than min_length characters. Neither was a security hole (a bad
token still fails cleanly against the provider's API), but the length
guarantee didn't hold for the actual token content. Fixed by re-checking
length AFTER stripping, inside the validator itself.
"""
import pytest
from pydantic import ValidationError

from corvin_console.routes.bridges import (
    SaveTelegramTokenRequest,
    SaveTokenRequest,
    ValidateTelegramTokenRequest,
    ValidateTokenRequest,
)


@pytest.mark.parametrize("model,min_len", [
    (ValidateTokenRequest, 20), (SaveTokenRequest, 20),
    (ValidateTelegramTokenRequest, 10), (SaveTelegramTokenRequest, 10),
])
class TestTokenStripAndLength:
    def test_whitespace_only_is_rejected(self, model, min_len):
        """A whitespace-only string must not pass length validation just
        because its RAW length happened to satisfy min_length — it strips
        to an empty token, which is never valid."""
        with pytest.raises(ValidationError):
            model(token=" " * (min_len + 5))

    def test_real_token_shorter_than_min_after_strip_is_rejected(self, model, min_len):
        """A real token one character SHORTER than this model's minimum,
        padded with whitespace to look long enough raw, must be rejected —
        the min_length guarantee is about the ACTUAL token, not the
        whitespace-padded input."""
        short_token = "a" * (min_len - 1)
        with pytest.raises(ValidationError):
            model(token=f"  {short_token}  ")

    def test_legit_padded_token_is_accepted_and_stripped(self, model, min_len):
        """A real, sufficiently long token with incidental whitespace
        (trailing newline from a terminal paste, leading/trailing spaces)
        must be accepted AND stored stripped."""
        real_token = "a" * (min_len + 5)
        m = model(token=f"  {real_token}  \n")
        assert m.token == real_token

    def test_exact_boundary_after_strip_is_accepted(self, model, min_len):
        """Exactly this model's minimum length after stripping must be
        accepted, not off-by-one rejected."""
        m = model(token="  " + "a" * min_len + "  ")
        assert len(m.token) == min_len
