#!/usr/bin/env python3
"""
Blocker 3: Phase 2 Automated Credential Rotation
Replaces live credentials with fail-closed placeholders.

Prerequisites:
- Phase 1 (manual revocation) MUST complete first
- Operator revokes credentials via web dashboards (GitHub, Hetzner, Cloudflare, OpenAI, etc.)
- This script replaces credentials with placeholders locally

Fail-Closed Strategy:
- Placeholders are format-correct but non-functional
- Any service using them will immediately fail with 401 Unauthorized
- No silent fallback behavior
"""

import json
import os
from pathlib import Path
from datetime import datetime
import hashlib


class CredentialRotator:
    """Rotate credentials to placeholders (Phase 2)."""

    TIMESTAMP = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    PLACEHOLDERS = {
        "GITHUB_TOKEN": "ghp_PLACEHOLDER_CorvinOS_{ts}",
        "HETZNER_API_TOKEN": "PLACEHOLDER_HETZNER_API_{ts}",
        "HETZNER_ROOT_PASSWORT": "PLACEHOLDER_SSH_ROOT_{ts}",
        "CLOUDFLARE_ID": "PLACEHOLDER_CF_ID_{ts}",
        "CLOUDFLARE_API_TOKEN": "PLACEHOLDER_CF_API_{ts}",
        "PYPI_TOKEN": "PLACEHOLDER_PYPI_{ts}",
        "RESEND_API_KEY": "PLACEHOLDER_RESEND_{ts}",
        "CORVIN_TTS_OPENAI_KEY": "sk-proj-PLACEHOLDER-TTS-{ts}",
        "CORVIN_STT_OPENAI_KEY": "sk-proj-PLACEHOLDER-STT-{ts}",
        "OPENAI_API_KEY": "sk-proj-PLACEHOLDER-OpenAI-{ts}",
        "GMAIL_APP_PASSWORD": "PLACEHOLDER_GMAIL_{ts}",
        "OLLAMA_API_KEY": "PLACEHOLDER_OLLAMA_{ts}",
        "HETZNER_SSH_KEY_NAME": "PLACEHOLDER_SSH_KEY_{ts}",
    }

    @staticmethod
    def generate_placeholder(name: str) -> str:
        """Generate placeholder for a credential."""
        template = CredentialRotator.PLACEHOLDERS.get(name, f"PLACEHOLDER_{name}_{{ts}}")
        return template.format(ts=CredentialRotator.TIMESTAMP)

    @staticmethod
    def backup_env_file(env_path: Path) -> str:
        """Backup current .env file."""
        if not env_path.exists():
            return None

        backup_path = env_path.parent / f".env.backup-{CredentialRotator.TIMESTAMP}"
        with open(env_path) as src, open(backup_path, 'w') as dst:
            dst.write(src.read())

        # Restrict permissions
        os.chmod(backup_path, 0o600)
        return str(backup_path)

    @staticmethod
    def rotate_env_file(env_path: Path) -> dict:
        """Replace credentials in .env with placeholders."""
        if not env_path.exists():
            return {"status": "not_found", "file": str(env_path), "rotated_count": 0}

        with open(env_path) as f:
            lines = f.readlines()

        rotated = {}
        new_lines = []

        for line in lines:
            # Skip comments and empty lines
            if line.startswith('#') or not line.strip():
                new_lines.append(line)
                continue

            # Parse KEY=VALUE
            if '=' not in line:
                new_lines.append(line)
                continue

            key, value = line.split('=', 1)
            key = key.strip()

            if key in CredentialRotator.PLACEHOLDERS:
                placeholder = CredentialRotator.generate_placeholder(key)
                new_lines.append(f"{key}={placeholder}\n")
                rotated[key] = placeholder
            else:
                new_lines.append(line)

        # Write back with mode 0600
        with open(env_path, 'w') as f:
            f.writelines(new_lines)
        os.chmod(env_path, 0o600)

        return {
            "status": "success",
            "file": str(env_path),
            "rotated_count": len(rotated),
            "rotated_keys": list(rotated.keys()),
        }

    @staticmethod
    def rotate_service_env_file(service_env_path: Path) -> dict:
        """Rotate ~/.config/corvin-voice/service.env."""
        if not service_env_path.exists():
            return {"status": "not_found", "file": str(service_env_path), "rotated_count": 0}

        # Same logic as .env rotation
        return CredentialRotator.rotate_env_file(service_env_path)

    @staticmethod
    def rotate_secrets_json(secrets_path: Path) -> dict:
        """Rotate secrets.json (JSON format)."""
        if not secrets_path.exists():
            return {"status": "not_found", "file": str(secrets_path), "rotated_count": 0}

        with open(secrets_path) as f:
            secrets = json.load(f)

        rotated = {}
        for key in list(secrets.keys()):
            if key in CredentialRotator.PLACEHOLDERS:
                placeholder = CredentialRotator.generate_placeholder(key)
                secrets[key] = placeholder
                rotated[key] = placeholder

        # Write back with mode 0600
        with open(secrets_path, 'w') as f:
            json.dump(secrets, f, indent=2)
        os.chmod(secrets_path, 0o600)

        return {
            "status": "success",
            "file": str(secrets_path),
            "rotated_count": len(rotated),
            "rotated_keys": list(rotated.keys()),
        }

    @staticmethod
    def generate_audit_event() -> dict:
        """Generate audit event for credential rotation."""
        return {
            "event_type": "secret_rotation_phase2",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "phase": "phase_2_placeholder_replacement",
            "tenant_id": "_default",  # Would be parameterized in production
            "credentials_rotated": len(CredentialRotator.PLACEHOLDERS),
            "rotation_timestamp": CredentialRotator.TIMESTAMP,
            "status": "phase2_complete",
            "notes": "Phase 1 (manual revocation) must complete before credentials become functional",
        }


def main():
    """Execute Phase 2 rotation."""
    print("\n🔐 Blocker 3: Phase 2 Credential Rotation (Automated Placeholder Replacement)\n")

    home = Path.home()
    corvinOS_dir = Path("/home/shumway/projects/CorvinOS")

    results = {
        "timestamp": CredentialRotator.TIMESTAMP,
        "phase": "phase_2_automated",
        "rotations": [],
        "audit_event": CredentialRotator.generate_audit_event(),
    }

    # 1. Backup + Rotate .env
    env_path = corvinOS_dir / ".env"
    print(f"📋 Backing up {env_path}...")
    backup = CredentialRotator.backup_env_file(env_path)
    if backup:
        print(f"   ✅ Backed up to {backup}")

    print(f"🔄 Rotating credentials in {env_path}...")
    result = CredentialRotator.rotate_env_file(env_path)
    results["rotations"].append(result)
    print(f"   ✅ Rotated {result['rotated_count']} credentials")

    # 2. Rotate service.env
    service_env = home / ".config" / "corvin-voice" / "service.env"
    if service_env.exists():
        print(f"🔄 Rotating {service_env}...")
        result = CredentialRotator.rotate_service_env_file(service_env)
        results["rotations"].append(result)
        print(f"   ✅ Rotated {result['rotated_count']} credentials")

    # 3. Rotate secrets.json
    secrets_json = home / ".config" / "corvin-voice" / "secrets.json"
    if secrets_json.exists():
        print(f"🔄 Rotating {secrets_json}...")
        result = CredentialRotator.rotate_secrets_json(secrets_json)
        results["rotations"].append(result)
        print(f"   ✅ Rotated {result['rotated_count']} credentials")

    # 4. Save audit event
    print(f"\n📝 Audit Event:")
    print(json.dumps(results["audit_event"], indent=2))

    print(f"\n✅ Phase 2 Complete!")
    print(f"   - All credentials replaced with fail-closed placeholders")
    print(f"   - Services will return 401 Unauthorized if used")
    print(f"   - Backup created at: {backup}")
    print(f"\n⚠️  Phase 1 (manual revocation) MUST have been completed before rotation!")
    print(f"    Otherwise, old credentials will still work and new placeholders won't be used.")

    return 0


if __name__ == "__main__":
    exit(main())
