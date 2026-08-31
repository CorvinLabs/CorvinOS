#!/usr/bin/env bash
# compliance/sign.sh — Sign or verify the compliance manifest bundle.
#
# Usage:
#   ./compliance/sign.sh sign    — create/update manifest.sig
#   ./compliance/sign.sh verify  — verify manifest.sig (exits 1 on failure)
#
# The GPG key used for signing must match the fingerprint configured in
# tenant.corvin.yaml::spec.compliance_manifest.signer_fingerprint.
#
# bridge.sh doctor and corvin-compliance-check verify the signature
# automatically.  A missing manifest.sig is WARNING; an invalid sig is CRITICAL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_FILES=(
    "$SCRIPT_DIR/eu-ai-act.yaml"
    "$SCRIPT_DIR/gdpr.yaml"
    "$SCRIPT_DIR/iso-42001.yaml"
    "$SCRIPT_DIR/nist-ai-rmf.yaml"
    "$SCRIPT_DIR/manifest-version.txt"
)
SIG_FILE="$SCRIPT_DIR/manifest.sig"

cmd="${1:-help}"

case "$cmd" in
sign)
    echo "Signing compliance manifest..."
    # gpg --armor --detach-sign creates a single sig over multiple files by
    # signing a concatenated digest.  We sign each file's SHA-256 hash to keep
    # the sig stable even if file ordering changes in future tools.
    [[ -f "$SIG_FILE" ]] && rm "$SIG_FILE"
    sha256sum "${MANIFEST_FILES[@]}" | \
        gpg --armor --detach-sign --batch --pinentry-mode loopback --no-tty \
            --output "$SIG_FILE" -
    echo "Signature written to $SIG_FILE"
    sha256sum "${MANIFEST_FILES[@]}" | gpg --verify "$SIG_FILE" -
    echo "Signature verified OK."
    ;;

verify)
    if [[ ! -f "$SIG_FILE" ]]; then
        echo "ERROR: manifest.sig not found — run './compliance/sign.sh sign' first" >&2
        exit 1
    fi
    sha256sum "${MANIFEST_FILES[@]}" | \
        gpg --verify "$SIG_FILE" -
    echo "Manifest signature OK."
    ;;

*)
    echo "Usage: $0 sign|verify" >&2
    exit 1
    ;;
esac
