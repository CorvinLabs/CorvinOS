"""Forge intake (ADR-2254): one fail-closed analysis stage for everything attached to a Forge.

``analyze_item`` runs the stages in order — admit, sniff, scan for secrets, project (Layer 24 snapshot, PII detection,
redaction), classify (the caller's gate on the projection the engine will actually read) — and returns an ``ItemReport``
plus, for an admitted item, the bounded projection. ``build_pack`` assembles the only thing a generator receives.
"""
from .intake import (  # noqa: F401
    ALLOWED_EXTENSIONS, MAX_ITEM_BYTES, MAX_ITEMS_PER_REQUEST, PACK_MAX_BYTES,
    AnalyzedItem, ItemReport, analyze_item, build_pack,
)
