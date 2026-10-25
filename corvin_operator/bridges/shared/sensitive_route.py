"""spec.data_classification.sensitive_route: parsing and provider eligibility (ADR-2268).

ONE module read by the pre-spawn gate and by the console routes, so the predicates of the
ADR's normative core cannot be implemented twice and drift apart:

* ``ROUTE_BLOCK``     the value is a mapping (``null`` counts as absent)
* ``ROUTE_ON``        a block whose ``enabled`` is the boolean ``true``
* ``ROUTE_MALFORMED`` a block whose ``enabled`` is present but not a boolean, or an enabled
                      block whose ``provider`` / ``model_id`` is missing or outside its charset

Eligibility is stricter than matrix admissibility. A provider is a target only with an explicit
operator declaration (``provider_compliance``) of locality exactly ``local`` or ``eu_cloud``;
never the native ``anthropic`` provider, never an aggregator (``model_source: openrouter``: the
vendor prefix of the model id picks the real upstream) and never a provider behind a
``proxy_base_url`` (the real upstream is unknown). The declaration is bound to the endpoint it
was made for: when the provider's current egress host differs, it is ineligible until declared
again. A platform provider is judged on the host rendered from its DECLARED region and resource,
never from the ambient environment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import data_classification as _dc

PROVIDER_ID_RE = re.compile(r"[a-z0-9_][a-z0-9_-]{0,62}")
MODEL_ID_RE = re.compile(r"[A-Za-z0-9._:@/-]{1,200}")

#: The classes a route can ever serve. ``PUBLIC`` never changes path; ``SECRET`` is never rerouted.
ROUTABLE_CLASSES = ("INTERNAL", "CONFIDENTIAL")
_ELIGIBLE_LOCALITIES = frozenset({"local", "eu_cloud"})


@dataclass(frozen=True)
class SensitiveRoute:
    provider: str
    model_id: str


@dataclass(frozen=True)
class RouteParse:
    block: bool            # ROUTE_BLOCK
    on: bool               # ROUTE_ON (well formed and enabled)
    malformed: bool        # ROUTE_MALFORMED
    route: "SensitiveRoute | None" = None     # set only when ``on``
    stored: "SensitiveRoute | None" = None    # well-formed provider + model_id, enabled or not
    reason: str = ""


def parse_sensitive_route(tenant_config: "dict | None") -> RouteParse:
    """Read the predicates from a parsed ``tenant.corvin.yaml`` mapping. Pure, never raises."""
    spec = tenant_config.get("spec") if isinstance(tenant_config, dict) else None
    dcfg = spec.get("data_classification") if isinstance(spec, dict) else None
    raw = dcfg.get("sensitive_route") if isinstance(dcfg, dict) else None
    if not isinstance(raw, dict):
        return RouteParse(block=False, on=False, malformed=False)

    provider = raw.get("provider")
    model_id = raw.get("model_id")
    stored = (
        SensitiveRoute(provider, model_id)
        if isinstance(provider, str) and PROVIDER_ID_RE.fullmatch(provider)
        and isinstance(model_id, str) and MODEL_ID_RE.fullmatch(model_id)
        else None
    )
    if "enabled" in raw and not isinstance(raw["enabled"], bool):
        return RouteParse(block=True, on=False, malformed=True, stored=stored,
                          reason="enabled_not_boolean")
    if raw.get("enabled") is not True:
        return RouteParse(block=True, on=False, malformed=False, stored=stored)
    if stored is None:
        return RouteParse(block=True, on=False, malformed=True,
                          reason="provider_or_model_id_missing_or_invalid")
    return RouteParse(block=True, on=True, malformed=False, route=stored, stored=stored)


def current_provider_host(ps: Any, decl: "_dc.ProviderCompliance") -> str:
    """The host a spawn to this provider would use, as a bare lower-case hostname ("" if none).

    A platform provider's host is the ``runtime_host_template`` (else ``host_template``) rendered
    with the DECLARED region and resource, never the ambient environment. Any other provider's
    host is the hostname of its ``base_url``.
    """
    if getattr(ps, "is_platform", False):
        env = getattr(ps, "platform_env", None) or {}
        tpl = str(env.get("runtime_host_template") or env.get("host_template") or "")
        if not tpl or (("{resource}" in tpl) and not decl.resource):
            return ""
        if ("{region}" in tpl) and not decl.region:
            return ""
        try:
            url = tpl.format(region=decl.region, resource=decl.resource)
        except (KeyError, IndexError, ValueError):
            return ""
        return (urlparse(url).hostname or "").lower()
    return (urlparse(str(getattr(ps, "base_url", "") or "")).hostname or "").lower()


def provider_eligibility(
    provider: str, *, declared: "dict[str, _dc.ProviderCompliance]", providers: "dict[str, Any]",
) -> "tuple[bool, str]":
    """``(eligible, reason)``: ``reason`` is ``""`` when eligible, else a stable machine code."""
    ps = providers.get(provider)
    if ps is None:
        return False, "unknown_provider"
    if provider == "anthropic" or getattr(ps, "id", "") == "anthropic":
        return False, "native_provider"
    if getattr(ps, "model_source", "") == "openrouter":
        return False, "aggregator"
    if getattr(ps, "proxy_base_url", ""):
        return False, "proxy_provider"
    decl = declared.get(provider)
    if decl is None:
        return False, "not_declared"
    if decl.locality not in _ELIGIBLE_LOCALITIES:
        return False, "locality_not_admissible"
    if getattr(ps, "is_platform", False):
        env = getattr(ps, "platform_env", None) or {}
        needs_project = bool(env.get("project_env"))
        tpl = str(env.get("runtime_host_template") or env.get("host_template") or "")
        if (not decl.region or not decl.models
                or (needs_project and not decl.project)
                or ("{resource}" in tpl and not decl.resource)):
            return False, "platform_incomplete"
    host = current_provider_host(ps, decl)
    if not host or host != decl.endpoint_host.rstrip("."):
        return False, "endpoint_changed"
    return True, ""


def model_allowed(
    provider: str, model_id: str, *, declared: "dict[str, _dc.ProviderCompliance]",
    providers: "dict[str, Any]",
) -> bool:
    """Is *model_id* usable on *provider*? Verbatim charset check for every provider; a
    platform provider additionally only serves the models its declaration lists."""
    ps = providers.get(provider)
    if ps is None or not isinstance(model_id, str) or not MODEL_ID_RE.fullmatch(model_id):
        return False
    if getattr(ps, "is_platform", False):
        decl = declared.get(provider)
        return decl is not None and model_id in decl.models
    return True


def eligible_providers(tenant_config: "dict | None", providers: "dict[str, Any]") -> "list[dict[str, Any]]":
    """The providers a sensitive route may target, with what each one admits. Never a sample:
    an empty declaration set gives an empty list. May raise ``ValueError`` when the tenant's
    matrix is malformed (the caller reports that as an invalid policy)."""
    declared, _rejected = _dc.parse_provider_compliance(tenant_config)
    matrix = _dc.DataFlowGuard.from_tenant_config(tenant_config).matrix
    rows: list[dict[str, Any]] = []
    for pid in sorted(declared):
        ok, _why = provider_eligibility(pid, declared=declared, providers=providers)
        if not ok:
            continue
        decl, ps = declared[pid], providers[pid]
        admitted = [name for name in ROUTABLE_CLASSES
                    if decl.locality in matrix.get(_dc.DataClassification[name], frozenset())]
        rows.append({
            "provider": pid, "label": getattr(ps, "label", pid), "locality": decl.locality,
            "network_egress": decl.network_egress, "region": decl.region,
            "resource": decl.resource, "admitted_classes": admitted,
        })
    return rows
