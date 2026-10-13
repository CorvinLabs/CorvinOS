"""A2A test client — sends a signed TaskEnvelope to a remote receiver.

Usage:
  python3 remote_trigger_client.py \\
      --url http://HOST:8000/v1/a2a/receive \\
      --origin-id cloud.corvin.eu \\
      --hmac-key <hex> \\
      --recv-key <hex> \\
      --instruction "echo hello"

Exit 0 on success (status != "rejected" and response signature valid).
Exit 1 on failure. No external dependencies (stdlib only).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as _hmac
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid


def build_envelope(
    origin_id: str,
    hmac_key_hex: str,
    instruction: str,
    ttl_s: int = 60,
    result_schema: dict | None = None,
    sender_instance_id: str = "",
    attachments: list | None = None,
) -> dict:
    """Build and sign a TaskEnvelope (protocol v3)."""
    env: dict = {
        "task_id": str(uuid.uuid4()),
        "nonce": secrets.token_hex(32),
        "issued_at": time.time(),
        "origin_id": origin_id,
        "instruction": instruction,
        "result_schema": result_schema or {},
        "ttl_s": ttl_s,
        "sender_instance_id": sender_instance_id,
        "attachments": list(attachments or []),
        "signature": "",
    }
    key = bytes.fromhex(hmac_key_hex)
    payload = {k: v for k, v in env.items() if k != "signature"}
    sig = _hmac.new(
        key,
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    env["signature"] = sig
    return env


def send_envelope(url: str, envelope: dict, timeout: int = 30) -> dict:
    """POST envelope to url; return parsed JSON response."""
    body = json.dumps(envelope).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def verify_response(
    response: dict,
    recv_key_hex: str,
    *,
    expected_task_id: str | None = None,
) -> bool:
    """Verify the HMAC signature on a ResponseEnvelope.

    Also cross-checks that response["task_id"] == expected_task_id when
    provided, so a legitimate-but-buggy receiver cannot return a
    valid-HMAC response for a different task_id (ADR-0099 iter-2
    finding MED-CLIENT-01).
    """
    sig = response.get("signature", "")
    if not sig:
        return False
    # Cross-check task_id binding before HMAC verification.
    if expected_task_id is not None:
        resp_task_id = response.get("task_id", "")
        if resp_task_id != expected_task_id:
            return False
    payload = {k: v for k, v in response.items() if k != "signature"}
    key = bytes.fromhex(recv_key_hex)
    expected = _hmac.new(
        key,
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    return _hmac.compare_digest(expected, sig.lower())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="A2A test client for Corvin Layer 38",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", required=True, help="Receiver URL (POST /v1/a2a/receive)")
    parser.add_argument("--origin-id", required=True, help="Origin ID (must be registered on the receiver)")
    parser.add_argument("--hmac-key", required=True, help="HMAC key (hex) for signing TaskEnvelope")
    parser.add_argument("--recv-key", required=True, help="Receiver key (hex) for verifying ResponseEnvelope")
    parser.add_argument("--instruction", default="ping", help="Task instruction text")
    parser.add_argument("--ttl", type=int, default=60, help="ttl_s (default: 60)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--sender-instance-id", default="", help="Local instance UUID to attest (optional)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print(f"Building envelope for origin '{args.origin_id}'…")
    envelope = build_envelope(
        origin_id=args.origin_id,
        hmac_key_hex=args.hmac_key,
        instruction=args.instruction,
        ttl_s=args.ttl,
        sender_instance_id=args.sender_instance_id,
    )
    print(f"  task_id   : {envelope['task_id']}")
    print(f"  nonce[:8] : {envelope['nonce'][:8]}")
    if args.verbose:
        print(f"  payload   : {json.dumps({k: v for k, v in envelope.items() if k != 'signature'}, indent=2)}")

    print(f"\nSending to {args.url} …")
    try:
        response = send_envelope(args.url, envelope, timeout=args.timeout)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode()}")
        return 1
    except Exception as exc:
        print(f"Request failed: {exc}")
        return 1

    print(f"\nResponse received:")
    print(f"  status    : {response.get('status')}")
    print(f"  task_id   : {response.get('task_id')}")
    print(f"  data      : {response.get('data')}")
    if args.verbose:
        print(f"  full      : {json.dumps(response, indent=2)}")

    if response.get("status") == "rejected":
        print("\nFAIL: receiver rejected the envelope.")
        return 1

    sig_valid = verify_response(response, args.recv_key,
                               expected_task_id=envelope["task_id"])
    if sig_valid:
        print("\nOK: response signature verified. E2E test passed.")
        return 0
    else:
        print("\nFAIL: response signature invalid.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
