"""Layer 39 social content sanitizer — NFKC-first, cap, framing injection defense.

FIX-1/FIX-2: sanitize_post_content() now returns RAW sanitized content (no framing).
Framing is separated into frame_for_llm() which uses a caller-local (not persisted)
fence token. This prevents stored framing tokens from being leaked and exploited.

FIX-3: sanitize_content_warning() now also rejects </social_post framing delimiters.
"""

import html
import re
import secrets
import unicodedata

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,49}$")

# _SESSION_FENCE_TOKEN has been intentionally removed (FIX-1/FIX-2).
# The fence token must be caller-local and never persisted.


class InjectionAttempt(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def sanitize_raw(content: str) -> str:
    """NFKC normalize, truncate to 2000 chars, strip control chars, and check
    for framing injection.  Returns the cleaned content string.

    This is the core sanitization step — it does NOT add any framing.
    Raises InjectionAttempt if the content contains a framing delimiter.
    """
    content = unicodedata.normalize("NFKC", content)
    content = content[:2000]
    content = _CONTROL_CHAR_RE.sub("", content)
    if "</social_post" in content.lower():
        raise InjectionAttempt("framing delimiter in post content")
    return content


def sanitize_post_content(content: str, actor_id: str, post_id: str) -> str:
    """Sanitize post content and return the RAW (unframed) sanitized string.

    CHANGED in FIX-1/FIX-2: this function no longer wraps content in an
    LLM framing block. The returned string is suitable for storage in the DB.
    Call frame_for_llm() when presenting content to an LLM context.

    Raises InjectionAttempt if the content contains a framing delimiter.
    The actor_id and post_id parameters are retained for backward-compatible
    signature; they are not used in the returned string.
    """
    return sanitize_raw(content)


def frame_for_llm(
    content: str,
    actor_id: str,
    post_id: str,
    fence_token: str | None = None,
) -> str:
    """Wrap sanitized content in a per-call LLM framing block.

    The fence_token is NOT persisted — it is caller-local.  Each call that
    presents posts to an LLM context should generate a fresh token (or pass
    one from the outer framing scope).

    Raises InjectionAttempt if the content contains the closing framing tag
    (both generic </social_post prefix and the full token-specific tag).
    """
    if fence_token is None:
        fence_token = secrets.token_hex(4)
    # Belt-and-suspenders: check both generic prefix and token-specific tag.
    if "</social_post" in content.lower():
        raise InjectionAttempt("framing delimiter in content passed to frame_for_llm")
    closing_tag = f"</social_post_{fence_token}"
    if closing_tag.lower() in content.lower():
        raise InjectionAttempt(
            f"token-specific framing delimiter {closing_tag!r} found in content"
        )
    return (
        f"<social_post_{fence_token} actor_id=\"{html.escape(actor_id)}\" "
        f"post_id=\"{html.escape(post_id)}\">"
        f"{content}"
        f"</social_post_{fence_token}>"
    )


def sanitize_content_warning(text: str) -> str:
    """Sanitize a content-warning string.

    FIX-3: also rejects </social_post framing delimiters as a defensive
    measure against future LLM injection paths.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text[:200]
    text = _CONTROL_CHAR_RE.sub("", text)
    if "</social_post" in text.lower():
        raise InjectionAttempt("framing delimiter in content warning")
    return text


def sanitize_display_name(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text[:64]
    return _CONTROL_CHAR_RE.sub("", text)


def sanitize_summary(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text[:500]
    return _CONTROL_CHAR_RE.sub("", text)


def sanitize_tag(tag: str) -> str:
    if not tag or len(tag) > 50 or not _TAG_RE.match(tag):
        raise ValueError(f"invalid tag: {tag!r}")
    return tag


def validate_tags(tags: list[str]) -> list[str]:
    if len(tags) > 10:
        raise ValueError(f"too many tags: {len(tags)} (max 10)")
    return [sanitize_tag(t) for t in tags]
