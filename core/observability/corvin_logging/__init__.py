"""Structured logging for CorvinOS (ADR-0231 Stage 1).

NOT named ``logging``: a package with that name shadows the standard library as
soon as its parent is on ``sys.path``, which is the failure that once broke
``corvin-webui.service`` through an ``operator`` package. The design sketch's
``core/logging/`` path is deliberately not used.
"""
from __future__ import annotations

from .context import (
    current,
    get_correlation_id,
    get_tenant_id,
    new_correlation_id,
    request_context,
    set_correlation_id,
)
from .scrubber import contains_pii, scrub, scrub_text
from .structured_logger import (
    CorvinLogger,
    JsonFormatter,
    get_logger,
    install_json_handler,
)

__all__ = [
    "CorvinLogger",
    "JsonFormatter",
    "contains_pii",
    "current",
    "get_correlation_id",
    "get_logger",
    "get_tenant_id",
    "install_json_handler",
    "new_correlation_id",
    "request_context",
    "scrub",
    "scrub_text",
    "set_correlation_id",
]
