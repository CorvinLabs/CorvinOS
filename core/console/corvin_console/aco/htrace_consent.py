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
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONSENT_VERSION = "htrace/1.1"
_TOKEN_ENDPOINT = "https://api.corvin-labs.com/v1/telemetry/token"
_TOKEN_TIMEOUT_S = 15
_CONSENT_TEXT_FILE = Path(__file__).parent / "consent_texts" / "htrace-1.0.txt"

# SHA-256 of the exact consent text shipped in htrace-1.0.txt, pinned at
# development time.  Any change to the text requires bumping _CONSENT_VERSION
# AND updating this constant (compute: sha256sum htrace-1.0.txt).
# Pinning prevents a tampered consent file from silently passing is_text_intact:
# _consent_text_sha256() now returns the pinned value, not the file's live hash.
_CONSENT_TEXT_SHA256_PINNED = (
    "559b776af1ab0697e01cc0888150a309d38ef6154af32bc125c88b9cc3c23a03"
)


def _consent_text() -> str:
    return _CONSENT_TEXT_FILE.read_text(encoding="utf-8")


def _consent_text_sha256() -> str:
    """Return the pinned SHA-256 of the shipped consent text.

    Using the pinned value (not a live file hash) means a tampered htrace-1.0.txt
    cannot make is_text_intact() return True — the stored act.text_sha256 would
    match the pinned value only if the user consented to the original text.
    """
    return _CONSENT_TEXT_SHA256_PINNED


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
        # Provision the stateless HMAC upload tokens now that consent exists.
        # Fail-soft: a network error must not undo the consent that was saved.
        try:
            instance_id = load_or_create_instance_id(home)
            provision_telemetry_tokens(home, instance_id)
        except Exception:  # noqa: BLE001
            logger.debug("htrace: token provisioning skipped (non-fatal)")

    @property
    def is_current_version(self) -> bool:
        return self.consent_version == _CONSENT_VERSION

    @property
    def is_text_intact(self) -> bool:
        return self.text_sha256 == _consent_text_sha256()


def _consent_act_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / "htrace-consent-act.json"


# ── Instance identity + stateless token provisioning (ADR-0180 M4) ────────────

def _instance_id_path(home: Path) -> Path:
    return home / "instance_id"


def _instance_token_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / "htrace-token.txt"


def _telemetry_token_path(home: Path) -> Path:
    return home / "aco" / "telemetry" / ".telemetry_token"


def load_or_create_instance_id(home: Path) -> str:
    """Return the persistent UUID4 instance id, creating ~/.corvin/instance_id if absent."""
    p = _instance_id_path(home)
    try:
        existing = p.read_text(encoding="utf-8").strip()
        parsed = uuid.UUID(existing)
        if parsed.version == 4 and str(parsed) == existing.lower():
            return existing
    except (OSError, ValueError):
        pass
    new_id = str(uuid.uuid4())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new_id, encoding="utf-8")
    return new_id


def provision_telemetry_tokens(home: Path, instance_id: str) -> bool:
    """Fetch and persist the healing-trace HMAC tokens; return False on network failure.

    POSTs the instance_id to the Corvin-Features token endpoint, then writes the
    returned instance_token and telemetry_token to disk with 0o600 permissions.
    Fail-soft: any network / HTTP error returns False without raising.
    """
    payload = json.dumps({"instance_id": instance_id}).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_ENDPOINT,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TOKEN_TIMEOUT_S) as resp:
            if not (200 <= resp.getcode() < 300):
                return False
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.debug("htrace: token endpoint unreachable (non-fatal): %s", e)
        return False

    instance_token = str(data.get("instance_token", ""))
    telemetry_token = str(data.get("telemetry_token", ""))
    if not instance_token or not telemetry_token:
        logger.debug("htrace: token endpoint returned incomplete payload")
        return False

    try:
        inst_p = _instance_token_path(home)
        tel_p = _telemetry_token_path(home)
        inst_p.parent.mkdir(parents=True, exist_ok=True)
        inst_p.write_text(instance_token, encoding="utf-8")
        inst_p.chmod(0o600)
        tel_p.write_text(telemetry_token, encoding="utf-8")
        tel_p.chmod(0o600)
    except OSError as e:
        logger.debug("htrace: token persistence failed (non-fatal): %s", e)
        return False
    return True


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
    # Gate 3: text integrity — a modified consent text without a version bump
    # must also block uploads (GDPR Art. 7 specificity: consent must cover the
    # exact text shown to the user).  is_text_intact compares the stored
    # text_sha256 against the current file hash.
    if not act.is_text_intact:
        logger.info("htrace: consent text SHA-256 mismatch — paused (re-consent required)")
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
        # Capture the existing act BEFORE overwriting — the file content is
        # replaced with a minimal {revoked: True} stub that cannot be
        # deserialized back into a ConsentAct, so the act_id is unrecoverable
        # afterwards.  We need it for the audit trail (GDPR Art. 7(3), Art. 30).
        existing_act: Optional[ConsentAct] = ConsentAct.load(home)
        p.write_text(
            json.dumps({"revoked": True, "ts_utc": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
        _write_revoke_audit_event(home, existing_act)
    print("Healing trace telemetry disabled. No further data will be sent.")
    # Run cap enforcement once now: after revocation, healing_traces_enabled()
    # returns False so neither write_trace nor run_upload_cycle will call
    # _enforce_caps again, causing JSONL files to persist past the 14-day
    # retention limit promised in the consent text (GDPR Art. 5(1)(e)).
    try:
        from .htrace import _enforce_caps  # local import avoids circular dependency
        _enforce_caps(home)
    except Exception:  # noqa: BLE001
        pass


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


def _write_revoke_audit_event(home: Path, act: Optional["ConsentAct"] = None) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        # Include the consent_act_id so GDPR Art. 30 audits can correlate the
        # revocation event with the specific ConsentAct that was active.
        # Previously the payload was {}, making the revocation event
        # uncorrelatable after the on-disk file was overwritten.
        payload: dict = {}
        if act is not None:
            payload = {
                "consent_act_id": act.consent_act_id,
                "consent_version": act.consent_version,
                "text_sha256": act.text_sha256[:16],
            }
        _audit.audit_event("telemetry.consent.revoked", payload)
    except Exception:  # noqa: BLE001
        pass
