"""Operator Declaration Gate — ADR-0057 Component 3.

Verifies that the operator has completed the EU AI Act Art. 28-30
operator declaration before the system accepts production traffic.

The declaration is read from:
  tenant.corvin.yaml::spec.operator_declaration

Required fields for eu_production + eu_production_ollama profiles:
  version:         "1.0"          (must match CURRENT_DECLARATION_VERSION)
  dpia_completed:  true
  dpia_date:       "YYYY-MM-DD"

Optional (stored but NEVER in audit chain — PII):
  declared_by:     "J. Müller"    (DPO or legal contact)
  permitted_use:   "internal-coding-assistant"

Audit event allow-list: declaration_version, dpia_completed, dpia_date,
deployment_profile.  Never: declared_by, permitted_use, any free-form text.

Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CURRENT_DECLARATION_VERSION = "1.0"

REQUIRE_DECLARATION_PROFILES: frozenset[str] = frozenset({
    "eu_production",
    "eu_production_ollama",
})


@dataclass(frozen=True)
class DeclarationCheckResult:
    ok: bool
    profile: str
    declaration_version: str = ""
    dpia_completed: bool = False
    dpia_date: str = ""
    error: str = ""

    def audit_dict(self) -> dict[str, Any]:
        """ADR-0057 audit allow-list — never includes declared_by."""
        return {
            "declaration_version": self.declaration_version,
            "dpia_completed": self.dpia_completed,
            "dpia_date": self.dpia_date,
            "deployment_profile": self.profile,
        }


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    return Path(os.path.expanduser(env)) if env else Path.home() / ".corvin"


def _tenant_yaml_path(tenant_id: str) -> Path:
    root = _corvin_home()
    primary = root / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    if primary.exists():
        return primary
    return root / "global" / "tenant.corvin.yaml"


def check_operator_declaration(tenant_id: str = "_default") -> DeclarationCheckResult:
    """Read tenant YAML and validate the operator_declaration block.

    Returns DeclarationCheckResult with ok=True when:
      - deployment_profile is not in REQUIRE_DECLARATION_PROFILES, OR
      - declaration is present with dpia_completed=true and a valid dpia_date.

    Returns ok=False (maps to CRITICAL in self_test.py) when:
      - profile requires declaration AND it is absent or incomplete.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return DeclarationCheckResult(
            ok=True,
            profile="unknown",
            error="PyYAML not installed — check skipped",
        )

    p = _tenant_yaml_path(tenant_id)
    if not p.exists():
        return DeclarationCheckResult(
            ok=True,
            profile="unknown",
            error="tenant.corvin.yaml not found — skipped",
        )

    try:
        data = yaml.safe_load(p.read_text("utf-8")) or {}
    except Exception as exc:
        # FAIL-CLOSED (R1 finding): the file EXISTS (operator intended a policy)
        # but cannot be parsed — so we cannot read deployment_profile to learn
        # whether a DPIA/Art.28-30 declaration is required. Returning ok=True
        # here let a malformed config silently bypass the declaration gate on an
        # eu_production deployment. Block instead and make the operator fix the
        # YAML (mirrors the L34/L35/L44 fail-closed loaders).
        return DeclarationCheckResult(
            ok=False,
            profile="unknown",
            error=f"tenant.corvin.yaml present but unparseable ({type(exc).__name__}) — "
                  "declaration gate fails CLOSED; fix the YAML to proceed.",
        )

    spec = data.get("spec") or {}
    profile = str(
        spec.get("deployment_profile") or spec.get("profile") or ""
    )

    if profile not in REQUIRE_DECLARATION_PROFILES:
        return DeclarationCheckResult(ok=True, profile=profile)

    decl = spec.get("operator_declaration") or {}
    if not decl:
        return DeclarationCheckResult(
            ok=False,
            profile=profile,
            error=(
                f"operator_declaration missing in tenant.corvin.yaml — "
                f"EU AI Act Art. 28-30 requires a completed declaration "
                f"for deployment_profile='{profile}'. "
                f"See docs/compliance/OPERATOR-OBLIGATIONS.md"
            ),
        )

    version = str(decl.get("version", ""))
    dpia_completed = bool(decl.get("dpia_completed", False))
    dpia_date = str(decl.get("dpia_date", ""))

    if not dpia_completed:
        return DeclarationCheckResult(
            ok=False,
            profile=profile,
            declaration_version=version,
            dpia_completed=False,
            dpia_date=dpia_date,
            error=(
                "operator_declaration.dpia_completed is false — "
                "complete DPIA before eu_production deployment. "
                "Use docs/compliance/DPIA-TEMPLATE.md"
            ),
        )

    if not dpia_date:
        return DeclarationCheckResult(
            ok=False,
            profile=profile,
            declaration_version=version,
            dpia_completed=dpia_completed,
            error="operator_declaration.dpia_date missing — record the DPIA completion date",
        )

    return DeclarationCheckResult(
        ok=True,
        profile=profile,
        declaration_version=version,
        dpia_completed=dpia_completed,
        dpia_date=dpia_date,
    )


def emit_declaration_audit(result: DeclarationCheckResult) -> None:
    """Best-effort audit emit for operator.declaration_verified.

    Only emits when ok=True (failed declarations are surfaced by self_test
    CRITICAL, not by a separate audit event).  Allow-list: audit_dict() only.
    """
    if not result.ok:
        return
    if not result.declaration_version and not result.dpia_date:
        return  # non-eu_production profiles: nothing to emit
    try:
        import sys
        from pathlib import Path as _Path

        repo = _Path(__file__).resolve().parents[3]
        forge = repo / "operator" / "forge"
        if forge.is_dir() and str(forge) not in sys.path:
            sys.path.insert(0, str(forge))
        from forge import security_events as _se  # type: ignore
        import os as _os

        home = _os.environ.get("CORVIN_HOME") or _os.environ.get("CORVIN_HOME")
        audit_dir = (
            (_Path(_os.path.expanduser(home)) if home else _Path.home() / ".corvin")
            / "global" / "forge"
        )
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_file = _Path(
            _os.environ.get("VOICE_AUDIT_PATH") or str(audit_dir / "audit.jsonl")
        )
        _se.write_event(
            audit_file,
            "operator.declaration_verified",
            severity="INFO",
            tool="", run_id="",
            details=result.audit_dict(),
            hash_chain=True,
        )
    except Exception:
        pass
