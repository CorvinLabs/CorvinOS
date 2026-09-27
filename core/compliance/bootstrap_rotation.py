"""Bootstrap hook for GDPR secret rotation (ADR-0758).

Checks at boot: if secrets >90 days old → trigger rotation.
Fail-closed: any error → abort boot with RuntimeError.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
rotation script it runs (scripts/rotate_corvin_keys_gdpr.py) is itself defused
and exits 2, so a due rotation fails closed here.
"""
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _tenant_global(tenant_id=None) -> Path:
    """``<corvin_home>/tenants/<tid>/global`` — CORVIN_HOME honoured, id validated."""
    from forge import paths as forge_paths  # type: ignore[import-not-found]

    return forge_paths.tenant_global_dir(tenant_id)


def _parse_utc(value: str) -> datetime:
    """ISO-8601 → aware UTC datetime (a naive value is taken as UTC)."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

_log = logging.getLogger(__name__)


def verify_secret_rotation_policy() -> bool:
    """Boot-time check: if secrets >90 days old, trigger rotation.

    Returns:
        True if rotation succeeded or no rotation needed
        Raises RuntimeError if rotation failed (fail-closed)
    """
    try:
        tenant_global = _tenant_global()
        tenant_id = tenant_global.parent.name
        policy_path = tenant_global / "compliance" / "rotation_policy.yaml"

        # Check if rotation is enabled
        if not policy_path.exists():
            _log.info("Secret rotation policy not found; skipping")
            return True

        # Load policy
        try:
            import yaml
            with open(policy_path) as f:
                policy = yaml.safe_load(f)
        except ImportError:
            _log.warning("PyYAML not available; skipping rotation check")
            return True

        rotation_config = policy.get("rotation", {})
        if not rotation_config.get("enabled", False):
            _log.info("Secret rotation disabled in policy")
            return True

        # Check secret age
        state_file = tenant_global / ".secret_rotation_state"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                last_rotation = _parse_utc(state.get("last_rotation", "2000-01-01"))
                age_days = (datetime.now(timezone.utc) - last_rotation).days

                rotation_interval = rotation_config.get("interval_days", 90)
                if age_days < rotation_interval:
                    _log.info(f"Secrets {age_days}d old (rotate every {rotation_interval}d); no rotation needed")
                    return True
            except (json.JSONDecodeError, ValueError) as exc:
                # Unknown age is treated as "due" (fail-closed), never as fresh.
                _log.warning(f"Could not parse state: {exc}")

        # Secrets are old; trigger rotation
        _log.info("Secrets >90 days old; triggering GDPR rotation")
        return trigger_rotation(policy_path, tenant_id)

    except RuntimeError:
        raise  # Re-raise boot failures
    except Exception as exc:
        _log.error(f"Secret rotation check failed: {exc}")
        raise RuntimeError(f"Boot rotation check failed (fail-closed): {exc}") from exc


def trigger_rotation(policy_path: Path, tenant_id: str) -> bool:
    """Trigger the rotation script.

    Args:
        policy_path: Path to rotation policy YAML
        tenant_id: Tenant ID for audit trail

    Returns:
        True if rotation succeeded
        Raises RuntimeError if rotation failed
    """
    try:
        script_path = Path(__file__).parent.parent.parent / "scripts" / "rotate_corvin_keys_gdpr.py"

        if not script_path.exists():
            raise FileNotFoundError(f"Rotation script not found: {script_path}")

        _log.info(f"Running rotation script: {script_path}")
        result = subprocess.run(
            [sys.executable, str(script_path), str(policy_path), tenant_id],
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout
        )

        if result.returncode != 0:
            raise RuntimeError(f"Rotation script failed: {result.stderr}")

        # Parse result + update state file. Fail-closed: only an explicit
        # {"status": "success"} counts; unparseable output is a failure.
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Rotation output unparseable — not treated as success") from exc
        if not isinstance(output, dict) or output.get("status") != "success":
            raise RuntimeError("Rotation script did not report success")
        state_file = _tenant_global(tenant_id) / ".secret_rotation_state"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump({"last_rotation": datetime.now(timezone.utc).isoformat()}, f)
        _log.info("Rotation succeeded; state updated")
        return True

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Rotation script timed out: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Rotation trigger failed: {exc}") from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        success = verify_secret_rotation_policy()
        print(f"Rotation check: {'OK' if success else 'FAILED'}")
    except RuntimeError as exc:
        print(f"Rotation check FAILED: {exc}")
        exit(1)
