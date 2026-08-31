"""corvin-license — ADR-0017 Phase III license-gate plugin.

Apache-2.0 plugin that mounts onto the gateway's ASGI app at
``/v1/license/*``. Validates RS256-signed JWT license tokens locally
against a pinned public key. No network calls, no telemetry.

Free-tier semantics: a deployment without ``license.jwt`` installed
reports ``tier="free"`` and disables nothing. The gate only fires
when an installed token is expired/revoked AND the 30-day grace
period has also elapsed.
"""
__version__ = "0.1.0"
