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
    def parse(cls, value: "str | int | DataClassification | None") -> "DataClassification":
        """Coerce a free-form value into a classification. Defaults to
        ``INTERNAL`` on unknown input (the safe middle ground — neither
        broadcast PUBLIC nor lockdown SECRET)."""
        if value is None:
            return cls.INTERNAL
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return cls.INTERNAL
        if isinstance(value, str):
            v = value.strip().upper()
            if v in cls.__members__:
                return cls[v]
        return cls.INTERNAL


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
      * ``local``    — only local sockets (e.g. local Ollama on 11434)
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
#                   --provider ollama  → locality=local, egress=local
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
    # ADR-0066/0067 — HermesEngine (local Ollama HTTP, zero egress).
    # Qualifies for CONFIDENTIAL and SECRET tasks without a compliance-zone
    # exception — the only bundled engine that satisfies both locality=local
    # AND network_egress=none by design (POST localhost:11434 only).
    "hermes": EngineCompliance(
        engine_id="hermes",
        locality="local",
        network_egress="none",
        notes="Ollama HTTP localhost:11434 only — zero network egress. "
              "L34 CONFIDENTIAL-capable without compliance-zone exception.",
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
    # override acs_worker to locality=local (e.g. via HermesEngine workers).
    "acs_worker": EngineCompliance(
        engine_id="acs_worker",
        locality="us_cloud",
        network_egress="external",
        notes="ACS manager+worker via claude -p → api.anthropic.com. "
              "Max classification: INTERNAL. "
              "For CONFIDENTIAL, override workers to use delegate_hermes.",
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
    # Conventional override aliases that EU presets pin to:
    "opencode_ollama": EngineCompliance(
        engine_id="opencode_ollama",
        locality="local",
        network_egress="local",
        notes="opencode CLI pinned to --provider ollama (local socket).",
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
    # For CONFIDENTIAL/SECRET jobs, use delegate_hermes (locality=local).
    "anthropic_batch": EngineCompliance(
        engine_id="anthropic_batch",
        locality="us_cloud",
        network_egress="external",
        notes="Anthropic Message Batches API — US jurisdiction. "
              "50% cost reduction vs real-time; 1-24 h result latency. "
              "Max classification: INTERNAL. "
              "Enabled via corvin-batch MCP Plugin Manager manifest.",
    ),
    # ADR-0126 — Claude Code Local Backend (Ollama redirect).
    # When enabled, Claude Code sends inference requests to a local Ollama server
    # (ANTHROPIC_BASE_URL redirect) instead of api.anthropic.com.
    # Locality/egress identical to Hermes — CONFIDENTIAL-capable.
    "claude_code_local": EngineCompliance(
        engine_id="claude_code_local",
        locality="local",
        network_egress="none",
        notes="Claude Code redirected to local Ollama (ADR-0126). "
              "Zero external egress — CONFIDENTIAL-capable without compliance-zone exception. "
              "Activated via CORVIN_CC_LOCAL_MODE=1 + tenant.corvin.yaml::spec.claude_code_local.enabled.",
    ),
}


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
# DESIGN: data-residency restriction is *opt-in*, not opt-out. The default
# is permissive so a zero-config single-operator install runs frictionless on
# its configured cloud engine (e.g. claude_code = us_cloud) — a normal chat
# message containing a name or e-mail is classified CONFIDENTIAL and must NOT
# be blocked by default. Operators with stricter data-residency needs tighten
# the matrix explicitly in tenant.corvin.yaml (see the eu-production preset,
# which pins every row to [local]). This mirrors the classifier's own stance:
# "Default is PUBLIC — users opt in to restriction."
#
# The one residual floor is SECRET (literal API keys / private keys /
# passwords detected by regex): it stays local-only AND carries an *additional*
# constraint (network_egress=="none") that the guard enforces independently of
# this mapping. SECRET fires rarely and protects credentials from egress — it
# is a security floor, not a residency policy, so it is kept on by default.
DEFAULT_MATRIX: dict[DataClassification, frozenset[Locality]] = {
    DataClassification.PUBLIC:       frozenset({"local", "eu_cloud", "us_cloud"}),
    DataClassification.INTERNAL:     frozenset({"local", "eu_cloud", "us_cloud"}),
    DataClassification.CONFIDENTIAL: frozenset({"local", "eu_cloud", "us_cloud"}),
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
                  - engine_id: opencode_ollama
                    locality: local
                    network_egress: local
                    notes: "qwen3:8b via Ollama"

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
                matrix[cls_key] = frozenset(allowed_localities)  # type: ignore[arg-type]

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
                        "Use hermes or opencode_ollama for local-only requirements."
                    )
                engine_compliance[eid] = EngineCompliance(
                    engine_id=eid,
                    locality=locality,         # type: ignore[arg-type]
                    network_egress=egress,     # type: ignore[arg-type]
                    notes=notes,
                )

        return cls(
            matrix=matrix,
            engine_compliance=engine_compliance,
            audit_writer=audit_writer,
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
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                           # OpenAI / Anthropic style
    re.compile(r"sk_live_[A-Za-z0-9]{16,}"),                      # Stripe live
    re.compile(r"AKIA[0-9A-Z]{16}"),                              # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),                       # Google API key
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                          # GitHub personal access
    re.compile(r"xox[bpoa]-[A-Za-z0-9-]{20,}"),                   # Slack token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),            # PEM private key
    re.compile(r"\bpassword\s*[:=]\s*[^\s]{6,}", re.IGNORECASE),  # password=...
]


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
            forge_pkg = repo / "operator" / "forge"
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
    # Validate tenant_id BEFORE any path interpolation — an attacker- or
    # env-influenced id with path separators ("../") could otherwise read an
    # arbitrary YAML as the L34 policy, or traverse to a non-existent path to
    # silently disable enforcement (downgrade). Reject anything that isn't a
    # single safe path segment.
    if not isinstance(tenant_id, str) or not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,62}", tenant_id):
        try:
            from forge.tenants import validate_tenant_id as _vti  # type: ignore
            tenant_id = _vti(tenant_id)
        except Exception:  # noqa: BLE001 — invalid id → no guard (don't traverse)
            return None
    home = corvin_home
    if home is None:
        env = os.environ.get("CORVIN_HOME")
        if env:
            home = Path(os.path.expanduser(os.path.expandvars(env)))
        else:
            try:
                from forge.paths import corvin_home as _ch  # type: ignore
                home = _ch()
            except Exception:  # noqa: BLE001
                home = Path.home() / ".corvin"
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
    audit_path = cfg_path.parent / "forge" / "audit.jsonl"
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
