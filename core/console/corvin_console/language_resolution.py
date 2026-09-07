"""Language resolution for web chat turns (Phase 4 integration).

Resolves the language for each turn using LanguageResolver.
Stores language_context in turn metadata for downstream use:
  - Voice Summary: speaks correct language
  - Output Translation: converts to nutzer's language
  - Audit: logs language decision
"""

from __future__ import annotations

import logging
from typing import Optional

from core.language import LanguageResolver, LanguageContext

_log = logging.getLogger("console.language_resolution")


def resolve_turn_language(
    user_text: str,
    response_text: str = "",
    profile_lang: Optional[str] = None,
    system_default: str = "en",
) -> LanguageContext:
    """Resolve language for this turn using ADR-0650 priority.

    Priority:
      1. Profile Setting (display_language) — CANONICAL
      2. Input Language (detected from user_text)
      3. Response Language (detected from response_text)
      4. System Default (usually "en")

    Args:
        user_text: User's input text
        response_text: Claude's response text (optional, for fallback)
        profile_lang: User's display_language setting (from Settings → Profile)
        system_default: Fallback language when all else fails

    Returns:
        LanguageContext with resolved language and metadata
    """
    resolver = LanguageResolver(
        profile_lang=profile_lang,
        system_default=system_default
    )

    # Resolve: Profile > Input > Response > System Default
    lang_ctx = resolver.resolve(
        user_text=user_text,
        response_text=response_text
    )

    _log.info(
        "Language resolved",
        extra={
            "resolved_lang": lang_ctx.resolved_lang,
            "source": lang_ctx.source.value,
            "confidence": lang_ctx.confidence,
            "profile_set": lang_ctx.profile_set,
        }
    )

    return lang_ctx


def load_profile_language(tenant_id: str) -> Optional[str]:
    """Load display_language from user's profile (best-effort).

    This mirrors the bridge adapter's profile loading, so console web-chat
    and Discord/WhatsApp pipelines resolve the same language.

    Args:
        tenant_id: Tenant context (for future multi-tenant support)

    Returns:
        BCP-47 language code (e.g., "de", "en", "zh-Hans") or None if not set
    """
    try:
        # Import the SAME profile resolver bridges use
        import sys
        from pathlib import Path
        repo_root = Path(__file__).parent.parent.parent.parent
        bridges_shared = repo_root / "operator" / "bridges" / "shared"
        if str(bridges_shared) not in sys.path:
            sys.path.insert(0, str(bridges_shared))

        import profile as profile_mod  # type: ignore

        prof = profile_mod.load()  # XDG-aware, same resolver as console/routes/profile.py
        if prof and isinstance(prof, dict):
            lang = prof.get("display_language")
            if lang and isinstance(lang, str):
                return lang.strip() or None
    except Exception as e:
        _log.debug(f"Failed to load profile language: {e}")

    return None


def store_language_context_in_metadata(
    turn_metadata: dict,
    lang_ctx: LanguageContext,
) -> None:
    """Store language_context in turn metadata for downstream use.

    Called after stream_turn() completes, before storing turn to history.
    Enables Summary Generator, Output Translator, and Audit to access it.

    Args:
        turn_metadata: Dict to update (mutable)
        lang_ctx: LanguageContext from resolve_turn_language()
    """
    turn_metadata["language_context"] = {
        "resolved_lang": lang_ctx.resolved_lang,
        "source": lang_ctx.source.value,
        "confidence": lang_ctx.confidence,
        "profile_set": lang_ctx.profile_set,
    }
    _log.debug(f"Language context stored: {lang_ctx}")
