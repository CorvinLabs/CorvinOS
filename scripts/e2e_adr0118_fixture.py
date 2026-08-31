#!/usr/bin/env python3
"""ADR-0118 E2E Fixture — injects synthetic A2A delegation events into audit.jsonl.

Run once to create test data that the DualTrackAuditPanel can visualise.
The fixture simulates a single OS turn that delegates to a Hermes worker:

  OS side                          Worker side
  ─────────────────────────────    ──────────────────────────────────
  os_turn.started                  (A2A.envelope_received)
  delegation.started               A2A.engine_spawned
  A2A.envelope_sent     ───────▶   A2A.engine_spawned
                                   A2A.chain_dna_verified
                                   A2A.result_filtered
  A2A.response_received ◀───────   A2A.response_signed
  delegation.ended
  os_turn.completed

Usage:
  python3 scripts/e2e_adr0118_fixture.py [--chat-key <key>] [--channel <channel>]

Default chat_key: "adr0118-demo"  (web console session)
"""
import argparse
import sys
import time
import uuid
from pathlib import Path

# bootstrap forge path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "operator" / "forge"))
from forge.paths import corvin_home  # noqa: E402
from forge.security_events import write_event  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="ADR-0118 audit fixture injector")
    ap.add_argument("--chat-key", default="adr0118-demo", help="Chat key for session filter")
    ap.add_argument("--channel", default="web", help="Channel name")
    ap.add_argument("--tenant", default="_default", help="Tenant ID")
    args = ap.parse_args()

    audit_path = corvin_home() / "tenants" / args.tenant / "global" / "audit.jsonl"

    delegation_id = f"d-{uuid.uuid4().hex[:12]}"
    task_id = delegation_id  # matched ID so the backend groups them together
    turn_id = f"t-{uuid.uuid4().hex[:8]}"
    sender_instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    worker_instance_id = f"inst-{uuid.uuid4().hex[:8]}"

    chat_key = args.chat_key
    channel = args.channel
    persona = "orchestrator"
    engine_id = "hermes"

    now = time.time()

    events = [
        # ── OS turn lifecycle ──────────────────────────────────────────────────
        ("os_turn.started", "INFO", {
            "turn_id":   turn_id,
            "channel":   channel,
            "chat_key":  chat_key,
            "persona":   persona,
        }, now),
        ("delegation.started", "INFO", {
            "delegation_id": delegation_id,
            "turn_id":       turn_id,
            "channel":       channel,
            "chat_key":      chat_key,
            "target_engine": engine_id,
            "persona":       persona,
        }, now + 0.05),
        # ── A2A bridge: OS sends envelope ────────────────────────────────────
        ("A2A.envelope_sent", "INFO", {
            "task_id":           task_id,
            "delegation_id":     delegation_id,
            "channel":           channel,
            "chat_key":          chat_key,
            "endpoint_id":       "local-hermes",
            "sender_instance_id": sender_instance_id,
            "ttl_s":             30,
        }, now + 0.10),
        # ── Worker side ───────────────────────────────────────────────────────
        ("A2A.envelope_received", "INFO", {
            "task_id":           task_id,
            "delegation_id":     delegation_id,
            "origin_id":         "os-local",
            "sender_instance_id": sender_instance_id,
            "instance_id":       worker_instance_id,
            "nonce_prefix":      uuid.uuid4().hex[:8],
        }, now + 0.12),
        ("A2A.engine_spawned", "INFO", {
            "task_id":   task_id,
            "engine_id": engine_id,
        }, now + 0.15),
        ("A2A.chain_dna_verified", "INFO", {
            "task_id":               task_id,
            "genesis_hash_prefix":   "a1b2c3d4",
            "peer_genesis_hash_prefix": "a1b2c3d4",
            "network_id":            "corvinlabs-local",
            "instance_id_match":     True,
        }, now + 0.18),
        ("A2A.result_filtered", "INFO", {
            "task_id":             task_id,
            "filter_pass_count":   3,
            "filter_reject_count": 0,
            "status":              "ok",
        }, now + 1.42),
        ("A2A.response_signed", "INFO", {
            "task_id":   task_id,
            "engine_id": engine_id,
            "status":    "ok",
        }, now + 1.45),
        # ── A2A bridge: OS receives response ─────────────────────────────────
        ("A2A.response_received", "INFO", {
            "task_id":           task_id,
            "delegation_id":     delegation_id,
            "channel":           channel,
            "chat_key":          chat_key,
            "status":            "ok",
            "instance_id_match": True,
            "duration_ms":       1350,
        }, now + 1.50),
        # ── OS delegation / turn ended ────────────────────────────────────────
        ("delegation.ended", "INFO", {
            "delegation_id": delegation_id,
            "turn_id":       turn_id,
            "channel":       channel,
            "chat_key":      chat_key,
            "status":        "ok",
            "duration_ms":   1450,
        }, now + 1.52),
        ("os_turn.completed", "INFO", {
            "turn_id":    turn_id,
            "channel":    channel,
            "chat_key":   chat_key,
            "persona":    persona,
            "tools_called": 1,
        }, now + 1.55),
    ]

    print(f"Writing {len(events)} fixture events to {audit_path}")
    print(f"  delegation_id : {delegation_id}")
    print(f"  chat_key      : {chat_key}")
    print(f"  channel       : {channel}")
    print()

    for event_type, severity, details, ts in events:
        rec = write_event(
            audit_path,
            event_type,
            severity=severity,
            details=details,
            ts=ts,
        )
        print(f"  [{rec.get('hash', '')[:8]}] {event_type}")

    print()
    sid = f"{channel}:{chat_key}" if channel != "web" else chat_key
    print(f"Done. Open the console, navigate to session '{sid}', click Audit → Dual-Track.")
    print(f"You should see 1 delegation group with OS (indigo) and Worker (emerald) lanes.")


if __name__ == "__main__":
    main()
