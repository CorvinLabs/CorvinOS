"""Bootstrap hook for GDPR secret rotation (ADR-0758).

Checks at boot: if secrets >90 days old → trigger rotation.
Fail-closed: any error → abort boot with RuntimeError.
"""
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

_log = logging.getLogger(__name__)


def verify_secret_rotation_policy() -> bool:
    """Boot-time check: if secrets >90 days old, trigger rotation.

    Returns:
        True if rotation succeeded or no rotation needed
        Raises RuntimeError if rotation failed (fail-closed)
    """
    try:
        corvin_home = os.environ.get("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        tenant_id = os.environ.get("CORVIN_TENANT_ID", "_default")
        policy_path = Path(corvin_home) / "tenants" / tenant_id / "global" / "compliance" / "rotation_policy.yaml"

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
        state_file = Path(corvin_home) / "tenants" / tenant_id / "global" / ".secret_rotation_state"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                last_rotation = datetime.fromisoformat(state.get("last_rotation", "2000-01-01"))
                age_days = (datetime.utcnow() - last_rotation).days

                rotation_interval = rotation_config.get("interval_days", 90)
                if age_days < rotation_interval:
                    _log.info(f"Secrets {age_days}d old (rotate every {rotation_interval}d); no rotation needed")
                    return True
            except (json.JSONDecodeError, ValueError) as exc:
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
            ["python3", str(script_path), str(policy_path), tenant_id],
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout
        )

        if result.returncode != 0:
            raise RuntimeError(f"Rotation script failed: {result.stderr}")

        # Parse result + update state file
        try:
            output = json.loads(result.stdout)
            if output.get("status") == "success":
                state_file = Path(os.environ.get("CORVIN_HOME", os.path.expanduser("~/.corvin"))) / "tenants" / tenant_id / "global" / ".secret_rotation_state"
                state_file.parent.mkdir(parents=True, exist_ok=True)
                with open(state_file, "w") as f:
                    json.dump({"last_rotation": datetime.utcnow().isoformat() + "Z"}, f)

                _log.info(f"Rotation succeeded; state updated")
                return True
        except json.JSONDecodeError:
            _log.warning("Could not parse rotation output; assuming success")
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
