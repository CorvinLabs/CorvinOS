"""data_classification.py — Layer 34: Data Classification + Flow Guard.

ADR-0042 (companion to ADR-0041 EU compliance). Orthogonal axis to the
compliance-zone classifier in :mod:`compliance_zone_classifier`:

  * **compliance_zone** answers "what kind of content is this?"
    (``general`` / ``code_only`` / ``personal_data`` /
    ``external_facing``).
  * **DataClassification** answers "how sensitive is this?"
    (``PUBLIC`` / ``INTERNAL`` / ``CONFIDENTIAL`` / ``SECRET``).

Both axes must be satisfied; this module owns the sensitivity axis.

Resolution rule (the Matrix)::

    PUBLIC       → any allowed engine
    INTERNAL     → engines with locality in {local, eu_cloud}
    CONFIDENTIAL → engines with locality == local
    SECRET       → engines with locality == local AND network_egress == none

Operators override the matrix per tenant via
``tenant.corvin.yaml::spec.data_classification.matrix``. Operators
override engine compliance metadata per tenant via
``spec.data_classification.engine_compliance``.

Fail-closed contract: on unknown classification, unknown engine_id, or
matrix miss, :class:`DataFlowGuard` returns a ``FlowDecision(allowed=False,
…)``. Callers can choose to convert the decision into a
:class:`DataFlowDenied` exception via ``validate_or_raise()``.

Audit contract (mirror of L16 hash-chain allow-list rule):

  * ``data_flow.approved`` (severity INFO)
  * ``data_flow.blocked``  (severity CRITICAL)

Both events carry ONLY metadata in ``details``:
``classification`` (name), ``engine_id``, ``persona``, ``channel``,
``chat_key``, ``reason``. NEVER the task text, NEVER the prompt,
NEVER the engine output.

CI lint:

  * Module MUST NOT ``import anthropic``.
  * ``details`` MUST stay in the allow-list — see
    :data:`_AUDIT_ALLOWED`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Literal

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("data_classification", version="1.0", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass

# ----- public enums ---------------------------------------------------

class DataClassification(IntEnum):
    """4-stage sensitivity grading.

    Ordered by sensitivity ascending: ``PUBLIC < INTERNAL <
    CONFIDENTIAL < SECRET``. Comparison operators work as expected
    (``classification >= DataClassification.CONFIDENTIAL`` is a valid
    check).
    """
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    SECRET = 3

    @classmethod
    def parse(cls, value: "str | int | DataClassification | None") -> "DataClassification | None":
        """Coerce a value into a classification, or ``None`` when it is not one.

        F-A10 (2026-09-07): an unknown/unparseable value used to become
        ``INTERNAL`` — which every default matrix row allows on a cloud
        engine, i.e. a typo in a caller's label silently DOWNGRADED the flow
        guard to "allow". ``None`` is returned instead and
        :meth:`DataFlowGuard.validate` denies it (``unknown_classification``).
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return None
        if isinstance(value, str):
            v = value.strip().upper()
            if v in cls.__members__:
                return cls[v]
        return None


# ----- engine compliance metadata -------------------------------------

Locality = Literal["local", "eu_cloud", "us_cloud", "unknown"]
NetworkEgress = Literal["none", "local", "external"]


@dataclass(frozen=True)
class EngineCompliance:
    """Per-engine_id compliance properties.

    ``locality`` answers "where does the model run?":
      * ``local``    — model executes on this machine / LAN
      * ``eu_cloud`` — model executes on EU-jurisdiction infrastructure
      * ``us_cloud`` — model executes on US-jurisdiction infrastructure
      * ``unknown``  — operator hasn't classified this engine yet

    ``network_egress`` answers "what does the engine talk to over the
    network during a spawn?":
      * ``none``     — no outbound calls at all (fully air-gapped)
      * ``local``    — only local / LAN sockets (e.g. a self-hosted endpoint)
      * ``external`` — public internet (an HTTPS endpoint)
    """
    engine_id: str
    locality: Locality
    network_egress: NetworkEgress
    notes: str = ""


# Conservative defaults. Operators MUST audit + override these for
# their tenant — the defaults are what we *know* about the bundled
# engines:
#   * claude_code → api.anthropic.com (US)
#   * codex_cli   → api.openai.com (US)
#   * opencode    → DEPENDS on provider, so we mark it unknown by
#                   default. Operators pin via tenant config:
#                   --provider anthropic → locality=us_cloud, egress=external
DEFAULT_ENGINE_COMPLIANCE: dict[str, EngineCompliance] = {
    "claude_code": EngineCompliance(
        engine_id="claude_code",
        locality="us_cloud",
        network_egress="external",
        notes="api.anthropic.com — US jurisdiction",
    ),
    "codex_cli": EngineCompliance(
        engine_id="codex_cli",
        locality="us_cloud",
        network_egress="external",
        notes="api.openai.com — US jurisdiction",
    ),
    "opencode": EngineCompliance(
        engine_id="opencode",
        locality="unknown",
        network_egress="external",
        notes="Locality depends on --provider; override per-tenant.",
    ),
    # ADR-0071 — CopilotCliEngine (github/copilot-cli, github.com by default).
    # github.com is US-jurisdiction cloud; operators with GitHub Enterprise Cloud
    # EU data residency can override to eu_cloud; GHES on-premise → local.
    "copilot": EngineCompliance(
        engine_id="copilot",
        locality="us_cloud",
        network_egress="external",
        notes="GitHub Copilot via github.com — US jurisdiction by default. "
              "GitHub Enterprise Cloud with EU data residency: override to eu_cloud. "
              "GHES on-premise: override to locality=local, network_egress=local.",
    ),
    # ADR-0104 — ACS Worker (Autonomous Compute Shell, manager + worker spawns).
    # Workers call api.anthropic.com via ``claude -p`` subprocess — same
    # locality and egress profile as claude_code.  Operators with an EU-only
    # deployment MUST NOT use ACS for CONFIDENTIAL/SECRET tasks unless they
    # override acs_worker via engine_compliance for an engine they host.
    "acs_worker": EngineCompliance(
        engine_id="acs_worker",
        locality="us_cloud",
        network_egress="external",
        notes="ACS manager+worker via claude -p → api.anthropic.com. "
              "Max classification: INTERNAL. "
              "No bundled engine is admissible for CONFIDENTIAL/SECRET here; a tenant "
              "may declare its own engine via engine_compliance.",
    ),
    # "acs" is the engine_id used by chat_runtime.py when a turn is routed
    # through the delegation fan-out (ACS orchestrator layer). Same compliance
    # profile as acs_worker — both egress to api.anthropic.com.
    "acs": EngineCompliance(
        engine_id="acs",
        locality="us_cloud",
        network_egress="external",
        notes="ACS orchestrator (delegation fan-out alias for acs_worker). "
              "Used by chat_runtime to classify delegated web-chat turns. "
              "Max classification: INTERNAL without operator override.",
    ),
    # Console Video Producer job (marketplace plugin buildin/media/corvintv,
    # started by core/console/corvin_console/routes/video_producer_api.py). Every job
    # sends the LLM-written narration — derived from the user's task text — to Google's
    # public translate TTS endpoint (gTTS), so its floor is US cloud + external egress
    # whatever storyboard backend it uses. The storyboard host (api.anthropic.com when
    # the Anthropic backend is chosen) is validated separately by the route via L35.
    "video_producer": EngineCompliance(
        engine_id="video_producer",
        locality="us_cloud",
        network_egress="external",
        notes="Console Video Producer: narration to translate.google.com (gTTS, tts_engine=gtts), "
              "optional storyboard via api.anthropic.com. Max classification: "
              "INTERNAL without operator override.",
    ),
    # The same job when its narration engine is OpenAI TTS (the default since ADR-2211) or
    # the edge-tts tier of the "auto" chain: same floor, different destination host.
    "video_producer_openai": EngineCompliance(
        engine_id="video_producer_openai",
        locality="us_cloud",
        network_egress="external",
        notes="Console Video Producer: narration to api.openai.com (OpenAI TTS), "
              "optional storyboard via api.anthropic.com. Max classification: "
              "INTERNAL without operator override.",
    ),
    "video_producer_edge": EngineCompliance(
        engine_id="video_producer_edge",
        locality="us_cloud",
        network_egress="external",
        notes="Console Video Producer: narration to speech.platform.bing.com (edge-tts, "
              "second tier of the auto chain). Max classification: INTERNAL without operator override.",
    ),
    "opencode_http": EngineCompliance(
        engine_id="opencode_http",
        locality="local",
        network_egress="local",
        notes="Self-hosted OpenCode HTTP server on tenant LAN.",
    ),
    # ADR-0098 — AnthropicBatchEngine (ABP, fire-and-forget batch inference).
    # Calls api.anthropic.com — US jurisdiction, external egress.
    # Max classification: INTERNAL (PUBLIC or INTERNAL data only).
    # No bundled engine takes CONFIDENTIAL/SECRET jobs from here (ADR-2091).
    # ADR-2091: the local-inference engines ``hermes``, ``opencode_ollama`` and
    # ``claude_code_local`` were removed. Their ids are no longer in this
    # registry, so ``validate`` refuses them as ``unknown_engine``. No bundled
    # engine has network_egress='none', so SECRET has no bundled admissible
    # engine; a tenant may declare its own engine via engine_compliance.
    "anthropic_batch": EngineCompliance(
        engine_id="anthropic_batch",
        locality="us_cloud",
        network_egress="external",
        notes="Anthropic Message Batches API — US jurisdiction. "
              "50% cost reduction vs real-time; 1-24 h result latency. "
              "Max classification: INTERNAL. "
              "Enabled via corvin-batch MCP Plugin Manager manifest.",
    ),
}


# T-0226 / ADR-2268 — bundled engines whose traffic always goes to a US cloud API.
# ADR-0072 pinned ``claude_code`` alone; an operator ``engine_compliance`` entry could
# still relabel any of these as ``local`` / ``eu_cloud`` or claim egress ``none`` and
# the guard then admitted CONFIDENTIAL / SECRET data to them. Their locality and egress
# are facts about the engine, not preferences. ``copilot`` is deliberately NOT here: its
# bundled note documents GitHub Enterprise Cloud EU residency and on-premise GHES as real
# overrides. Restating the truth (``us_cloud`` / ``external``) or narrowing to
# ``unknown`` stays allowed: both only refuse more.
@dataclass(frozen=True)
class ProviderCompliance:
    """Operator-declared facts about one provider a rerouted claude_code turn may use (ADR-2268).

    ``endpoint_host`` is bound to the endpoint the operator declared: a provider whose host
    changed afterwards is ineligible until it is declared again. ``region`` / ``models`` are
    set only for platform providers (Bedrock / Vertex / Foundry).
    """
    provider: str
    locality: str
    network_egress: str
    endpoint_host: str
    region: str = ""
    project: str = ""
    resource: str = ""
    models: tuple[str, ...] = ()


_PROVIDER_LOCALITIES = frozenset({"local", "eu_cloud", "us_cloud"})
_PROVIDER_EGRESS = frozenset({"external", "local"})
_PROVIDER_ID_RE = re.compile(r"[a-z0-9_][a-z0-9_-]{0,62}")


def parse_provider_compliance(
    tenant_config: "dict | None",
) -> "tuple[dict[str, ProviderCompliance], list[dict[str, str]]]":
    """Parse ``spec.data_classification.provider_compliance`` in its OWN step (ADR-2268, Decision 8).

    Returns ``(valid, rejected)``. A malformed entry, an ``unknown`` locality, egress ``none``,
    a missing ``endpoint_host`` or a platform provider without its region or models drops
    THAT provider only. A provider declared twice is rejected in both entries. The matrix
    guard is never affected: this function is not called by ``from_tenant_config``.
    Pure: reads a parsed mapping, raises nothing.
    """
    spec = tenant_config.get("spec") if isinstance(tenant_config, dict) else None
    dcfg = spec.get("data_classification") if isinstance(spec, dict) else None
    entries = dcfg.get("provider_compliance") if isinstance(dcfg, dict) else None
    valid: dict[str, ProviderCompliance] = {}
    rejected: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return valid, rejected

    seen: dict[str, int] = {}
    for entry in entries:
        pid = entry.get("provider") if isinstance(entry, dict) else None
        if isinstance(pid, str) and _PROVIDER_ID_RE.fullmatch(pid):
            seen[pid] = seen.get(pid, 0) + 1

    for entry in entries:
        if not isinstance(entry, dict):
            rejected.append({"provider": "", "reason": "entry is not a mapping"})
            continue
        pid = entry.get("provider")
        if not isinstance(pid, str) or not _PROVIDER_ID_RE.fullmatch(pid):
            rejected.append({"provider": "", "reason": "provider id missing or malformed"})
            continue
        if seen.get(pid, 0) > 1:
            rejected.append({"provider": pid, "reason": "declared more than once"})
            continue
        locality = entry.get("locality")
        if locality not in _PROVIDER_LOCALITIES:
            rejected.append({"provider": pid, "reason": f"locality {locality!r} is not admissible"})
            continue
        egress = entry.get("network_egress")
        if egress not in _PROVIDER_EGRESS:
            rejected.append({"provider": pid, "reason": f"network_egress {egress!r} is not admissible"})
            continue
        host = entry.get("endpoint_host")
        if not isinstance(host, str) or not host.strip():
            rejected.append({"provider": pid, "reason": "endpoint_host is required"})
            continue
        region = str(entry.get("region") or "")
        models_raw = entry.get("models") or []
        models = tuple(str(m) for m in models_raw) if isinstance(models_raw, list) else ()
        platform = bool(entry.get("platform"))
        if platform and (not region or not models):
            rejected.append({"provider": pid, "reason": "a platform provider needs region and models"})
            continue
        valid[pid] = ProviderCompliance(
            provider=pid, locality=locality, network_egress=egress,
            endpoint_host=host.strip().lower(), region=region,
            project=str(entry.get("project") or ""), resource=str(entry.get("resource") or ""),
            models=models,
        )
    return valid, rejected


PINNED_US_CLOUD_ENGINES: frozenset[str] = frozenset({
    "acs", "acs_worker", "anthropic_batch", "codex_cli",
    "video_producer", "video_producer_openai", "video_producer_edge",
})


def pinned_relabel_violations(tenant_config: "dict | None") -> "list[dict[str, str]]":
    """Operator ``engine_compliance`` entries that relabel a pinned engine to a safer place.

    Pure: reads the parsed ``tenant.corvin.yaml`` mapping, builds nothing, raises nothing.
    Each item names ``engine_id``, ``field`` (``locality`` or ``network_egress``) and the
    offending ``value``. :meth:`DataFlowGuard.from_tenant_config` raises on a non-empty
    result; the boot report lists them so an existing config is named, not just refused.
    """
    spec = tenant_config.get("spec") if isinstance(tenant_config, dict) else None
    dcfg = spec.get("data_classification") if isinstance(spec, dict) else None
    entries = dcfg.get("engine_compliance") if isinstance(dcfg, dict) else None
    found: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return found
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("engine_id")
        if eid not in PINNED_US_CLOUD_ENGINES:
            continue
        locality = entry.get("locality", "unknown")
        egress = entry.get("network_egress", "external")
        if locality in ("local", "eu_cloud"):
            found.append({"engine_id": eid, "field": "locality", "value": str(locality)})
        if egress in ("none", "local"):
            found.append({"engine_id": eid, "field": "network_egress", "value": str(egress)})
    return found


# ── Single source of truth: the delegation-fanout engine_id ──────────────────
# chat_runtime.py classifies a *delegated* web-chat turn under this engine_id
# (the ACS orchestrator fan-out alias) rather than the configured OS engine.
# It MUST be a key in DEFAULT_ENGINE_COMPLIANCE — otherwise the L34 guard fails
# closed with "engine_id='acs' not in compliance registry" and silently blocks
# EVERY delegated turn while direct (non-delegated) turns keep working. That is
# the exact production symptom diagnosed on 2026-06-27 (web:VErk2UPDjg session)
# and patched in commit 49457d3: the producer emitted the literal "acs" while
# the registry only held "acs_worker".
#
# The producer (core/console chat_runtime, via _spawn_gates) imports THIS
# constant instead of hard-coding the literal, so the two can never drift again.
# The invariant (DELEGATION_ENGINE_ID in DEFAULT_ENGINE_COMPLIANCE) is locked by
# test_data_classification.test_delegation_engine_id_registered — a CI gate that
# catches the same class of drift for any future engine rename.
DELEGATION_ENGINE_ID: str = "acs"


# Default matrix — sensitivity → set of allowed localities.
#
# DESIGN (F-A10, 2026-09-07 — RESTRICTIVE default): PUBLIC and INTERNAL may go
# anywhere; CONFIDENTIAL (personal data — the classifier grades a message
# carrying a name / e-mail / phone as CONFIDENTIAL) never leaves EU/local
# jurisdiction by default; SECRET (literal credentials) is local-only AND
# additionally requires network_egress=="none" (enforced independently of
# this mapping). This is what ``load_guard_for_tenant`` already promised for
# its fail-closed fallback ("the restrictive DEFAULT matrix (CONFIDENTIAL/
# SECRET → local only)") and what GDPR Art. 44 ff. imply for a zero-config
# install: residency is the default, WIDENING is the operator's explicit,
# audited choice in ``tenant.corvin.yaml``:
#
#     spec:
#       data_classification:
#         matrix:
#           CONFIDENTIAL: [local, eu_cloud, us_cloud]   # opt IN to US cloud
#
# Note the enforcement contract is unchanged: no tenant.corvin.yaml on disk →
# ``load_guard_for_tenant`` returns None (no enforcement at all). This matrix
# applies the moment a tenant config exists without its own ``matrix``.
DEFAULT_MATRIX: dict[DataClassification, frozenset[Locality]] = {
    DataClassification.PUBLIC:       frozenset({"local", "eu_cloud", "us_cloud"}),
    DataClassification.INTERNAL:     frozenset({"local", "eu_cloud", "us_cloud"}),
    DataClassification.CONFIDENTIAL: frozenset({"local", "eu_cloud"}),
    DataClassification.SECRET:       frozenset({"local"}),
}


# ----- decision shape -------------------------------------------------

@dataclass(frozen=True)
class FlowDecision:
    """Result of a :meth:`DataFlowGuard.validate` call."""
    allowed: bool
    classification: DataClassification
    engine_id: str
    reason: str
    matched_rule: str  # "matrix" | "unknown_engine" | "unknown_classification" | "secret_egress"


class DataFlowDenied(Exception):
    """Raised by :meth:`DataFlowGuard.validate_or_raise` on deny."""
    def __init__(self, decision: FlowDecision):
        self.decision = decision
        super().__init__(
            f"data flow denied: classification={decision.classification.name} "
            f"engine_id={decision.engine_id} reason={decision.reason}"
        )


# ----- audit allow-list (regression-tested) ---------------------------

_AUDIT_ALLOWED: frozenset[str] = frozenset({
    "classification",  # enum name only, not the task content
    "engine_id",
    "persona",
    "channel",
    "chat_key",
    "reason",
    "matched_rule",
})


def _validate_audit_details(details: dict[str, Any]) -> None:
    for k in details:
        if k not in _AUDIT_ALLOWED:
            raise ValueError(
                f"data_flow audit detail '{k}' not in allow-list "
                f"{sorted(_AUDIT_ALLOWED)}"
            )


# ----- guard ----------------------------------------------------------

# Type for the audit writer. Decoupled from forge.security_events so the
# module is testable without the forge import path being importable.
AuditWriter = Callable[[str, str, dict[str, Any]], None]
# Signature: (event_type, severity, details) -> None


@dataclass
class DataFlowGuard:
    """Sensitivity × engine-locality matrix enforcement.

    Construct one instance per tenant (cheap; mostly immutable state).
    Call :meth:`validate` at every engine-spawn callsite; pass the
    returned :class:`FlowDecision` upstream so callers can decide
    between soft (degrade-and-route) and strict (raise) responses.

    Tenant overrides are loaded once at construction time via
    :meth:`from_tenant_config`. Hot-reload is the operator's concern
    (re-construct the guard when ``tenant.corvin.yaml`` mtime changes).
    """
    matrix: dict[DataClassification, frozenset[Locality]] = field(
        default_factory=lambda: dict(DEFAULT_MATRIX)
    )
    engine_compliance: dict[str, EngineCompliance] = field(
        default_factory=lambda: dict(DEFAULT_ENGINE_COMPLIANCE)
    )
    audit_writer: AuditWriter | None = None
    #: ADR-2268 — operator-declared facts about providers a rerouted claude_code turn may use.
    #: Parsed in its own step; a rejected declaration is listed, never applied.
    provider_compliance: dict[str, ProviderCompliance] = field(default_factory=dict)
    provider_compliance_rejected: list[dict[str, str]] = field(default_factory=list)

    # ----- factories --------------------------------------------------

    @classmethod
    def from_tenant_config(
        cls,
        tenant_config: dict[str, Any] | None,
        *,
        audit_writer: AuditWriter | None = None,
    ) -> "DataFlowGuard":
        """Build a guard from a parsed tenant.corvin.yaml dict.

        Expects (all optional)::

            spec:
              data_classification:
                # Example of TIGHTENING the permissive defaults for a
                # data-residency-restricted tenant (EU/local only). Omit
                # the matrix entirely to keep the permissive module defaults
                # (all rows allow us_cloud except SECRET → local).
                matrix:
                  PUBLIC:       [local, eu_cloud]
                  INTERNAL:     [local, eu_cloud]
                  CONFIDENTIAL: [local]
                  SECRET:       [local]
                engine_compliance:
                  - engine_id: my_onprem_engine
                    locality: local
                    network_egress: local
                    notes: "self-hosted model on the tenant LAN"

        Missing fields keep the module defaults. Malformed entries
        raise ``ValueError`` — operator should see the configuration
        error loudly.
        """
        matrix = dict(DEFAULT_MATRIX)
        engine_compliance = dict(DEFAULT_ENGINE_COMPLIANCE)

        if not tenant_config:
            return cls(
                matrix=matrix,
                engine_compliance=engine_compliance,
                audit_writer=audit_writer,
            )

        spec = tenant_config.get("spec") if isinstance(tenant_config, dict) else None
        if not isinstance(spec, dict):
            return cls(
                matrix=matrix,
                engine_compliance=engine_compliance,
                audit_writer=audit_writer,
            )

        dc = spec.get("data_classification")
        if not isinstance(dc, dict):
            return cls(
                matrix=matrix,
                engine_compliance=engine_compliance,
                audit_writer=audit_writer,
            )

        # Matrix override
        mraw = dc.get("matrix")
        if isinstance(mraw, dict):
            for key, vals in mraw.items():
                try:
                    cls_key = DataClassification[str(key).strip().upper()]
                except KeyError as e:
                    raise ValueError(
                        f"data_classification.matrix: unknown classification {key!r}"
                    ) from e
                if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
                    raise ValueError(
                        f"data_classification.matrix[{key}] must be list[str]"
                    )
                allowed_localities = {v.strip().lower() for v in vals}
                bad = allowed_localities - {"local", "eu_cloud", "us_cloud", "unknown"}
                if bad:
                    raise ValueError(
                        f"data_classification.matrix[{key}] has unknown locality(ies): {sorted(bad)}"
                    )
                # R2-A5: "unknown" is not a place. It is the label for an engine
                # the operator has NOT classified yet (``opencode`` ships with
                # it), so admitting it in a matrix row says "send this grade of
                # data anywhere, including wherever we have not looked" — the
                # opposite of what a residency matrix is for, and a permanent
                # hole that widens by itself every time a new unclassified
                # engine appears. Classify the engine instead
                # (``engine_compliance``), then name its real locality here.
                if "unknown" in allowed_localities:
                    raise ValueError(
                        f"data_classification.matrix[{key}]: 'unknown' is not an "
                        "admissible locality — it means the engine has not been "
                        "classified. Give the engine a real locality under "
                        "spec.data_classification.engine_compliance instead."
                    )
                # R2-A5: SECRET is the residual floor — the one grade whose
                # promise is "never leaves this machine". A tenant config could
                # widen it to us_cloud, which makes the strictest classification
                # in the system weaker than a config line, with nothing in the
                # chain marking the moment it happened. The egress rule in
                # ``validate`` (SECRET requires network_egress == "none") is not
                # a substitute: it is an AND, so a cloud locality that also
                # claimed zero egress would pass both.
                if cls_key is DataClassification.SECRET:
                    forbidden = allowed_localities - {"local"}
                    if forbidden:
                        raise ValueError(
                            "data_classification.matrix[SECRET] may only allow "
                            f"'local' — refusing {sorted(forbidden)}. SECRET is the "
                            "residual floor; no bundled engine is admissible for "
                            "it — declare your own zero-egress engine under "
                            "engine_compliance, or classify the data CONFIDENTIAL."
                        )
                matrix[cls_key] = frozenset(allowed_localities)  # type: ignore[arg-type]

        # T-0226 / ADR-2268: the same pin as claude_code below, for every other bundled
        # engine that calls a US cloud API. Raised before any entry is applied so a
        # tenant never ends up with half a policy. The loader failure refuses every
        # spawn of the tenant (spawn_gates._load_l34_guard), it never allows.
        _relabels = pinned_relabel_violations(tenant_config)
        if _relabels:
            raise ValueError(
                "ADR-2268: engine_compliance may not relabel a bundled US-cloud engine as "
                "a safer place — "
                + "; ".join(f"{v['engine_id']}.{v['field']}={v['value']!r}" for v in _relabels)
                + ". These engines always egress to a US API. Declare your own engine "
                "under engine_compliance for local or EU processing."
            )

        # Engine compliance override / extension
        eraw = dc.get("engine_compliance")
        if isinstance(eraw, list):
            for entry in eraw:
                if not isinstance(entry, dict):
                    raise ValueError(
                        "data_classification.engine_compliance entries must be dicts"
                    )
                eid = entry.get("engine_id")
                if not isinstance(eid, str) or not eid:
                    raise ValueError(
                        "data_classification.engine_compliance: engine_id required"
                    )
                locality = entry.get("locality", "unknown")
                if locality not in ("local", "eu_cloud", "us_cloud", "unknown"):
                    raise ValueError(
                        f"engine_compliance[{eid}]: locality={locality!r} invalid"
                    )
                egress = entry.get("network_egress", "external")
                if egress not in ("none", "local", "external"):
                    raise ValueError(
                        f"engine_compliance[{eid}]: network_egress={egress!r} invalid"
                    )
                notes = entry.get("notes") or ""
                if not isinstance(notes, str):
                    raise ValueError(
                        f"engine_compliance[{eid}]: notes must be a string"
                    )
                # ADR-0072: V-020 — claude_code always egresses to api.anthropic.com (us_cloud).
                # An operator setting locality=local would produce incorrect compliance classifications.
                if eid == "claude_code" and locality == "local":
                    raise ValueError(
                        "ADR-0072: claude_code locality cannot be overridden to 'local' — "
                        "it always egresses to api.anthropic.com (us_cloud). "
                        "No bundled engine is admissible for local-only data; declare "
                        "your own engine under engine_compliance."
                    )
                # R2-A5: the pin above covered ONE of the two fields the SECRET
                # rule reads. ``validate`` admits SECRET only when
                # ``network_egress == "none"``, so an operator who could not say
                # ``locality: local`` could still say ``network_egress: none``
                # and route SECRET data to api.anthropic.com by the other half of
                # the same false claim. Both fields are facts about the engine,
                # not preferences: claude_code makes an external network call.
                if eid == "claude_code" and egress != "external":
                    raise ValueError(
                        f"ADR-0072: claude_code network_egress cannot be overridden to "
                        f"{egress!r} — it always egresses to api.anthropic.com. "
                        "No bundled engine is admissible for zero-egress data; declare "
                        "your own engine under engine_compliance."
                    )
                engine_compliance[eid] = EngineCompliance(
                    engine_id=eid,
                    locality=locality,         # type: ignore[arg-type]
                    network_egress=egress,     # type: ignore[arg-type]
                    notes=notes,
                )

        # ADR-2268: provider_compliance has its OWN parse step and can only add or drop
        # providers; it never touches the matrix or engine_compliance above.
        _providers, _rejected = parse_provider_compliance(tenant_config)

        return cls(
            matrix=matrix,
            engine_compliance=engine_compliance,
            audit_writer=audit_writer,
            provider_compliance=_providers,
            provider_compliance_rejected=_rejected,
        )

    # ----- core API ---------------------------------------------------

    def validate(
        self,
        *,
        classification: "DataClassification | str | int | None",
        engine_id: str,
        persona: str | None = None,
        channel: str | None = None,
        chat_key: str | None = None,
    ) -> FlowDecision:
        """Return a :class:`FlowDecision`. Never raises (use
        :meth:`validate_or_raise` for the strict variant).

        Emits exactly one audit event per call:
        ``data_flow.approved`` on allow, ``data_flow.blocked`` on deny.
        """
        cls = DataClassification.parse(classification)
        if cls is None:
            # F-A10: an unparseable classification is NOT a permissive default.
            return self._deny(
                DataClassification.SECRET, engine_id,
                reason=f"classification {classification!r} is not a known level",
                matched_rule="unknown_classification",
                persona=persona, channel=channel, chat_key=chat_key,
            )

        # Unknown engine → fail closed.
        compl = self.engine_compliance.get(engine_id)
        if compl is None:
            return self._deny(
                cls, engine_id,
                reason=f"engine_id={engine_id!r} not in compliance registry",
                matched_rule="unknown_engine",
                persona=persona, channel=channel, chat_key=chat_key,
            )

        # SECRET extra rule: network_egress must be "none".
        if cls == DataClassification.SECRET and compl.network_egress != "none":
            return self._deny(
                cls, engine_id,
                reason=(
                    f"SECRET requires network_egress='none', "
                    f"engine has '{compl.network_egress}'"
                ),
                matched_rule="secret_egress",
                persona=persona, channel=channel, chat_key=chat_key,
            )

        # Matrix lookup.
        allowed_localities = self.matrix.get(cls)
        if allowed_localities is None:
            return self._deny(
                cls, engine_id,
                reason=f"classification {cls.name} not in matrix",
                matched_rule="unknown_classification",
                persona=persona, channel=channel, chat_key=chat_key,
            )

        if compl.locality not in allowed_localities:
            return self._deny(
                cls, engine_id,
                reason=(
                    f"engine locality={compl.locality!r} not in "
                    f"allowed {sorted(allowed_localities)} for {cls.name}"
                ),
                matched_rule="matrix",
                persona=persona, channel=channel, chat_key=chat_key,
            )

        return self._approve(
            cls, engine_id,
            persona=persona, channel=channel, chat_key=chat_key,
        )

    def validate_provider(
        self,
        *,
        classification: "DataClassification | str | int | None",
        engine_id: str,
        provider: str,
        current_host: "str | None",
        persona: str | None = None,
        channel: str | None = None,
        chat_key: str | None = None,
    ) -> FlowDecision:
        """Judge the pair (``claude_code``, *provider*) for a rerouted turn (ADR-2268).

        Reads the operator's ``provider_compliance`` instead of the engine's locality: a
        rerouted turn keeps the engine and changes only the endpoint. Fail-closed on every
        doubt. Never raises. Emits one audit event per call, naming ``<engine>@<provider>``
        and never the prompt.

        Denied: an engine other than ``claude_code``; ``SECRET`` always (the CLI is not
        zero-egress whatever the endpoint is); a provider with no valid declaration; a
        *current_host* that is unknown or differs from the declared ``endpoint_host`` (the
        locality is bound to the endpoint it was declared for); a locality outside the
        matrix row of the class.
        """
        label = f"{engine_id}@{provider}"
        where = dict(persona=persona, channel=channel, chat_key=chat_key)
        cls = DataClassification.parse(classification)
        if cls is None:
            return self._deny(
                DataClassification.SECRET, label,
                reason=f"classification {classification!r} is not a known level",
                matched_rule="unknown_classification", **where)
        if engine_id != "claude_code" or engine_id not in self.engine_compliance:
            return self._deny(
                cls, label, reason="only claude_code can be rerouted to a provider",
                matched_rule="provider_engine", **where)
        if cls == DataClassification.SECRET:
            return self._deny(
                cls, label,
                reason="SECRET is never rerouted: the claude_code CLI is not zero-egress",
                matched_rule="secret_provider", **where)
        decl = self.provider_compliance.get(provider)
        if decl is None:
            return self._deny(
                cls, label, reason=f"provider {provider!r} has no valid provider_compliance entry",
                matched_rule="provider_not_declared", **where)
        if not isinstance(current_host, str) or not current_host.strip():
            return self._deny(
                cls, label, reason="the provider's current egress host is unknown",
                matched_rule="endpoint_unknown", **where)
        if current_host.strip().rstrip(".").lower() != decl.endpoint_host.rstrip("."):
            return self._deny(
                cls, label,
                reason="the provider's egress host differs from the declared endpoint_host; declare it again",
                matched_rule="endpoint_changed", **where)
        allowed_localities = self.matrix.get(cls)
        if allowed_localities is None or decl.locality not in allowed_localities:
            return self._deny(
                cls, label,
                reason=(f"provider locality={decl.locality!r} not in allowed "
                        f"{sorted(allowed_localities or [])} for {cls.name}"),
                matched_rule="matrix", **where)
        return self._approve(cls, label, persona=persona, channel=channel, chat_key=chat_key)

    def validate_or_raise(self, **kwargs: Any) -> FlowDecision:
        """Strict variant: raise :class:`DataFlowDenied` on deny.

        Same kwargs as :meth:`validate`. Returns the (allowed) decision
        on pass so callers can record ``matched_rule`` if they care.
        """
        decision = self.validate(**kwargs)
        if not decision.allowed:
            raise DataFlowDenied(decision)
        return decision

    def list_engines_for(
        self,
        classification: "DataClassification | str | int | None",
    ) -> list[str]:
        """Diagnostic: which registered engine_ids are admissible for
        the given classification? Used by ``/whoami`` and the gap
        analysis verifier."""
        cls = DataClassification.parse(classification)
        allowed_localities = self.matrix.get(cls, frozenset())
        out: list[str] = []
        for eid, compl in self.engine_compliance.items():
            if compl.locality not in allowed_localities:
                continue
            if cls == DataClassification.SECRET and compl.network_egress != "none":
                continue
            out.append(eid)
        return sorted(out)

    # ----- internals --------------------------------------------------

    def _approve(
        self,
        cls: DataClassification,
        engine_id: str,
        *,
        persona: str | None,
        channel: str | None,
        chat_key: str | None,
    ) -> FlowDecision:
        decision = FlowDecision(
            allowed=True,
            classification=cls,
            engine_id=engine_id,
            reason="matrix allow",
            matched_rule="matrix",
        )
        self._emit("data_flow.approved", "INFO", decision,
                   persona=persona, channel=channel, chat_key=chat_key)
        return decision

    def _deny(
        self,
        cls: DataClassification,
        engine_id: str,
        *,
        reason: str,
        matched_rule: str,
        persona: str | None,
        channel: str | None,
        chat_key: str | None,
    ) -> FlowDecision:
        decision = FlowDecision(
            allowed=False,
            classification=cls,
            engine_id=engine_id,
            reason=reason,
            matched_rule=matched_rule,
        )
        self._emit("data_flow.blocked", "CRITICAL", decision,
                   persona=persona, channel=channel, chat_key=chat_key)
        return decision

    def _emit(
        self,
        event_type: str,
        severity: str,
        decision: FlowDecision,
        *,
        persona: str | None,
        channel: str | None,
        chat_key: str | None,
    ) -> None:
        if self.audit_writer is None:
            return
        details: dict[str, Any] = {
            "classification": decision.classification.name,
            "engine_id": decision.engine_id,
            "reason": decision.reason,
            "matched_rule": decision.matched_rule,
        }
        if persona is not None:
            details["persona"] = persona
        if channel is not None:
            details["channel"] = channel
        if chat_key is not None:
            details["chat_key"] = chat_key
        try:
            _validate_audit_details(details)  # structural defence — best-effort, must not gate flow
            self.audit_writer(event_type, severity, details)
        except Exception:  # noqa: BLE001
            # Best-effort, mirrors engine_switch._audit() pattern.
            pass


# ----- default heuristic classifier -----------------------------------

# Explicit operator marker. Example: "[class:secret] write a deploy script…"
_CLASS_MARKER_RE = re.compile(
    r"^\s*\[class:(public|internal|confidential|secret)\]\s*",
    re.IGNORECASE,
)

# Conservative secret patterns (regex bank). False-positive cost is
# "we routed it to a local-only engine", which is the safe direction.
_SECRET_PATTERNS = [
    # Common credential key prefixes
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                           # OpenAI legacy style
    # T-0231 — current formats carry a dash right after "sk-", which the legacy
    # pattern above cannot match (sk-ant-api03-..., sk-proj-... were classified PUBLIC).
    re.compile(r"sk-(?:ant|proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}"),  # Anthropic / OpenAI project, service account, admin
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),                  # GitHub fine-grained token
    re.compile(r"gh[ousr]_[A-Za-z0-9]{30,}"),                     # GitHub OAuth / user / server / refresh token
    re.compile(r"sk_live_[A-Za-z0-9]{16,}"),                      # Stripe live
    re.compile(r"AKIA[0-9A-Z]{16}"),                              # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),                       # Google API key
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                          # GitHub personal access
    re.compile(r"xox[bpoa]-[A-Za-z0-9-]{20,}"),                   # Slack token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),            # PEM private key
    re.compile(r"\bpassword\s*[:=]\s*[^\s]{6,}", re.IGNORECASE),  # password=...
    # T-0248 — vendor formats the 2026-10-21 review found missing. Each carries the vendor's
    # documented body length and an alphabet without "_", bounded on both sides, so prose
    # ("the hf_ prefix"), snake_case identifiers (hf_hub_download) and short strings never
    # trip them; the false-positive cost of the bank is still only "routed locally".
    re.compile(r"sk-or-v1-[0-9a-fA-F]{64}"),                                  # OpenRouter (64 hex)
    # GitLab PAT (20-char body; the GitLab 17 routable form adds dotted segments)
    re.compile(r"(?<![A-Za-z0-9_])glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{34}(?![A-Za-z0-9_])"),        # HuggingFace user token (34)
    re.compile(r"(?<![A-Za-z0-9_])gsk_[A-Za-z0-9]{52}(?![A-Za-z0-9_])"),       # Groq (52)
    re.compile(r"(?<![A-Za-z0-9_])xai-[A-Za-z0-9]{80}(?![A-Za-z0-9_])"),       # xAI (80)
    re.compile(r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{36}(?![A-Za-z0-9_])"),       # npm access token (36)
]


class _JwtPattern:
    """A compact JWS/JWT (``header.payload.signature``) in the bank (T-0248).

    Three base64url segments are not enough — ``eyJ`` is only the encoding of ``{"``, and
    dotted identifiers exist — so a candidate counts only when its header decodes to a JSON
    object naming ``alg`` (RFC 7515 §4.1.1). The signature may be empty (``alg: none``).
    Duck-types :meth:`re.Pattern.search` so the bank stays a flat list of ``search``-ables.
    """

    _RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*")
    pattern = "<jwt: header.payload.signature with alg in the header>"

    @staticmethod
    def _header_names_alg(token: str) -> bool:
        import base64
        import binascii
        import json
        head = token.split(".", 1)[0]
        try:
            raw = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))
            obj = json.loads(raw.decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return False
        return isinstance(obj, dict) and "alg" in obj

    def search(self, text: str) -> re.Match[str] | None:
        for m in self._RE.finditer(text):
            if self._header_names_alg(m.group(0)):
                return m
        return None


_SECRET_PATTERNS.append(_JwtPattern())  # type: ignore[arg-type]  # JWT bearer tokens


def classify_task(
    task: str,
    persona: str | None = None,
    *,
    secret_patterns: list[re.Pattern[str]] | None = None,
) -> DataClassification:
    """Heuristic classifier. Default is ``PUBLIC`` — users opt in to restriction.

    Precedence:
      1. Secret regex hit → ``SECRET`` (cannot be overridden by any marker).
      2. Explicit ``[class:<name>]`` marker → user/operator override (only
         honoured when no secret pattern matched in step 1). Users who handle
         sensitive data add ``[class:internal]`` or ``[class:confidential]``
         to their task to enforce stricter engine routing.
      3. compliance_zone == ``personal_data`` → ``CONFIDENTIAL``.
      4. Otherwise → ``PUBLIC`` (any engine allowed).

    Rationale: data residency restrictions should apply when the user knows
    their data is sensitive and declares it. Defaulting to INTERNAL blocked
    tasks that use only public data sources (e.g. yfinance, public APIs).
    Users who need INTERNAL/CONFIDENTIAL handling prepend [class:internal].

    ``secret_patterns`` is injectable for tests; defaults to the
    module-level :data:`_SECRET_PATTERNS` bank.
    """
    if not isinstance(task, str) or not task.strip():
        return DataClassification.PUBLIC

    # 1. Secret patterns first — cannot be overridden by an explicit marker.
    # A task containing a literal secret must never be down-classified to PUBLIC
    # by prepending [class:public].
    patterns = secret_patterns if secret_patterns is not None else _SECRET_PATTERNS
    for pat in patterns:
        if pat.search(task):
            return DataClassification.SECRET

    # 2. Explicit marker (only honoured when no secret pattern matched above).
    m = _CLASS_MARKER_RE.match(task)
    if m:
        return DataClassification[m.group(1).upper()]

    # 3. PII → CONFIDENTIAL (delegate to compliance_zone_classifier).
    # Lazy import to keep this module standalone-testable.
    try:
        from .compliance_zone_classifier import classify_zone  # type: ignore
    except ImportError:
        try:
            from compliance_zone_classifier import classify_zone  # type: ignore
        except ImportError:
            classify_zone = None  # type: ignore[assignment]
    if classify_zone is not None:
        try:
            z = classify_zone(task, persona)
            if isinstance(z, dict) and z.get("zone") == "personal_data":
                return DataClassification.CONFIDENTIAL
        except Exception:  # noqa: BLE001
            pass

    # 4. Default: PUBLIC — users opt in to stricter classification via [class:internal].
    return DataClassification.PUBLIC


# ----- forge-backed audit writer (production wiring) ------------------

def make_forge_audit_writer(audit_path: Path) -> AuditWriter:
    """Build an :data:`AuditWriter` that appends to the unified forge
    chain via :func:`forge.security_events.write_event`.

    Best-effort: if forge isn't importable (standalone test
    environment), returns a no-op writer.
    """
    try:
        import sys
        here = Path(__file__).resolve()
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "corvin_operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception:  # noqa: BLE001
        def _noop(event_type: str, severity: str, details: dict[str, Any]) -> None:
            return
        return _noop

    def _writer(event_type: str, severity: str, details: dict[str, Any]) -> None:
        try:
            write_event(
                audit_path, event_type,
                severity=severity, details=details,
            )
        except Exception:  # noqa: BLE001
            pass

    return _writer


_SIBLING_PATHS = None


def _sibling_paths():
    """The ``paths`` module that sits next to THIS file, loaded by file path.

    Never ``corvin_operator.forge.forge.paths`` and never a bare ``import
    paths``: with the live venv's editable ``.pth`` on ``sys.path`` the dotted
    name can resolve into a DIFFERENT checkout, whose repo-marker home is that
    checkout's ``.corvin`` — the split-brain that let a worktree test run
    append 34 foreign-MAC ``data_flow.*`` records to the live install's chain
    (2026-09-27). A bare ``paths`` can resolve to ``corvin_operator/forge/paths.py``
    (a different module of the same name without ``tenant_audit_chain``).
    """
    global _SIBLING_PATHS
    if _SIBLING_PATHS is None:
        import importlib.util as _ilu  # noqa: PLC0415

        spec = _ilu.spec_from_file_location(
            "_paths_l34", Path(__file__).resolve().parent / "paths.py",
        )
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _SIBLING_PATHS = mod
    return _SIBLING_PATHS


def _default_home() -> Path:
    """``CORVIN_HOME``, else THIS checkout's repo-marker home — via the sibling
    ``paths`` module, never a dotted import that may land in a different
    checkout (see :func:`_sibling_paths`)."""
    return Path(_sibling_paths().corvin_home())


def _guard_audit_chain(tenant_id: str, home: Path) -> Path:
    """The chain the L34 guard's ``data_flow.*`` records go to.

    Used to be composed by hand as ``<cfg dir>/forge/audit.jsonl``: a second
    per-tenant layout next to the canonical chain, blind to the redirects
    every other bridge writer honours. Now the same precedence as the bridge
    audit writer (``audit.audit_path()``): ``VOICE_AUDIT_PATH`` (file) >
    ``FORGE_ROOT/audit.jsonl`` (dir) > the canonical ``tenant_audit_chain``
    layout of the sibling ``paths`` module, applied under ``home`` (the home
    the tenant config was read from, so an explicit ``corvin_home`` keeps the
    guard's records under that home).
    """
    redirect = os.environ.get("VOICE_AUDIT_PATH", "").strip()
    if redirect:
        return Path(redirect).expanduser()
    forge_root = os.environ.get("FORGE_ROOT", "").strip()
    if forge_root:
        return Path(forge_root).expanduser() / "audit.jsonl"
    mirror = _sibling_paths()
    canonical = Path(mirror.tenant_audit_chain(tenant_id))
    try:
        rel = canonical.relative_to(Path(mirror.corvin_home()))
    except ValueError:
        return canonical
    return home / rel


def _tenant_config_file(
    tenant_id: str, corvin_home: "Path | None" = None,
) -> "tuple[str, Path, Path] | None":
    """Resolve ``(tenant_id, home, tenant.corvin.yaml)``, or ``None`` when there is nothing to read.

    ``None`` covers three cases that all mean "no L34 policy file": an id that is not a
    single safe path segment, a config that resolves outside ``<home>/tenants/``, and no
    file on disk (the single-operator zero-config default). Shared by
    :func:`load_guard_for_tenant` and :func:`report_pinned_relabels`, so the confinement
    rules exist once.
    """
    # Validate tenant_id BEFORE any path interpolation — an attacker- or
    # env-influenced id with path separators ("../") could otherwise read an
    # arbitrary YAML as the L34 policy, or traverse to a non-existent path to
    # silently disable enforcement (downgrade). Reject anything that isn't a
    # single safe path segment.
    if not isinstance(tenant_id, str) or not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,62}", tenant_id):
        try:
            from corvin_operator.forge.forge.tenants import validate_tenant_id as _vti  # type: ignore
            tenant_id = _vti(tenant_id)
        except Exception:  # noqa: BLE001 — invalid id → no guard (don't traverse)
            return None
    home = corvin_home
    if home is None:
        home = _default_home()
    # Expand the explicitly-passed home too (callers like ACS may pass a raw
    # "~/.corvin"/"$HOME/.corvin"); without this the same tenant is enforced
    # on some spawn paths and not others.
    home = Path(os.path.expanduser(os.path.expandvars(str(home))))
    tenants_root = (home / "tenants").resolve()
    cfg_path = (home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml").resolve()
    # Confinement: the resolved config MUST live under <home>/tenants/.
    if tenants_root not in cfg_path.parents:
        return None
    if not cfg_path.is_file():
        return None  # opt-in: no config → no enforcement (adapter parity)
    return tenant_id, home, cfg_path


def report_pinned_relabels(
    tenant_id: str, *, corvin_home: "Path | None" = None,
) -> "list[dict[str, str]]":
    """Name the engine relabels of an existing tenant config that L34 now rejects (T-0226).

    Run at boot so an operator whose ``engine_compliance`` relabels a bundled US-cloud
    engine reads WHICH entry is refused and why, instead of finding out as a refusal on
    the first spawn. Best effort and read-only: a missing, unreadable or unparseable file
    reports nothing (the guard loader already handles those, fail-closed). It never
    changes what the guard does.
    """
    resolved = _tenant_config_file(tenant_id, corvin_home)
    if resolved is None:
        return []
    tid, _home, cfg_path = resolved
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(cfg_path.read_text("utf-8"))
    except Exception:  # noqa: BLE001 — not this function's problem
        return []
    found = pinned_relabel_violations(cfg)
    if found:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "tenant %s: engine_compliance relabels a bundled US-cloud engine (%s); L34 refuses "
            "every spawn of this tenant until the entries are removed",
            tid, "; ".join(f"{v['engine_id']}.{v['field']}={v['value']}" for v in found),
        )
    return found


def load_guard_for_tenant(tenant_id: str, *, corvin_home: Path | None = None) -> "DataFlowGuard | None":
    """Build an enforcing DataFlowGuard for a tenant, or None to fail-open.

    SINGLE SOURCE OF TRUTH for the L34 opt-in contract used by every
    spawn-site gate (adapter, ACS, delegation, A2A, compute batch):

      * No ``tenant.corvin.yaml`` on disk  → return None (NO enforcement —
        the single-operator zero-config default; preserves back-compat).
      * File present → parse it and build a guard from its real
        ``spec.data_classification`` matrix (NOT the bare default matrix).

    Callers MUST pass the parsed config to ``from_tenant_config`` via this
    helper — passing a tenant_id *string* directly to ``from_tenant_config``
    is a bug (it expects a dict and silently falls back to the default
    matrix, ignoring the tenant's real policy).
    """
    resolved = _tenant_config_file(tenant_id, corvin_home)
    if resolved is None:
        return None  # invalid id / outside <home>/tenants / no config → no enforcement
    tenant_id, home, cfg_path = resolved
    audit_path = _guard_audit_chain(tenant_id, home)
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(cfg_path.read_text("utf-8"))
    except Exception:  # noqa: BLE001 — config PRESENT but unparseable
        # SECURITY: a broken/unparseable tenant.corvin.yaml must NOT silently
        # disable L34/L35 (that turned a deny policy into allow-all on a typo).
        # The config IS present (operator intended enforcement) → FAIL-CLOSED to
        # the restrictive DEFAULT matrix (CONFIDENTIAL/SECRET → local only)
        # rather than returning None (no enforcement).
        import logging as _logging
        _logging.getLogger(__name__).critical(
            "tenant.corvin.yaml for %s is unparseable — enforcing DEFAULT "
            "data-classification matrix (fail-closed), not the operator's policy",
            tenant_id,
        )
        return DataFlowGuard.from_tenant_config(
            {}, audit_writer=make_forge_audit_writer(audit_path),
        )
    return DataFlowGuard.from_tenant_config(
        cfg, audit_writer=make_forge_audit_writer(audit_path),
    )
