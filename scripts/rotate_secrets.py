#!/usr/bin/env python3
"""Secret Rotation Script — DEFUSED (adversarial review 2026-09-27).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — no
systemd unit, cron entry, installer or boot path runs this file.

This script used to "rotate" a hard-coded two-entry MOCK inventory
(``api_key_prod`` / ``db_password``): it generated a new random secret, threw
it away without storing it anywhere, hashed the literal string
``"placeholder_<id>"`` as the "old secret", and appended an UNCHAINED
``secret_rotated`` JSON line to a side file named by the policy — outside the
tenant audit chain. It also crashed on first run (``chmod`` of a log file that
did not exist yet). Its boot tripwire checked the same mock inventory.

A rotation that rotates nothing while writing records that say it did is worse
than no rotation, so the script now REFUSES to run (exit code 2) and the
manager raises ``NotImplementedError``. (``scripts/rotate_corvin_keys_gdpr.py``
was defused for the same reason; there is no working secret-rotation script in
this repo as of 2026-09-27.)

To make this real it would need: a real secret inventory (KMS / vault), a
store for the new secret, and ``secret_rotated`` records written through
``forge.security_events.write_event`` onto ``tenant_audit_chain(tenant)``.
"""

import argparse
import sys

REFUSAL = (
    "rotate_secrets.py is defused: it rotated a hard-coded mock inventory, "
    "discarded the new secrets and wrote unchained audit records. Nothing was "
    "rotated."
)


class SecretRotationManager:
    """Refuses every operation — see the module docstring."""

    def __init__(self, policy_path: str, tenant_id: str = "_default"):
        raise NotImplementedError(REFUSAL)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Secret Rotation (defused)")
    parser.add_argument("--policy-file", default="core/compliance/secret_rotation_policy.yaml")
    parser.add_argument("--boot-check-only", action="store_true")
    parser.parse_args(argv)
    print(f"ERROR: {REFUSAL}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
