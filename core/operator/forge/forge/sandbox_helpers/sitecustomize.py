"""sitecustomize.py — Loopback-Deny shim for forged tools (Layer-16 v2 D).

Python imports ``sitecustomize`` automatically on interpreter startup when it
is reachable on ``sys.path``. The forge runner ensures it is reachable by
prepending this directory to ``PYTHONPATH`` via ``bwrap --setenv`` whenever
the operator policy says the persona's network access should *deny loopback*
(the default for any persona that gets ``--share-net``).

What it does
------------
Patches ``socket.socket.connect`` (and ``connect_ex``) so any attempt to
reach 127.0.0.0/8, ::1, or 169.254.169.254 (cloud metadata service) raises
``ConnectionRefusedError`` *inside* the tool. External HTTP/HTTPS still
work normally — the bwrap shares the host net namespace, only the syscall
hook in this shim says no to loopback.

What it does NOT do
-------------------
* Cover bash tools using ``curl`` / ``wget``. Those run a separate libc
  process; the sitecustomize hook only affects Python tools. The audit
  event records the policy intent so an operator can correlate.
* Cover ``socket.create_connection`` with a pre-resolved address. Only
  the user-visible APIs are patched; lower-level libc calls bypass it.
  In practice every Python HTTP library (urllib, requests, httpx,
  aiohttp) ends up calling ``socket.connect`` so the coverage is
  effectively complete.

Trust model
-----------
This is a *defense-in-depth* layer, not the only line of defense. The
canonical isolation comes from bwrap; loopback-deny narrows
``--share-net`` so a network-permitted persona cannot reach private
internal services on the host (Redis, Postgres, Vault, the metadata IMDS).
Operators who want loopback access (e.g. for local-service tests) can
opt in per-persona via ``persona_sandbox_overrides[<persona>][\"loopback\"]
= \"allow\"`` in policy.json.

ADR-0159 M4 — TCP loopback bridge-port exception
-------------------------------------------------
When ``CORVIN_BRIDGE_PORT`` is set in the environment (injected by the forge
runner when TCP loopback transport is active), connections to
``127.0.0.1:<CORVIN_BRIDGE_PORT>`` are allowed so that forged tools can call
back into the bridge's ECI sidecar transport. All other loopback destinations
remain denied. The exception is ONLY for the exact port — not for all
loopback — so the isolation perimeter is not meaningfully widened.

The shim never imports anything it doesn't already need; it must work
inside the strict bwrap (no /home, no internet on import path).
"""
from __future__ import annotations

import ipaddress as _ipaddress
import os as _os
import socket as _socket

# ADR-0159 M4: bridge TCP port exception — set by forge runner when using
# tcp_loopback transport. 0 means no exception (unix_socket mode or not set).
_BRIDGE_PORT: int = 0
_raw_port = _os.environ.get("CORVIN_BRIDGE_PORT", "").strip()
if _raw_port.isdigit():
    _p = int(_raw_port)
    if 1 <= _p <= 65535:
        _BRIDGE_PORT = _p


# Hostnames that always resolve to loopback.
_BLOCKED_NAMES = {"localhost", "ip6-localhost", "ip6-loopback"}
# Known IPv6 cloud-IMDS endpoints (v4 IMDS 169.254.169.254 is caught by
# is_link_local). fd00:ec2::254 is unique-local so needs an explicit entry.
_IMDS_V6 = {"fd00:ec2::254", "fe80::a9fe:a9fe"}
# Cloud-IMDS IPv4 endpoints that are NOT link-local/loopback/private and so slip
# the structural checks: Oracle Cloud (192.0.0.192) and Alibaba Cloud
# (100.100.100.200). AWS/GCP/Azure use 169.254.169.254 (caught by is_link_local).
_IMDS_V4 = {"192.0.0.192", "100.100.100.200"}


def _ip_is_blocked(ipstr: str) -> bool:
    """True if a parsed IP is loopback / link-local / IMDS (incl. v4-mapped v6)."""
    try:
        ip = _ipaddress.ip_address(ipstr)
    except ValueError:
        return False
    # is_unspecified catches 0.0.0.0 / :: / decimal 0 — the kernel routes a
    # connect() to the unspecified address to 127.0.0.1, so it reaches host
    # loopback services and must be blocked alongside is_loopback (R2-13 missed
    # this; verified 0.0.0.0 reaches a 127.0.0.1 listener).
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and (mapped.is_loopback or mapped.is_link_local
                               or mapped.is_unspecified):
        return True
    s = str(ip)
    if s in _IMDS_V6 or s in _IMDS_V4:
        return True
    m = getattr(ip, "ipv4_mapped", None)
    return m is not None and str(m) in _IMDS_V4


def _is_blocked(host: str) -> bool:
    # Normalise the target the way the KERNEL will, not by string prefix —
    # decimal (2130706433), hex (0x7f000001), octal, short-dotted (127.1) and
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) all reach loopback but slip a
    # "127."/"::1" prefix check. Parse via ipaddress + inet_aton and test the
    # canonical address. DNS names are also resolved + checked below (R2-13);
    # the residuals are a rebind that flips between our lookup and the kernel's,
    # and non-Python/bash tools (curl/wget) — close those with a netns +
    # nftables rule, not a Python shim.
    if not isinstance(host, str):
        return False
    h = host.strip().lower()
    if h in _BLOCKED_NAMES:
        return True
    if _ip_is_blocked(h):
        return True
    try:
        # inet_aton accepts decimal/hex/octal/short-dotted IPv4 encodings.
        if _ip_is_blocked(_socket.inet_ntoa(_socket.inet_aton(h))):
            return True
    except (OSError, ValueError):
        pass
    # R2-13: a DNS NAME that resolves to loopback/IMDS (an attacker-controlled
    # domain pointing at 169.254.169.254, or a rebind name) was previously
    # passed verbatim to the kernel, which resolved + connected to the blocked
    # IP — an SSRF-to-IMDS bypass for network-allowed Python personas. Resolve
    # the name here and block if ANY resolved address is loopback/IMDS. Skip the
    # lookup for literal IPs (already checked above). A rebind that flips BETWEEN
    # this lookup and the kernel's own connect remains a residual — the true
    # closure is a netns + nftables egress rule (see "What it does NOT do").
    try:
        _ipaddress.ip_address(h)
        return False  # literal IP — already checked, no DNS to resolve
    except ValueError:
        pass
    try:
        infos = _socket.getaddrinfo(host, None)
    except (OSError, ValueError, UnicodeError):
        return False
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        if sockaddr and _ip_is_blocked(str(sockaddr[0])):
            return True
    return False


def _refuse(addr) -> None:
    msg = (
        f"loopback-deny: connection to {addr!r} blocked by sandbox policy. "
        f"This persona may reach external networks but not 127.0.0.0/8, "
        f"::1, or 169.254.169.254. To allow: set "
        f"persona_sandbox_overrides[<persona>][\"loopback\"]=\"allow\" "
        f"in policy.json."
    )
    # ConnectionRefusedError keeps urllib / requests / httpx error paths
    # well-behaved. errno=111 (ECONNREFUSED) is the canonical "service
    # not listening" signal.
    raise ConnectionRefusedError(111, msg)


_orig_connect = _socket.socket.connect
_orig_connect_ex = _socket.socket.connect_ex


def _is_bridge_port_exception(address: object) -> bool:
    """ADR-0159 M4: allow connection to bridge's own TCP loopback port.

    Only active when CORVIN_BRIDGE_PORT is set (tcp_loopback transport).
    Checks both host (must resolve to loopback) and port (must be exact match)
    so the exception cannot be widened to non-bridge loopback services.
    """
    if _BRIDGE_PORT == 0:
        return False
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host, port = address[0], address[1]
    if port != _BRIDGE_PORT:
        return False
    # Verify that the host IS loopback (prevents non-loopback abuse of the
    # env var if somehow set in a non-bridge context).
    return _is_blocked(host)


def _wrapped_connect(self, address, *args, **kwargs):
    if isinstance(address, tuple) and address:
        if _is_bridge_port_exception(address):
            pass  # ADR-0159 M4: allow bridge's own TCP loopback port
        elif _is_blocked(address[0]):
            _refuse(address)
    return _orig_connect(self, address, *args, **kwargs)


def _wrapped_connect_ex(self, address, *args, **kwargs):
    if isinstance(address, tuple) and address:
        if _is_bridge_port_exception(address):
            pass  # ADR-0159 M4: allow bridge's own TCP loopback port
        elif _is_blocked(address[0]):
            # connect_ex returns errno; 111 = ECONNREFUSED.
            return 111
    return _orig_connect_ex(self, address, *args, **kwargs)


_socket.socket.connect = _wrapped_connect
_socket.socket.connect_ex = _wrapped_connect_ex
