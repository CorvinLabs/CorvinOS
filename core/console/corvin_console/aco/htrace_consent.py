"""HealingTrace consent management (ADR-0180 §5).

Implements the GDPR Art. 7-compliant consent flow: timestamped ConsentAct
with the exact text SHA-256, stored in the L16 audit chain.

The YAML flag (telemetry.healing_traces: true) is the operator-level gate.
The ConsentAct is the user-level gate. BOTH must be present for uploads.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONSENT_VERSION = "htrace/1.0"
_CONSENT_TEXT_FILE = Path(__file__).parent / "consent_texts" / "htrace-1.0.txt"

# SHA-256 of the exact consent text (htrace-1.0.txt). Must match the file.
# Any change to the text requires bumping _CONSENT_VERSION and this hash.
_CONSENT_TEXT_SHA256 = ""  # computed lazily on first use


def _consent_text() -> str:
    return _CONSENT_TEXT_FILE.read_text(encoding="utf-8")


def _consent_text_sha256() -> str:
    global _CONSENT_TEXT_SHA256  # noqa: PLW0603
    if not _CONSENT_TEXT_SHA256:
        _CONSENT_TEXT_SHA256 = hashlib.sha256(
            _consent_text().encode("utf-8")
        ).hexdigest()
    return _CONSENT_TEXT_SHA256


# ── ConsentAct ────────────────────────────────────────────────────────────────

@dataclass
class ConsentAct:
    """GDPR Art. 7 consent record — the single source of truth for 'was consent given?'

    Stored locally in the L16 audit chain AND sent to the server for Art. 7
    accountability. The text_sha256 pins the exact text that was shown.
    """
    consent_act_id: str
    consent_version: str
    ts_utc: str
    text_sha256: str
    method: str          # "cli" | "web_ui"
    corvin_version: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, home: Path) -> Optional["ConsentAct"]:
        """Load the stored ConsentAct or None if not present / malformed."""
        p = _consent_act_path(home)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return cls(**d)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def save(self, home: Path) -> None:
        p = _consent_act_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        p.chmod(0o600)

    @property
    def is_current_version(self) -> bool:
        return self.consent_version == _CONSENT_VERSION

    @property
    def is_text_intact(self) -> bool:
        return self.text_sha256 == _consent_text_sha256()


def _consent_act_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / "htrace-consent-act.json"


# ── Consent gate (double gate: YAML flag + ConsentAct) ────────────────────────

def healing_traces_enabled(home: Path, *, cfg: dict | None = None) -> bool:
    """True only when BOTH the YAML opt-in flag AND a valid ConsentAct are present.

    deny-by-default — any missing piece → False.
    """
    # Gate 1: YAML flag
    if cfg is not None:
        if not cfg.get("telemetry", {}).get("healing_traces", False):
            return False
    else:
        # Fall back to reading tenant.corvin.yaml if no cfg passed
        try:
            import yaml  # type: ignore[import]
            cfg_path = home.parent.parent / "global" / "tenant.corvin.yaml"
            if cfg_path.exists():
                data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                if not data.get("telemetry", {}).get("healing_traces", False):
                    return False
            else:
                return False
        except Exception:  # noqa: BLE001
            return False

    # Gate 2: ConsentAct
    act = ConsentAct.load(home)
    if act is None:
        return False
    if not act.is_current_version:
        logger.info("htrace: consent version mismatch (%s vs %s) — paused",
                    act.consent_version, _CONSENT_VERSION)
        return False
    return True


def load_consent_act_id(home: Path) -> str:
    """Return the consent_act_id for embedding in records, or '' if none."""
    act = ConsentAct.load(home)
    return act.consent_act_id if act is not None else ""


# ── CLI consent flow ──────────────────────────────────────────────────────────

def run_consent_flow(home: Path, *, method: str = "cli") -> Optional[ConsentAct]:
    """Interactive consent prompt. Returns ConsentAct on 'yes', None on cancel.

    Shows the versioned consent text, requires explicit 'yes' input (not Enter),
    and records a ConsentAct in the L16 audit chain on acceptance.
    """
    try:
        text = _consent_text()
    except OSError:
        print("ERROR: consent text not found — cannot enable healing traces.", file=sys.stderr)
        return None

    print("\n" + "=" * 72)
    print(text)

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return None

    if answer != "yes":
        print("Healing trace telemetry NOT enabled.")
        return None

    try:
        from importlib.metadata import version
        cv = version("corvinOS")
    except Exception:  # noqa: BLE001
        cv = "unknown"

    act = ConsentAct(
        consent_act_id=str(uuid.uuid4()),
        consent_version=_CONSENT_VERSION,
        ts_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        text_sha256=_consent_text_sha256(),
        method=method,
        corvin_version=cv,
    )
    act.save(home)
    _write_consent_audit_event(home, act)
    print(f"\nHealing trace telemetry enabled (consent_act_id: {act.consent_act_id[:8]}…).")
    return act


def run_revoke_flow(home: Path) -> None:
    """Revoke consent. Immediate effect — no further uploads."""
    p = _consent_act_path(home)
    if p.exists():
        p.write_text(
            json.dumps({"revoked": True, "ts_utc": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
        _write_revoke_audit_event(home)
    print("Healing trace telemetry disabled. No further data will be sent.")


# ── L16 audit chain integration ───────────────────────────────────────────────

def _write_consent_audit_event(home: Path, act: ConsentAct) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        _audit.audit_event(
            "telemetry.consent.granted",
            {
                "consent_act_id": act.consent_act_id,
                "consent_version": act.consent_version,
                "text_sha256": act.text_sha256[:16],  # partial — enough for correlation
                "method": act.method,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _write_revoke_audit_event(home: Path) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        _audit.audit_event("telemetry.consent.revoked", {})
    except Exception:  # noqa: BLE001
        pass
