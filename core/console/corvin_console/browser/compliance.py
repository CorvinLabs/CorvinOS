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

import ipaddress
import logging
import re
import socket
import time
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
    "metadata.google.internal",    # GCP metadata DNS alias
    "metadata",                    # short-form alias used by some SDKs
})

# IP-level metadata targets. Matched against the host PARSED to a canonical IP
# (review HIGH-2), so alternate encodings of 169.254.169.254 — decimal
# (2852039166), hex (0xa9fea9fe), octal (0251.0376.0251.0376), a trailing dot,
# or an IPv4-mapped IPv6 ([::ffff:169.254.169.254]) — cannot slip past a bare
# string compare the way the old exact-match set allowed.
_METADATA_V4_NETS = (ipaddress.ip_network("169.254.0.0/16"),)  # link-local, incl IMDS
_METADATA_V4_HOSTS = frozenset({ipaddress.ip_address("100.100.100.200")})  # Alibaba ECS
_METADATA_V6_HOSTS = frozenset({
    ipaddress.ip_address("fd00:ec2::254"),   # AWS IMDSv2 (IPv6)
})


def _parse_host_ip(host: str):
    """Best-effort: canonicalize an IP-literal host (in ANY textual encoding) to
    an ``ipaddress`` object, else None for a real DNS name. Handles dotted v4/v6,
    a single decimal/hex/octal integer, dotted octal/hex octets, brackets, and a
    trailing dot. Returns None (not an error) for anything that isn't an IP."""
    h = (host or "").strip().rstrip(".")
    if not h:
        return None
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        return ipaddress.ip_address(h)                 # dotted v4 / v6 literal
    except ValueError:
        pass
    try:
        val = int(h, 0)                                # 2852039166 / 0xa9fea9fe / 0o...
        if 0 <= val <= 0xFFFFFFFF:
            return ipaddress.ip_address(val)
    except ValueError:
        pass
    parts = h.split(".")                               # 0251.0376.0251.0376 etc.
    if len(parts) == 4:
        try:
            octets = [_octet(p) for p in parts]
            if all(0 <= o <= 255 for o in octets):
                return ipaddress.ip_address(".".join(str(o) for o in octets))
        except ValueError:
            pass
    return None


def _octet(p: str) -> int:
    """Parse one IPv4 octet honoring legacy hex (0x..) and octal (leading-0)
    encodings — ``int(p, 0)`` rejects bare-leading-zero octal in Python 3, which
    is exactly the SSRF-obfuscation form ``0251.0376.0251.0376`` relies on."""
    p = p.strip().lower()
    if p.startswith("0x"):
        return int(p, 16)
    if len(p) > 1 and p.startswith("0"):
        return int(p, 8)
    return int(p)


def _is_cloud_metadata_ip(ip) -> bool:
    """True if a resolved/parsed ``ipaddress`` object is a cloud-metadata target.
    Split out from ``_is_cloud_metadata`` so the DNS-rebind guard below can reuse
    it on RESOLVED addresses, not just IP-literal hosts (BR-F2)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped                            # unwrap ::ffff:169.254.169.254
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in _METADATA_V4_HOSTS or any(ip in net for net in _METADATA_V4_NETS)
    return ip in _METADATA_V6_HOSTS


def _is_cloud_metadata(host: str) -> bool:
    if host in _METADATA_HOSTS:
        return True
    ip = _parse_host_ip(host)
    if ip is None:
        return False
    return _is_cloud_metadata_ip(ip)


# ── BR-F2: private/link-local SSRF + DNS-rebind guard ────────────────────────
# Besides the cloud-metadata endpoints above, a browser agent in the DEFAULT
# no-allowlist mode must not be steerable — by an injected link, a redirect, or a
# DNS-rebind — into the operator's PRIVATE network: RFC-1918 (10/8, 172.16/12,
# 192.168/16), IPv4 link-local (169.254/16, which also carries the IMDS IP),
# IPv6 ULA (fc00::/7) and IPv6 link-local (fe80::/10), plus reserved/unspecified.
# These are blocked by default and reachable ONLY when an explicit allowlist
# names the specific host.
#
# LOOPBACK (127.0.0.0/8, ::1) is deliberately NOT in this default block: it is
# the operator's OWN machine (local dev servers — the single most common
# automation target), it stays governed by the normal allowlist policy exactly as
# the long-standing carve-out on _METADATA_HOSTS already documents, and an
# operator who wants it blocked names it on the tenant `forbidden_hosts` list.
# (Blocking loopback by default would additionally break every same-host request
# a page makes to itself, since a page and its own subresources share a host.)
_RESOLVE_CACHE: dict[str, tuple[float, tuple]] = {}
_RESOLVE_TTL = 30.0   # seconds — bounds per-host getaddrinfo cost on the request hot path


def _resolve_host_ips(host: str) -> tuple:
    """Best-effort DNS resolution of ``host`` to a tuple of ``ipaddress`` objects,
    memoized with a short TTL so the per-request egress gate does not re-resolve
    the same host on every subresource. NEVER raises: an unresolvable host (or an
    offline CI) yields an empty tuple — a host that does not resolve cannot reach
    a private target anyway, and every IP-LITERAL private range is blocked below
    WITHOUT any lookup, so this stays fail-safe rather than fail-open onto a
    known-bad literal."""
    now = time.monotonic()
    hit = _RESOLVE_CACHE.get(host)
    if hit is not None and hit[0] > now:
        return hit[1]
    ips: list = []
    try:
        for *_meta, sockaddr in socket.getaddrinfo(host, None):
            try:
                ips.append(ipaddress.ip_address(sockaddr[0]))
            except ValueError:
                continue
    except Exception:  # noqa: BLE001 — offline / NXDOMAIN / timeout: treat as unresolved
        ips = []
    out = tuple(ips)
    _RESOLVE_CACHE[host] = (now + _RESOLVE_TTL, out)
    return out


def _is_private_target_ip(ip) -> bool:
    """True if ``ip`` is in a range the default egress block forbids — every
    non-global range EXCEPT loopback (see the module note above). Unwraps an
    IPv4-mapped IPv6 first so ``::ffff:192.168.0.1`` is judged as its v4 form."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return False
    # ``is_private`` already spans RFC-1918, IPv4 link-local (169.254/16, IMDS),
    # IPv6 ULA (fc00::/7) and IPv6 link-local; keep ``is_link_local`` explicit for
    # clarity. Deliberately NOT ``is_reserved``/``is_multicast``: the NAT64 prefix
    # 64:ff9b::/96 (a public IPv4 tunnelled over v6, used by DNS64 resolvers for
    # ordinary public sites) is is_reserved=True yet globally routable, so blocking
    # it would break legitimate public browsing on v6-only / NAT64 networks.
    return bool(ip.is_private or ip.is_link_local or ip.is_unspecified)


def _private_block_reason(host: str) -> str | None:
    """Return a block reason if ``host`` IS, or RESOLVES to, a forbidden private/
    link-local target (SSRF / DNS-rebind guard); else None. An IP-literal host is
    judged with no DNS lookup (keeps the existing canonicalization); a DNS name is
    resolved best-effort and blocked if ANY resolved address lands in a forbidden
    range OR a cloud-metadata range — closing the DNS-rebind-to-IMDS gap where a
    public-looking name resolves to 169.254.169.254."""
    lit = _parse_host_ip(host)
    if lit is not None:
        return ("private/link-local address blocked (SSRF guard)"
                if _is_private_target_ip(lit) else None)
    for rip in _resolve_host_ips(host):
        if _is_private_target_ip(rip) or _is_cloud_metadata_ip(rip):
            return "host resolves to a private/link-local address (SSRF/rebind guard)"
    return None


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

    # BR-F2 SSRF / DNS-rebind guard: a private/link-local target (or a DNS name
    # that RESOLVES into one) is blocked UNLESS an explicit allowlist names this
    # host. Ordering: an allowlist match wins (an operator can opt a specific LAN
    # host in), but the private block runs BEFORE the deny-by-default verdict so a
    # rebind host is rejected with a precise reason rather than a generic miss —
    # and, crucially, it applies in the no-allowlist mode where nothing else would.
    allowlisted = allowlist is not None and _match(allowlist)
    if not allowlisted:
        reason = _private_block_reason(host)
        if reason:
            return EgressDecision(False, host, reason)

    # allowlist is not None → explicit policy set (even [] means deny-all).
    # allowlist is None    → no policy configured → allow (still audited).
    if allowlist is not None:
        return (EgressDecision(True, host, "host on allowlist") if allowlisted
                else EgressDecision(False, host, "host not on the egress allowlist (deny-by-default)"))
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


# Keys whose VALUE could be user-typed content / a secret and must never enter
# the metadata-only audit trail (review LOW-2 — broadened from the original
# 5-key list; still a denylist because the permitted metadata key set is open).
_AUDIT_VALUE_KEYS = frozenset({
    "value", "text", "secret", "password", "content", "query", "email",
    "token", "credential", "card", "cvv", "cvc", "ssn", "otp", "pin",
})


def _scrub_extra(extra: dict) -> dict:
    out = {}
    for k, v in extra.items():
        if k.lower() in _AUDIT_VALUE_KEYS:
            continue
        out[k] = _scrub_extra(v) if isinstance(v, dict) else v
    return out


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
        # Defensive scrub (review LOW-2): never let a caller smuggle a field value
        # into the audit trail. Broadened denylist + one level of recursion into
        # nested dicts, so a value tucked under a common alias or a sub-dict is
        # still dropped. Defense-in-depth only — no current call site does this.
        details.update(_scrub_extra(extra))
    try:
        if audit_fn is not None:
            audit_fn(tenant_id=tenant_id, event="browser.action", details=details)
        else:
            logger.debug("[browser-audit] %s", details)
    except Exception:  # noqa: BLE001 — audit must never break the action
        logger.debug("[browser-audit] emit failed for %s", action)
