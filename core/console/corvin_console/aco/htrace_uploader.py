"""HealingTrace batch uploader — NerveFiber (ADR-0180 §2 + §3).

Fires once per day (opportunistic: at boot if a day was missed, plus scheduled
nightly window). Compresses yesterday's .jsonl, validates every record through
_assert_safe_htrace, then POSTs the bundle to the configured endpoint.

Primary target: POST /v1/telemetry/healing-traces (Corvin-Features proxy, M4).
Transparency mirror: github.com/CorvinLabs/CorvinLogs (via CORVINLOGS_GITHUB_TOKEN).

Upload is silently skipped when:
  - telemetry.healing_traces flag is not set in tenant config, OR
  - No valid ConsentAct is present (double-gate, ADR-0180 §2), OR
  - tenant_shape == "multi" (operator cannot consent for end-users).

On network failure: leave file, retry on next trigger (14-day cap).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# fcntl is POSIX-only (unavailable on Windows).  Conditional import keeps the
# module importable on all platforms; file locking is silently skipped when absent
# (Windows concurrent-upload race is a known limitation — single-instance installs
# are not affected).
try:
    import fcntl as _fcntl
    _HAS_FLOCK = True
except ImportError:
    _HAS_FLOCK = False

from .htrace import (
    _assert_safe_htrace,
    _inc_dropped,
    _enforce_caps,
    compress_for_upload,
    htrace_dir,
    _today_utc,
)
from .htrace_consent import (
    healing_traces_enabled,
    load_consent_act_id,
    load_or_create_instance_id,
)
from .nerve import NerveFiber, NerveSignal, SEVERITY_OK, SEVERITY_LOW, SEVERITY_MEDIUM

logger = logging.getLogger(__name__)

_UPLOAD_URL_DEFAULT = "https://api.corvin-labs.com/v1/telemetry/healing-traces"
_UPLOAD_TIMEOUT_S = 30
_MAX_BUNDLE_BYTES = 5 * 1024 * 1024  # 5 MB compressed
_MAX_BUNDLES_PER_DAY = 3
_LOCK_FILENAME = ".upload.lock"
_LAST_UPLOAD_FILENAME = ".last_upload"
_CORVINLOGS_REPO = "CorvinLabs/CorvinLogs"


def _home() -> Optional[Path]:
    try:
        from forge import paths as _p  # type: ignore[import]
        return _p.corvin_home()
    except Exception:  # noqa: BLE001
        return None


def _load_instance_token(home: Path) -> str:
    """Load the pre-computed instance_token issued at license time."""
    try:
        p = home / "aco" / "telemetry" / "htrace-token.txt"
        return p.read_text(encoding="utf-8").strip()[:64]
    except OSError:
        return ""


def _load_telemetry_token(home: Path) -> str:
    """Load the scoped telemetry Bearer token (scope=healing_traces, 90d TTL).

    Enforces 0o600 permissions on the token file.  ConsentAct.save() does the
    same for htrace-consent-act.json, but .telemetry_token is provisioned
    externally and may be written with a permissive umask.  A world-readable
    token would allow any local user to post forged bundles.
    """
    try:
        p = home / "aco" / "telemetry" / ".telemetry_token"
        if not p.exists():
            return ""
        try:
            if p.stat().st_mode & 0o777 != 0o600:
                p.chmod(0o600)
        except OSError:
            pass  # best-effort; proceed to read
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _load_corvinlogs_token() -> str:
    """Fine-grained GitHub PAT with contents:write on CorvinLabs/CorvinLogs."""
    return os.environ.get("CORVINLOGS_GITHUB_TOKEN", "")


def _upload_url(home: Path) -> str:
    try:
        import yaml  # type: ignore[import]
        cfg_path = home.parent.parent / "global" / "tenant.corvin.yaml"
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return data.get("telemetry", {}).get("upload_url", _UPLOAD_URL_DEFAULT)
    except Exception:  # noqa: BLE001
        pass
    return _UPLOAD_URL_DEFAULT


def _already_uploaded_today(home: Path) -> bool:
    try:
        p = htrace_dir(home) / _LAST_UPLOAD_FILENAME
        if not p.exists():
            return False
        last = p.read_text(encoding="utf-8").strip()
        today = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        parts = last.split(",")  # "<date>,<count>"
        if parts[0] == today and int(parts[1]) >= _MAX_BUNDLES_PER_DAY:
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _record_upload(home: Path) -> None:
    try:
        p = htrace_dir(home) / _LAST_UPLOAD_FILENAME
        today = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        existing = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        parts = existing.split(",")
        if parts[0] == today:
            count = int(parts[1]) + 1
        else:
            count = 1
        p.write_text(f"{today},{count}", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _validate_bundle(gz_path: Path) -> tuple[bool, int]:
    """Validate all records in a .jsonl.gz bundle. Returns (ok, count).

    Drops records that fail _assert_safe_htrace. Returns False if the bundle
    cannot be read or is too large.

    stat() is inside the try block so that a FileNotFoundError (file deleted
    between compress_for_upload and _validate_bundle) is caught and returns
    (False, 0) instead of propagating to the outer except in run_upload_cycle
    and incorrectly returning 'error'.
    """
    count = 0
    try:
        size = gz_path.stat().st_size
        if size > _MAX_BUNDLE_BYTES:
            logger.warning("htrace: bundle too large (%d bytes) — skipping", size)
            return False, 0
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    _assert_safe_htrace(record)
                    count += 1
                except (json.JSONDecodeError, ValueError) as e:
                    logger.debug("htrace: invalid record dropped during validation: %s", e)
        return True, count
    except Exception as e:  # noqa: BLE001
        logger.warning("htrace: bundle validation error: %s", e)
        return False, 0


def _post_bundle(
    gz_path: Path,
    *,
    upload_url: str,
    bearer_token: str,
    instance_token: str,
    instance_id: str,
    consent_act_id: str,
) -> bool:
    """POST the bundle. Returns True on 2xx, False on error."""
    if not upload_url.lower().startswith("https://"):
        logger.warning("htrace: upload_url must be https:// — skipping")
        return False
    try:
        data = gz_path.read_bytes()
        req = urllib.request.Request(
            upload_url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/x-ndjson+gzip",
                "Authorization": f"Bearer {bearer_token}",
                "X-HTTrace-Schema": "htrace/1",
                "X-HTTrace-Instance-Token": instance_token,
                "X-HTrace-Instance-Id": instance_id,
                "X-HTTrace-Consent-Act-Id": consent_act_id,
            },
        )
        with urllib.request.urlopen(req, timeout=_UPLOAD_TIMEOUT_S) as resp:
            status = resp.getcode()
            if 200 <= status < 300:
                logger.info("htrace: bundle uploaded (%s → %d)", gz_path.name, status)
                return True
            logger.warning("htrace: upload returned %d — will retry", status)
            return False
    except Exception as e:  # noqa: BLE001
        logger.info("htrace: upload failed (will retry tomorrow): %s", e)
        return False


def _push_to_corvinlogs(gz_path: Path, *, instance_token: str) -> bool:
    """Transparency mirror: push bundle to CorvinLabs/CorvinLogs via GitHub API.

    Uses CORVINLOGS_GITHUB_TOKEN env var (fine-grained PAT, contents:write).
    Silent no-op when token is absent.
    """
    token = _load_corvinlogs_token()
    if not token:
        return False
    try:
        import base64

        data = gz_path.read_bytes()
        if len(data) > _MAX_BUNDLE_BYTES:
            return False

        # File path in repo: traces/htrace-v1/<date>/<token_prefix>_<date>.jsonl.gz
        stem = gz_path.stem.replace(".jsonl", "")  # YYYY-MM-DD
        token_prefix = instance_token[:12] if instance_token else "unknown"
        repo_path = f"traces/htrace-v1/{stem}/{token_prefix}_{stem}.jsonl.gz"

        payload = json.dumps({
            "message": f"chore: add healing trace bundle {stem}",
            "content": base64.b64encode(data).decode("ascii"),
            "branch": "main",
        }).encode("utf-8")

        api_url = f"https://api.github.com/repos/{_CORVINLOGS_REPO}/contents/{repo_path}"
        req = urllib.request.Request(
            api_url,
            data=payload,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "CorvinOS-HealingTrace/1",
            },
        )
        with urllib.request.urlopen(req, timeout=_UPLOAD_TIMEOUT_S) as resp:
            status = resp.getcode()
            ok = 200 <= status < 300
            if ok:
                logger.info("htrace: pushed to CorvinLogs (%s)", repo_path)
            return ok
    except Exception as e:  # noqa: BLE001
        logger.debug("htrace: CorvinLogs mirror failed (non-fatal): %s", e)
        return False


def _write_upload_audit_event(outcome: str, count: int) -> None:
    try:
        from forge import audit as _audit  # type: ignore[import]
        event = "htrace.upload.sent" if outcome == "sent" else f"htrace.upload.{outcome}"
        _audit.audit_event(event, {"record_count": count})
    except Exception:  # noqa: BLE001
        pass


def run_upload_cycle(home: Path) -> tuple[str, int]:
    """Run one upload cycle. Returns (outcome, record_count).

    outcome: "sent" | "skipped" | "error" | "no_bundle" | "already_done"
    Acquires a file lock to prevent concurrent runs.
    """
    lock_file = htrace_dir(home) / _LOCK_FILENAME
    # Open the lock file before entering the main try block so we can close
    # it in a dedicated except branch if flock raises.  Without this the FD
    # leaks on every concurrent invocation because the finally block of the
    # inner try is never entered when flock raises before it is reached.
    # Double-gate: both YAML flag and ConsentAct must be present.
    # scan() also checks this, but run_upload_cycle() is a public function —
    # direct callers (e.g. maintainer_cli upload) must not bypass consent.
    if not healing_traces_enabled(home):
        logger.debug("htrace: upload skipped — consent gate not active")
        return "skipped", 0

    lf = None
    try:
        htrace_dir(home).mkdir(parents=True, exist_ok=True)
        lf = lock_file.open("w")
        if _HAS_FLOCK:
            _fcntl.flock(lf, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        logger.debug("htrace: upload already running (lock held)")
        if lf is not None:
            try:
                lf.close()
            except Exception:  # noqa: BLE001
                pass
        return "skipped", 0

    try:
        if _already_uploaded_today(home):
            return "already_done", 0

        _enforce_caps(home)

        # Compress yesterday's file
        gz = compress_for_upload(home)
        if gz is None:
            # Try the day before (startup catch-up for missed days)
            for delta in range(2, 8):
                date_str = (datetime.now(timezone.utc) - timedelta(days=delta)).strftime("%Y-%m-%d")
                gz = compress_for_upload(home, date_str=date_str)
                if gz is not None:
                    break

        if gz is None:
            _write_upload_audit_event("skipped", 0)
            return "no_bundle", 0

        ok, count = _validate_bundle(gz)
        if not ok or count == 0:
            # Move the invalid/empty bundle to sent/ so it is still subject to
            # the 14-day cap.  Ensure sent/ exists first — on a fresh install
            # (or after the directory was removed) it doesn't exist yet, and
            # Path.replace() would raise FileNotFoundError which is then
            # swallowed by the outer except, leaving the gz as an orphan.
            skip_dir = htrace_dir(home) / "sent"
            skip_dir.mkdir(exist_ok=True)
            gz.replace(skip_dir / gz.name)
            _write_upload_audit_event("skipped", 0)
            return "skipped", 0

        bearer = _load_telemetry_token(home)
        inst_token = _load_instance_token(home)
        instance_id = load_or_create_instance_id(home)
        consent_act_id = load_consent_act_id(home)
        url = _upload_url(home)

        # Create sent/ before posting so a disk-full / permission error on
        # mkdir doesn't cause a duplicate POST on the next cycle.  If mkdir
        # fails here we abort cleanly before touching the network.
        sent_dir = htrace_dir(home) / "sent"
        sent_dir.mkdir(exist_ok=True)

        sent = False
        if bearer:
            sent = _post_bundle(
                gz,
                upload_url=url,
                bearer_token=bearer,
                instance_token=inst_token,
                instance_id=instance_id,
                consent_act_id=consent_act_id,
            )

        if sent:
            # Only push to CorvinLogs after primary upload confirmed — prevents
            # data appearing on the public mirror without auth/audit trail.
            cl_token = _load_corvinlogs_token()
            if cl_token:
                mirror_ok = _push_to_corvinlogs(gz, instance_token=inst_token)
                if not mirror_ok:
                    logger.warning("htrace: CorvinLogs mirror failed — bundle kept locally")
                    try:
                        from forge import audit as _audit  # type: ignore[import]
                        _audit.audit_event("htrace.mirror.failed", {"bundle": gz.name})
                    except Exception:  # noqa: BLE001
                        pass
            gz.replace(sent_dir / gz.name)
            _record_upload(home)
            _write_upload_audit_event("sent", count)
            return "sent", count
        else:
            # Leave file for retry (up to 14-day cap)
            _write_upload_audit_event("error", 0)
            return "error", 0

    except Exception as e:  # noqa: BLE001
        logger.warning("htrace: upload cycle error: %s", e)
        _write_upload_audit_event("error", 0)
        return "error", 0
    finally:
        try:
            if lf is not None:
                if _HAS_FLOCK:
                    _fcntl.flock(lf, _fcntl.LOCK_UN)
                lf.close()
            lock_file.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ── NerveFiber ────────────────────────────────────────────────────────────────

class HealingTraceUploaderFiber(NerveFiber):
    """Daily healing-trace batch uploader (ADR-0180 M2+M3).

    Fires at boot if yesterday's bundle wasn't sent, and opportunistically
    when the scheduled nightly window passes (checked by last-upload stamp).
    Silent no-op when consent is not active.
    """
    fiber_id = "htrace.uploader"
    fiber_version = "1.0.0"
    fiber_description = "ADR-0180: täglicher Healing-Trace-Upload (opt-in, deny-by-default)"

    def scan(self) -> list[NerveSignal]:
        home = _home()
        if home is None:
            return []

        if not healing_traces_enabled(home):
            return []

        if _already_uploaded_today(home):
            return []

        outcome, count = run_upload_cycle(home)

        if outcome == "sent":
            return [NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="htrace.upload.sent",
                severity=SEVERITY_OK,
                message=f"Healing-Traces hochgeladen ({count} Records)",
                data={"record_count": count},
                audit=True,
            )]
        elif outcome == "no_bundle":
            return []
        elif outcome == "error":
            return [NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="htrace.upload.error",
                severity=SEVERITY_LOW,
                message="Healing-Trace-Upload fehlgeschlagen (wird morgen wiederholt)",
                data={},
                repair_hint="CORVINLOGS_GITHUB_TOKEN prüfen oder Netzwerk",
                audit=True,
            )]
        return []
