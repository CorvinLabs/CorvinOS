#!/usr/bin/env python3
"""
Blocker 3: Corvin-Keys Secret Rotation — Phase 2 & 3 Automation
(Phase 1: Manual Revocation via Service Dashboards — NOT AUTOMATED)

Automated Phases:
- Phase 2: Update .env, service.env, secrets.json with placeholders
- Phase 3: Emit audit trail events (hash-chained)

PREREQUISITES:
✅ Phase 1 (Manual Revocation) must be completed FIRST:
   - Revoke GitHub PATs: https://github.com/settings/tokens
   - Revoke Hetzner tokens: https://console.hetzner.cloud/account/security/api-tokens
   - Revoke Cloudflare tokens: https://dash.cloudflare.com/profile/api-tokens
   - Delete OpenAI keys: https://platform.openai.com/account/api-keys
   - Remove Gmail app password: https://myaccount.google.com/apppasswords
   - Delete PyPI token: https://pypi.org/account/settings/
   - Delete Resend key: https://resend.com/settings/api-keys
   - Regenerate Ollama locally

USAGE:
    python3 scripts/rotate_corvin_keys_blocker3.py --confirm

ADR-0760: Audit trail + hash-chain
GDPR Art. 32: Data security (credential rotation)
"""

import json
import os
import sys
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from hashlib import sha256


# Credentials to rotate (14 items from BLOCKER_3_ROTATION_PLAN.md)
CREDENTIALS_TO_ROTATE = {
    ".env": {
        "GITHUB_TOKEN": "ghp_PLACEHOLDER_CorvinOS_{timestamp}",
        "HETZNER_API_TOKEN": "PLACEHOLDER_Hetzner_Token_{timestamp}",
        "HETZNER_ROOT_PASSWORT": "PLACEHOLDER_Hetzner_SSH_{timestamp}",
        "CLOUDFLARE_ID": "PLACEHOLDER_Cloudflare_ID_{timestamp}",
        "CLOUDFLARE_API_TOKEN": "PLACEHOLDER_Cloudflare_Token_{timestamp}",
        "PYPI_TOKEN": "PLACEHOLDER_PyPI_Token_{timestamp}",
        "RESEND_API_KEY": "PLACEHOLDER_Resend_Key_{timestamp}",
    },
    "~/.config/corvin-voice/service.env": {
        "CORVIN_TTS_OPENAI_KEY": "sk-proj-PLACEHOLDER-TTS-{timestamp}",
        "CORVIN_STT_OPENAI_KEY": "sk-proj-PLACEHOLDER-STT-{timestamp}",
        "OPENAI_API_KEY": "sk-proj-PLACEHOLDER-CorvinOS-{timestamp}",
        "GMAIL_APP_PASSWORD": "PLACEHOLDER_Gmail_Password_{timestamp}",
        "OLLAMA_API_KEY": "PLACEHOLDER_Ollama_Key_{timestamp}",
    },
    "~/.config/corvin-voice/secrets.json": {
        "HETZNER_API_TOKEN": "PLACEHOLDER_Hetzner_Token_{timestamp}",
        "HETZNER_SSH_KEY_NAME": "PLACEHOLDER_Hetzner_SSH_Name_{timestamp}",
    },
}


class CredentialRotator:
    """Automate credential rotation with audit trail."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.backup_path = Path(f"/tmp/credential_backup_{self.timestamp}.json")
        self.audit_events = []
        self.repo_root = Path(__file__).parent.parent

    def backup_credentials(self) -> bool:
        """Backup current credentials before rotation."""
        print("📋 [Phase 2.1] Backing up current credentials...")

        backup_data = {}
        for file_path_str in CREDENTIALS_TO_ROTATE.keys():
            file_path = Path(file_path_str.replace("~", str(Path.home())))

            if not file_path.exists():
                print(f"   ⚠️  File not found: {file_path} (skipping)")
                continue

            try:
                with open(file_path, "r") as f:
                    backup_data[file_path_str] = f.read()
                print(f"   ✅ Backed up: {file_path}")
            except PermissionError:
                print(f"   ❌ Permission denied: {file_path}")
                return False

        if self.dry_run:
            print(f"   [DRY-RUN] Would save to: {self.backup_path}")
            return True

        try:
            with open(self.backup_path, "w") as f:
                json.dump(backup_data, f, indent=2)
            os.chmod(self.backup_path, 0o600)  # Only owner can read
            print(f"   ✅ Backup saved: {self.backup_path} (mode 0600)")
            return True
        except Exception as e:
            print(f"   ❌ Backup failed: {e}")
            return False

    def rotate_credentials(self) -> bool:
        """Replace credentials with placeholders."""
        print("🔄 [Phase 2.2] Rotating credentials...")

        for file_path_str, creds_dict in CREDENTIALS_TO_ROTATE.items():
            file_path = Path(file_path_str.replace("~", str(Path.home())))

            if not file_path.exists():
                print(f"   ⚠️  File not found: {file_path} (skipping)")
                continue

            print(f"   📝 Processing: {file_path}")

            try:
                with open(file_path, "r") as f:
                    content = f.read()

                # Replace each credential
                for key, placeholder_template in creds_dict.items():
                    placeholder = placeholder_template.format(timestamp=self.timestamp)

                    # Find and replace the key=value line
                    lines = content.split("\n")
                    found = False
                    for i, line in enumerate(lines):
                        if line.startswith(f"{key}="):
                            lines[i] = f"{key}={placeholder}"
                            found = True
                            self.audit_events.append({
                                "event_type": "credential_rotated",
                                "service": key,
                                "file_path": file_path_str,
                                "timestamp": self.timestamp,
                                "status": "rotated",
                            })
                            print(f"      ✅ Rotated: {key}")
                            break

                    if not found:
                        self.audit_events.append({
                            "event_type": "credential_rotation_skipped",
                            "service": key,
                            "file_path": file_path_str,
                            "timestamp": self.timestamp,
                            "reason": "key not found in file",
                        })
                        print(f"      ⚠️  Key not found: {key} (skipping)")

                    content = "\n".join(lines)

                if self.dry_run:
                    print(f"   [DRY-RUN] Would update: {file_path}")
                else:
                    with open(file_path, "w") as f:
                        f.write(content)
                    print(f"   ✅ Updated: {file_path}")

            except Exception as e:
                print(f"   ❌ Error processing {file_path}: {e}")
                return False

        return True

    def emit_audit_events(self) -> bool:
        """Emit audit trail events (hash-chained)."""
        print("📊 [Phase 3] Emitting audit events...")

        audit_file = self.repo_root / ".corvin" / "tenants" / "_default" / "global" / "audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Read existing audit trail (for hash-chaining)
            prev_hash = "0" * 64  # Initial hash
            if audit_file.exists():
                with open(audit_file, "r") as f:
                    for line in f:
                        if line.strip():
                            last_event = json.loads(line)
                            prev_hash = last_event.get("hash", "0" * 64)

            # Emit events
            for event in self.audit_events:
                event_data = {
                    "event_type": event["event_type"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "tenant_id": "_default",
                    "service": event.get("service", ""),
                    "file_path": event.get("file_path", ""),
                    "status": event.get("status", ""),
                    "reason": event.get("reason", ""),
                    "prev_hash": prev_hash,
                }

                # Compute hash
                event_str = json.dumps(event_data, sort_keys=True)
                event_data["hash"] = sha256(event_str.encode()).hexdigest()

                if self.dry_run:
                    print(f"   [DRY-RUN] Would emit: {event_data['event_type']} ({event_data['service']})")
                else:
                    with open(audit_file, "a") as f:
                        f.write(json.dumps(event_data) + "\n")
                    print(f"   ✅ Emitted: {event_data['event_type']} ({event_data['service']})")

                prev_hash = event_data["hash"]

            print(f"   ✅ Audit trail updated: {audit_file}")
            return True

        except Exception as e:
            print(f"   ❌ Audit emission failed: {e}")
            return False

    def verify_rotation(self) -> bool:
        """Verify no real credentials remain."""
        print("✅ [Verification] Checking for remaining credentials...")

        dangerous_patterns = [
            "ghp_",  # GitHub token
            "sk-proj-",  # OpenAI key (real starts with this)
            "htz_",  # Hetzner token
            "CLOUDFLARE",  # Cloudflare pattern
        ]

        found_issues = False
        for file_path_str in CREDENTIALS_TO_ROTATE.keys():
            file_path = Path(file_path_str.replace("~", str(Path.home())))

            if not file_path.exists():
                continue

            try:
                with open(file_path, "r") as f:
                    for line_no, line in enumerate(f, 1):
                        for pattern in dangerous_patterns:
                            if pattern in line and "PLACEHOLDER" not in line:
                                print(f"   ❌ Potential credential at {file_path}:{line_no}")
                                print(f"      {line[:80]}...")
                                found_issues = True

            except Exception as e:
                print(f"   ⚠️  Could not read {file_path}: {e}")

        if not found_issues:
            print("   ✅ No remaining credentials detected")
            return True
        else:
            print("   ❌ Found potential credentials — manual review required")
            return False

    def run(self) -> bool:
        """Execute full rotation workflow."""
        print("=" * 70)
        print("BLOCKER 3: Secret Rotation (Phase 2–3 Automation)")
        print("=" * 70)
        print()

        if self.dry_run:
            print("🏃 DRY-RUN MODE (no files will be modified)")
            print()

        # Check prerequisite
        print("⚠️  PREREQUISITE CHECK:")
        print("   ✅ Phase 1 (Manual Revocation) MUST be completed before proceeding:")
        print("      - GitHub: https://github.com/settings/tokens")
        print("      - Hetzner: https://console.hetzner.cloud/account/security/api-tokens")
        print("      - Cloudflare: https://dash.cloudflare.com/profile/api-tokens")
        print("      - OpenAI: https://platform.openai.com/account/api-keys")
        print("      - Gmail: https://myaccount.google.com/apppasswords")
        print("      - PyPI: https://pypi.org/account/settings/")
        print("      - Resend: https://resend.com/settings/api-keys")
        print("      - Ollama: (regenerate locally)")
        print()

        steps = [
            ("Backup Credentials", self.backup_credentials),
            ("Rotate Credentials", self.rotate_credentials),
            ("Emit Audit Events", self.emit_audit_events),
            ("Verify Rotation", self.verify_rotation),
        ]

        for step_name, step_func in steps:
            if not step_func():
                print(f"❌ {step_name} FAILED")
                return False

        print()
        print("=" * 70)
        print("✅ BLOCKER 3 ROTATION COMPLETE")
        print("=" * 70)
        print()
        print("Summary:")
        print(f"  📋 Backup: {self.backup_path}")
        print(f"  🔄 Credentials rotated: {len(self.audit_events)}")
        print(f"  📊 Audit events: {len([e for e in self.audit_events if e['event_type'] == 'credential_rotated'])}")
        print()
        print("Next steps:")
        print("  1. Verify: git status (check .env is in .gitignore)")
        print("  2. Confirm: All services reject old credentials (401 Unauthorized)")
        print("  3. Commit: Rotation documented in this session")
        print()

        return True


def main():
    parser = argparse.ArgumentParser(description="Rotate Corvin credentials (Phase 2–3)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying files")
    parser.add_argument("--confirm", action="store_true", help="Confirm execution (required for real run)")

    args = parser.parse_args()

    if not args.confirm and not args.dry_run:
        print("❌ SAFETY CHECK: Must use --confirm or --dry-run")
        print("   Usage: python3 scripts/rotate_corvin_keys_blocker3.py --dry-run")
        print("   Or:    python3 scripts/rotate_corvin_keys_blocker3.py --confirm")
        sys.exit(1)

    rotator = CredentialRotator(dry_run=args.dry_run)
    success = rotator.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
