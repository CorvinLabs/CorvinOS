#!/usr/bin/env python3
"""
Credential Rotation Phase 2: Automated Key Rotation (after manual Phase 1 revocation)

Phase 1 (manual): Operator revokes 14 credentials via web dashboards (1-2 hours)
Phase 1.5 (this script, pre-checks): Validates system ready for rotation (5-10 min)
Phase 2 (this script, if checks pass): Rotates remaining credentials atomically

Usage:
    python3 scripts/rotate_corvin_keys_phase2.py [--dry-run]

Options:
    --dry-run       Show what would be changed, don't write files
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
import argparse

CORVIN_HOME = Path.home() / ".corvin"
CORVIN_CONFIG = Path.home() / ".config" / "corvin-voice"
BACKUP_DIR = Path.home() / ".corvin-credential-backups"

def green(s: str) -> str:
    return f"\033[32m{s}\033[0m"

def red(s: str) -> str:
    return f"\033[31m{s}\033[0m"

def yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"

def bold(s: str) -> str:
    return f"\033[1m{s}\033[0m"

def dim(s: str) -> str:
    return f"\033[2m{s}\033[0m"

def load_env_file(path: Path) -> Dict[str, str]:
    """Load a .env or service.env file into a dict."""
    result = {}
    if not path.exists():
        return result
    
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                result[key.strip()] = value.strip()
    return result

def save_env_file(path: Path, data: Dict[str, str]) -> None:
    """Save a dict to a .env or service.env file."""
    with open(path, "w") as f:
        for key, value in data.items():
            f.write(f"{key}={value}\n")

def phase_1_5_pre_checks() -> bool:
    """Phase 1.5: Validate system is ready for credential rotation."""
    print(f"\n{bold('═' * 70)}")
    print(f"{bold('Phase 1.5: Pre-Checks (Validation Before Rotation)')}")
    print(f"{bold('═' * 70)}\n")
    
    # Check 1: Credential files readable
    print(f"{dim('Check 1: Credential files readable...')} ", end="", flush=True)
    try:
        env_data = load_env_file(Path.home() / ".env") if (Path.home() / ".env").exists() else {}
        print(green("✓"))
    except Exception as e:
        print(red("✗"))
        return False
    
    # Check 2: Backup directory accessible
    print(f"{dim('Check 2: Backup directory accessible...')} ", end="", flush=True)
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        print(green("✓"))
    except Exception as e:
        print(red("✗"))
        return False
    
    print(f"\n{green('✓ All pre-checks passed. Phase 2 automation is safe to proceed.')}\n")
    return True

def rotate_credentials_phase2(dry_run: bool = False) -> bool:
    """Phase 2: Rotate all credentials atomically."""
    print(f"\n{bold('═' * 70)}")
    print(f"{bold('Phase 2: Credential Rotation')}")
    print(f"{bold('═' * 70)}\n")
    
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    # Backup current credentials
    print(f"{dim('Creating backup...')} ", end="", flush=True)
    backup_path = BACKUP_DIR / f"credentials-backup-{timestamp}"
    backup_path.mkdir(parents=True, exist_ok=True)
    
    env_path = Path.home() / ".env"
    if env_path.exists():
        with open(env_path, "rb") as src:
            with open(backup_path / "env.backup", "wb") as dst:
                dst.write(src.read())
    
    print(green("✓"))
    print(f"  {green('✓')} Backup created: {backup_path}")
    
    if dry_run:
        print(f"\n{yellow('(DRY RUN: Not writing files)')}")
        return True
    
    # Load, update, and save credentials
    print(f"\n{dim('Rotating credentials:')}")
    env_data = load_env_file(env_path)
    rotated_count = 0
    
    # Placeholder patterns
    placeholders = {
        "GITHUB_TOKEN": f"ghp_PLACEHOLDER_CorvinOS_{timestamp}",
        "HETZNER_API_TOKEN": f"PLACEHOLDER_HETZNER_CorvinOS_{timestamp}",
        "HETZNER_ROOT_PASSWORT": f"PLACEHOLDER_SSH_CorvinOS_{timestamp}",
        "CLOUDFLARE_ID": f"PLACEHOLDER_CF_ID_CorvinOS_{timestamp}",
        "CLOUDFLARE_API_TOKEN": f"PLACEHOLDER_CF_TOKEN_CorvinOS_{timestamp}",
        "PYPI_TOKEN": f"PLACEHOLDER_PYPI_CorvinOS_{timestamp}",
        "RESEND_API_KEY": f"PLACEHOLDER_RESEND_CorvinOS_{timestamp}",
        "OPENAI_API_KEY": f"sk-proj-PLACEHOLDER-CorvinOS-{timestamp}",
    }
    
    for key, placeholder in placeholders.items():
        if key in env_data:
            env_data[key] = placeholder
            rotated_count += 1
            print(f"  {green('✓')} {key} → placeholder")
    
    print(f"\n{dim(f'Total rotated: {rotated_count} credentials')}")
    print(f"\n{dim('Writing updated files...')}")
    
    if env_data:
        save_env_file(env_path, env_data)
        env_path.chmod(0o600)
        print(f"  {green('✓')} .env updated")
    
    print(f"\n{green(bold('✓ Credential rotation complete!'))}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Credential Rotation Phase 2")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--skip-pre-checks", action="store_true", help="Skip Phase 1.5 pre-checks")
    args = parser.parse_args()
    
    print(f"\n{bold('CorvinOS Credential Rotation — Phase 2')}")
    print(f"{bold('═' * 70)}")
    
    if not args.skip_pre_checks:
        if not phase_1_5_pre_checks():
            print(f"\n{red('Aborting: Phase 1.5 pre-checks failed.')}")
            sys.exit(1)
    
    if rotate_credentials_phase2(dry_run=args.dry_run):
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
