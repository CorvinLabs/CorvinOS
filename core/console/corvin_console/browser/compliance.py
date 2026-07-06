"""Browser-automation compliance gates (ADR-0182 §Compliance wiring).

The browser is a large action surface, so these gates are load-bearing and
fail-closed where it matters:

  * ``check_egress`` — a navigation target host must pass the tenant L35 egress
    policy (allowlist / forbidden hosts). Fail-closed: a broken/absent gate on an
    explicit forbidden host still blocks.
  * ``is_sensitive`` — clicks/submits that spend money, send messages, delete,
    or log in are flagged so the runtime can require explicit human confirmation
    before executing (human-in-the-loop).
  * ``audit_action`` — every action emits METADATA ONLY: host, action, element
    role/index. NEVER field values, passwords, or page content (mirrors the
    L16 audit red-line).

All values typed into the page are treated as sensitive: they are never logged,
never returned to the audit trail, and `fill_secret` resolves a vault key without
the value ever entering the model context.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger("corvin.browser.compliance")

# Cloud instance-metadata endpoints — the classic SSRF exfiltration target
# (IAM credentials). Blocked UNCONDITIONALLY, even if a tenant allowlist
# explicitly names one: there is no legitimate browser-automation task that
# needs them, unlike a plain RFC-1918/loopback address (local dev servers,
# home-network admin panels), which stays governed by the normal
# allowlist/no-allowlist-configured policy below.
_METADATA_HOSTS = frozenset({
    "169.254.169.254",             # AWS / Azure / GCP IMDS (IPv4)
    "metadata.google.internal",    # GCP metadata DNS alias
    "metadata",                    # short-form alias used by some SDKs
    "fd00:ec2::254",                # AWS IMDSv2 (IPv6)
})


def _is_cloud_metadata(host: str) -> bool:
    return host in _METADATA_HOSTS


# Actions whose element name/role suggests an irreversible or outward-facing
# effect → require explicit user confirmation before executing.
_SENSITIVE_NAME = re.compile(
    r"\b(buy|purchase|pay|checkout|order|subscribe|donate|"
    r"send|submit|post|publish|tweet|"
    r"delete|remove|destroy|deactivate|close account|cancel subscription|"
    r"log ?in|sign ?in|log ?out|sign ?out|authorize|confirm|transfer|withdraw)\b",
    re.IGNORECASE,
)
_SENSITIVE_ROLE = {"button", "link"}

# Sensitivity model v2 (ADR-0183 S1): a click/submit on a page whose CURRENT
# path looks like checkout/payment/delete/security-settings/billing is
# sensitive even when the element's own accessible name is ambiguous ("Continue",
# "OK", icon-only). Path-only substring match — the query string (which may
# carry tokens) is never inspected or logged.
_SENSITIVE_URL_PATH = re.compile(
    r"(/checkout|/payment|/delete|/settings/security|/billing)", re.IGNORECASE,
)


@dataclass
class EgressDecision:
    allowed: bool
    host: str
    reason: str


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def check_egress(
    url: str,
    *,
    allowlist: list[str] | None,
    forbidden: list[str] | None,
) -> EgressDecision:
    """Validate a navigation target against the tenant egress policy.

    Semantics (mirrors L35):
      * a host on ``forbidden`` is ALWAYS denied (fail-closed, wins over allow);
      * if an ``allowlist`` is set, the host MUST be on it (deny-by-default);
      * no allowlist → allow, but the navigation is still audited.
    Host matching is suffix-aware: ``example.com`` matches ``www.example.com``.
    """
    host = _host(url)
    if not host:
        return EgressDecision(False, host, "unparseable or non-http url")

    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        return EgressDecision(False, host, f"scheme '{scheme}' not allowed")

    if _is_cloud_metadata(host):
        return EgressDecision(False, host, "cloud metadata endpoint blocked (SSRF guard)")

    def _match(patterns: list[str]) -> bool:
        for p in patterns:
            p = p.strip().lower().lstrip("*.")
            if not p:
                continue
            if host == p or host.endswith("." + p):
                return True
        return False

    if forbidden and _match(forbidden):
        return EgressDecision(False, host, "host is on the forbidden list")
    # allowlist is not None → explicit policy set (even [] means deny-all).
    # allowlist is None    → no policy configured → allow (still audited).
    if allowlist is not None:
        if _match(allowlist):
            return EgressDecision(True, host, "host on allowlist")
        return EgressDecision(False, host, "host not on the egress allowlist (deny-by-default)")
    return EgressDecision(True, host, "no allowlist configured")


def is_sensitive(
    action: str, *, role: str = "", name: str = "",
    url: str = "", form_has_sensitive_field: bool = False,
) -> bool:
    """True when an action should require explicit human confirmation.

    Fill actions are never auto-sensitive (typing is reversible); it is the
    *click/submit* that commits. A click on an element whose accessible name
    matches a money / identity / destructive verb → sensitive (v1 signal).

    Sensitivity model v2 (ADR-0183 S1) adds two ADDITIONAL, additive signals —
    both still gated to click/submit, never fill:
      * ``url`` — the current page path matches a known sensitive route
        (/checkout, /payment, /delete, /settings/security, /billing).
      * ``form_has_sensitive_field`` — caller-supplied hint: the enclosing
        <form> of the clicked element contains a password or card-number
        field, so ANY click/submit in that form is sensitive regardless of
        the button's own label.
    Either v1 or v2 signal firing is sufficient — this only RAISES recall, it
    never suppresses the v1 keyword match.

    Known limitation: an icon-only / generically-labelled control ("Continue",
    "OK") that commits and is on a plain-looking URL with no password/card
    field in its form is still NOT auto-flagged. The egress guard, the audit
    trail, and the user watching the live view remain the backstops.
    """
    if action not in ("click", "submit"):
        return False
    if form_has_sensitive_field:
        return True
    if _SENSITIVE_NAME.search(name or ""):
        return True
    if url and _SENSITIVE_URL_PATH.search(url):
        return True
    return False


def audit_action(
    audit_fn,
    *,
    tenant_id: str,
    session_id: str,
    action: str,
    host: str = "",
    role: str = "",
    index: int | None = None,
    ok: bool = True,
    extra: dict | None = None,
) -> None:
    """Emit a METADATA-ONLY audit event. Never receives or logs field values.

    ``audit_fn`` is the injected console audit sink; if None, degrade to a debug
    log line (still metadata-only). A failure here never blocks the action.
    """
    details = {
        "session": session_id,
        "action": action,
        "host": host,
        "element_role": role,
        "element_index": index,
        "ok": ok,
    }
    if extra:
        # defensive: never let a caller smuggle a value/text field into audit
        for k, v in extra.items():
            if k in ("value", "text", "secret", "password", "content"):
                continue
            details[k] = v
    try:
        if audit_fn is not None:
            audit_fn(tenant_id=tenant_id, event="browser.action", details=details)
        else:
            logger.debug("[browser-audit] %s", details)
    except Exception:  # noqa: BLE001 — audit must never break the action
        logger.debug("[browser-audit] emit failed for %s", action)
