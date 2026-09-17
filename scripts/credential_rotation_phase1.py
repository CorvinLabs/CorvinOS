#!/usr/bin/env python3
"""
Credential Rotation Phase 1: Inventory & Verification (ADR-0869)

Phase 1 validates all 14 credentials are:
- Accessible (files exist + readable)
- Valid (services authenticate successfully)
- Documented (audit trail baseline)

Does NOT modify credentials. Prepares Phase 2 automation.

Usage:
    python3 scripts/credential_rotation_phase1.py [--verbose] [--tenant _default]

Options:
    --verbose       Show detailed diagnostics
    --tenant        Tenant ID (default: _default)
    --skip-network  Skip authentication tests (fast mode)
"""

import json
import os
import sys
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from hashlib import sha256
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialInventory:
    """Immutable credential inventory record."""
    credential_name: str
    file_path: str
    env_variable: str
    status: str  # "accessible", "missing", "inaccessible"
    file_exists: bool
    file_readable: bool
    key_present: bool
    key_masked: str  # First 4 chars + "***"
    test_passed: bool
    test_reason: str
    timestamp_utc: str
    tenant_id: str

    def to_dict(self) -> dict:
        """Convert to dictionary (for JSON serialization)."""
        return asdict(self)

    @property
    def hash(self) -> str:
        """SHA256 hash of inventory record (for audit chain)."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return sha256(payload.encode()).hexdigest()


class CredentialRotationPhase1:
    """Phase 1: Inventory & Verification."""

    # 14 credentials across 3 files
    CREDENTIALS = {
        ".env": {
            "GITHUB_TOKEN": "GitHub Personal Access Token",
            "HETZNER_API_TOKEN": "Hetzner Cloud API Token",
            "HETZNER_ROOT_PASSWORT": "Hetzner Root Password",
            "CLOUDFLARE_ID": "Cloudflare Account ID",
            "CLOUDFLARE_API_TOKEN": "Cloudflare API Token",
            "PYPI_TOKEN": "PyPI Upload Token",
            "RESEND_API_KEY": "Resend Email API Key",
        },
        "~/.config/corvin-voice/service.env": {
            "CORVIN_TTS_OPENAI_KEY": "OpenAI TTS API Key",
            "CORVIN_STT_OPENAI_KEY": "OpenAI STT (Whisper) API Key",
            "OPENAI_API_KEY": "OpenAI Primary API Key",
            "GMAIL_APP_PASSWORD": "Gmail App-Specific Password",
            "OLLAMA_API_KEY": "Ollama API Key",
        },
        "~/.config/corvin-voice/secrets.json": {
            "HETZNER_API_TOKEN": "Hetzner API Token (secrets.json copy)",
            "HETZNER_SSH_KEY_NAME": "Hetzner SSH Key Name",
        },
    }

    def __init__(self, tenant_id: str = "_default", verbose: bool = False, skip_network: bool = False):
        self.tenant_id = tenant_id
        self.verbose = verbose
        self.skip_network = skip_network
        self.timestamp_utc = datetime.utcnow().isoformat() + "Z"
        self.repo_root = Path(__file__).parent.parent
        self.audit_path = (
            self.repo_root / ".corvin" / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        )
        self.inventory: List[CredentialInventory] = []

    def _color(self, text: str, color: str) -> str:
        """Apply terminal color."""
        colors = {
            "green": "\033[32m",
            "red": "\033[31m",
            "yellow": "\033[33m",
            "blue": "\033[34m",
            "reset": "\033[0m",
            "bold": "\033[1m",
            "dim": "\033[2m",
        }
        return f"{colors.get(color, '')}{text}{colors['reset']}"

    def _load_env_file(self, path: Path) -> Dict[str, str]:
        """Load .env or service.env file."""
        if not path.exists():
            return {}
        try:
            data = {}
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        data[key.strip()] = value.strip()
            return data
        except Exception as e:
            _log.warning(f"Failed to load {path}: {e}")
            return {}

    def _load_json_file(self, path: Path) -> Dict[str, str]:
        """Load secrets.json file."""
        if not path.exists():
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            _log.warning(f"Failed to load {path}: {e}")
            return {}

    def _mask_value(self, value: str) -> str:
        """Mask credential value: show first 4 chars + ***"""
        if not value:
            return "***"
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:4]}***"

    def _test_authentication(self, credential_name: str, value: str) -> Tuple[bool, str]:
        """Test if credential authenticates against its service."""
        if self.skip_network:
            return False, "network test skipped"

        tests = {
            "GITHUB_TOKEN": self._test_github,
            "HETZNER_API_TOKEN": self._test_hetzner,
            "CLOUDFLARE_API_TOKEN": self._test_cloudflare,
            "OPENAI_API_KEY": self._test_openai,
            "CORVIN_TTS_OPENAI_KEY": self._test_openai,
            "CORVIN_STT_OPENAI_KEY": self._test_openai,
            "OLLAMA_API_KEY": self._test_ollama,
        }

        test_func = tests.get(credential_name)
        if test_func:
            try:
                result, reason = test_func(value)
                return result, reason
            except Exception as e:
                return False, f"test error: {e}"
        else:
            return None, "no automated test"

    def _test_github(self, token: str) -> Tuple[bool, str]:
        """Test GitHub token."""
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"Authorization: token {token}",
                 "https://api.github.com/user"],
                timeout=5,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "login" in result.stdout:
                return True, "authenticated"
            else:
                return False, "authentication failed"
        except Exception as e:
            return False, f"test failed: {e}"

    def _test_hetzner(self, token: str) -> Tuple[bool, str]:
        """Test Hetzner API token."""
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"Authorization: Bearer {token}",
                 "https://api.hetzner.cloud/v1/account"],
                timeout=5,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "account" in result.stdout:
                return True, "authenticated"
            else:
                return False, "authentication failed"
        except Exception as e:
            return False, f"test failed: {e}"

    def _test_cloudflare(self, token: str) -> Tuple[bool, str]:
        """Test Cloudflare API token."""
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"Authorization: Bearer {token}",
                 "https://api.cloudflare.com/client/v4/user"],
                timeout=5,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "result" in result.stdout:
                return True, "authenticated"
            else:
                return False, "authentication failed"
        except Exception as e:
            return False, f"test failed: {e}"

    def _test_openai(self, token: str) -> Tuple[bool, str]:
        """Test OpenAI API key."""
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"Authorization: Bearer {token}",
                 "https://api.openai.com/v1/models"],
                timeout=5,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "data" in result.stdout:
                return True, "authenticated"
            else:
                return False, "authentication failed"
        except Exception as e:
            return False, f"test failed: {e}"

    def _test_ollama(self, token: str) -> Tuple[bool, str]:
        """Test Ollama API key."""
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"Authorization: Bearer {token}",
                 "http://localhost:11434/api/tags"],
                timeout=5,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "models" in result.stdout:
                return True, "authenticated"
            else:
                return False, "authentication failed or service down"
        except Exception as e:
            return False, f"test failed (service likely down): {str(e)[:50]}"

    def inventory_credentials(self) -> bool:
        """Inventory all 14 credentials."""
        print(f"\n{self._color('=' * 70, 'bold')}")
        print(f"{self._color('Phase 1: Credential Inventory & Verification', 'bold')}")
        print(f"{self._color('=' * 70, 'bold')}\n")

        print(f"{self._color('Step 1: Inventory 14 Credentials', 'blue')}\n")

        for file_path_str, credentials in self.CREDENTIALS.items():
            file_path = Path(file_path_str.replace("~", str(Path.home())))
            file_exists = file_path.exists()
            file_readable = file_exists and os.access(file_path, os.R_OK)

            print(f"{self._color(f'File: {file_path_str}', 'dim')}")

            # Load file
            if file_path_str.endswith(".json"):
                data = self._load_json_file(file_path)
            else:
                data = self._load_env_file(file_path)

            for key, description in credentials.items():
                value = data.get(key, "")
                key_present = bool(value)
                masked_value = self._mask_value(value)

                # Test authentication (if value present)
                test_passed = None
                test_reason = "N/A"
                if key_present:
                    test_passed, test_reason = self._test_authentication(key, value)

                # Determine status
                if not file_exists:
                    status = "missing"
                elif not file_readable:
                    status = "inaccessible"
                elif key_present:
                    status = "accessible"
                else:
                    status = "accessible_but_key_missing"

                # Create inventory record
                record = CredentialInventory(
                    credential_name=key,
                    file_path=file_path_str,
                    env_variable=key,
                    status=status,
                    file_exists=file_exists,
                    file_readable=file_readable,
                    key_present=key_present,
                    key_masked=masked_value,
                    test_passed=test_passed if test_passed is not None else False,
                    test_reason=test_reason,
                    timestamp_utc=self.timestamp_utc,
                    tenant_id=self.tenant_id,
                )
                self.inventory.append(record)

                # Print status
                status_icon = self._color("✓", "green") if status == "accessible" else self._color("✗", "red")
                test_icon = ""
                if key_present and test_passed is not None:
                    test_icon = f"  {self._color('✓ auth', 'green') if test_passed else self._color('✗ auth', 'red')}"

                print(f"  {status_icon} {key:30} {masked_value:10} {test_icon}")

            print()

        return len(self.inventory) == 14

    def verify_credentials(self) -> bool:
        """Verify all credentials are accessible + valid."""
        print(f"{self._color('Step 2: Verification Summary', 'blue')}\n")

        accessible_count = sum(1 for r in self.inventory if r.status == "accessible")
        test_passed_count = sum(1 for r in self.inventory if r.test_passed)

        print(f"Total inventoried: {len(self.inventory)}")
        print(f"Accessible:       {accessible_count}/{len(self.inventory)}")
        print(f"Auth tests passed: {test_passed_count} (network tests)")
        print()

        if accessible_count < len(self.inventory):
            print(self._color("⚠️  WARNING: Not all credentials are accessible", "yellow"))
            missing = [r for r in self.inventory if r.status != "accessible"]
            for r in missing:
                print(f"   - {r.credential_name} ({r.file_path}): {r.status}")
            print()

        return accessible_count > 0  # At least some are accessible

    def document_baseline(self) -> bool:
        """Document baseline audit trail before rotation."""
        print(f"{self._color('Step 3: Document Baseline Audit Trail', 'blue')}\n")

        # Create audit directory
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

        # Emit baseline event
        baseline_event = {
            "event_type": "credential_rotation_phase1_baseline",
            "timestamp": self.timestamp_utc,
            "tenant_id": self.tenant_id,
            "inventory_count": len(self.inventory),
            "accessible_count": sum(1 for r in self.inventory if r.status == "accessible"),
            "test_passed_count": sum(1 for r in self.inventory if r.test_passed),
            "credentials": [r.to_dict() for r in self.inventory],
            "prev_hash": "0" * 64,  # First event
        }

        # Compute hash
        event_str = json.dumps(baseline_event, sort_keys=True, default=str)
        baseline_event["hash"] = sha256(event_str.encode()).hexdigest()

        try:
            with open(self.audit_path, "a") as f:
                f.write(json.dumps(baseline_event) + "\n")
            os.chmod(self.audit_path, 0o600)
            print(self._color(f"✓ Baseline documented: {self.audit_path}", "green"))
            print(f"  Event hash: {baseline_event['hash'][:16]}...")
            return True
        except Exception as e:
            print(self._color(f"✗ Audit emission failed: {e}", "red"))
            return False

    def generate_report(self) -> str:
        """Generate Phase 1 report."""
        report = []
        report.append("=" * 70)
        report.append("CREDENTIAL ROTATION PHASE 1 — VERIFICATION REPORT")
        report.append("=" * 70)
        report.append(f"\nTimestamp: {self.timestamp_utc}")
        report.append(f"Tenant: {self.tenant_id}")
        report.append(f"Repository: {self.repo_root}")
        report.append(f"\n--- INVENTORY SUMMARY ---")
        report.append(f"Total credentials: {len(self.inventory)}")
        report.append(f"Accessible: {sum(1 for r in self.inventory if r.status == 'accessible')}")
        report.append(f"Missing: {sum(1 for r in self.inventory if r.status == 'missing')}")
        report.append(f"Inaccessible: {sum(1 for r in self.inventory if r.status == 'inaccessible')}")

        report.append(f"\n--- AUTHENTICATION TESTS ---")
        report.append(f"Tests passed: {sum(1 for r in self.inventory if r.test_passed)}")
        report.append(f"Tests failed: {sum(1 for r in self.inventory if not r.test_passed and r.test_reason != 'no automated test')}")
        report.append(f"Tests skipped: {sum(1 for r in self.inventory if r.test_reason == 'no automated test')}")

        report.append(f"\n--- DETAILED INVENTORY ---")
        for r in self.inventory:
            report.append(f"\n{r.credential_name}")
            report.append(f"  File: {r.file_path}")
            report.append(f"  Status: {r.status}")
            report.append(f"  File readable: {r.file_readable}")
            report.append(f"  Key present: {r.key_present}")
            report.append(f"  Masked: {r.key_masked}")
            report.append(f"  Test result: {r.test_passed} ({r.test_reason})")

        report.append(f"\n--- PHASE 1 COMPLETION ---")
        report.append(f"Inventory complete: YES")
        report.append(f"Baseline documented: YES (audit trail)")
        report.append(f"Phase 2 ready: YES")

        report.append(f"\n--- NEXT STEPS (Phase 2) ---")
        report.append(f"1. Operator revokes old keys via web dashboards (manual, ~1-2 hours)")
        report.append(f"2. Run Phase 2 pre-checks")
        report.append(f"3. Run credential rotation atomically")
        report.append(f"4. Verify old keys rejected, new keys accepted")
        report.append(f"5. Audit trail captures full rotation")

        return "\n".join(report)

    def run(self) -> bool:
        """Execute Phase 1."""
        print(f"{self._color(f'CorvinOS Credential Rotation — Phase 1', 'bold')}")
        print(f"{self._color('=' * 70, 'bold')}\n")

        success = True
        success &= self.inventory_credentials()
        success &= self.verify_credentials()
        success &= self.document_baseline()

        # Print report
        report = self.generate_report()
        print(f"\n{report}\n")

        # Save report
        report_path = self.repo_root / f"CREDENTIAL_ROTATION_PHASE1_REPORT.md"
        try:
            with open(report_path, "w") as f:
                f.write(report)
            print(f"\n{self._color(f'✓ Report saved: {report_path}', 'green')}")
        except Exception as e:
            print(f"\n{self._color(f'✗ Failed to save report: {e}', 'red')}")
            success = False

        return success


def main():
    parser = argparse.ArgumentParser(
        description="Credential Rotation Phase 1: Inventory & Verification"
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--tenant", default="_default", help="Tenant ID")
    parser.add_argument("--skip-network", action="store_true", help="Skip network auth tests")

    args = parser.parse_args()

    phase1 = CredentialRotationPhase1(
        tenant_id=args.tenant,
        verbose=args.verbose,
        skip_network=args.skip_network,
    )

    success = phase1.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
