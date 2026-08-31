"""
E2E Full-Audit Demo Fixture
===========================
Creates a fictional but realistic session `web:e2e-full-audit-demo` with
events that exercise every sub-panel of the Audit panel:

  Single-Chain:
    1. ACS Workflow Graph  — acs.manager_decided + acs.worker_spawned x3
                             + acs.engine_started/completed + forge.tool_executed
                             + acs.worker_traced x3 + acs.workflow_complete
    2. OS-Turn Audit       — os_turn.started + os_turn.tool_called x2
                             + os_turn.completed
    3. Execution Log       — all of the above combined

  Dual-Track (ADR-0118):
    4. Dual-Track          — delegation.started + A2A.envelope_sent
                             + A2A.envelope_received + A2A.engine_spawned
                             + A2A.chain_dna_verified + A2A.result_filtered
                             + A2A.response_signed + A2A.response_received
                             + delegation.ended

Audit path routing:
  • os_turn.*     → global/forge/audit.jsonl   (bridge adapter audit chain)
  • delegation.*,
    A2A.*,
    acs.*,
    chain.genesis → tenants/_default/global/audit.jsonl

Run:
    python scripts/e2e_full_audit_fixture.py
"""

from __future__ import annotations

import hashlib
import json
import os as _os
import sys
import time
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from forge import paths as _fp  # type: ignore[import-untyped]

CORVIN_HOME   = _fp.corvin_home()
TENANT_ID     = "_default"
SESSION_ID    = "e2e-full-audit-demo"
CHANNEL       = "web"
CHAT_KEY_FULL = "web:e2e-full-audit-demo"   # bridge adapter format (channel:bare)
CHAT_KEY_BARE = "e2e-full-audit-demo"        # bare key used by chain_dual_track filter
RUN_ID        = "acs-demo-full-audit-001"
TURN_ID       = "turn-demo-002"             # bumped to avoid duplicate with earlier run
DELEGATION_ID = "a2a-deleg-demo-002"        # bumped to avoid collision
TASK_ID       = "task-demo-002"
ORIGIN_ID     = "origin-demo-hermes"
ENDPOINT_ID   = "endpoint-demo-hermes"
INSTANCE_ID   = "inst-demo-corvin-001"
NETWORK_ID    = "corvin-demo-net"

# ── two separate audit chain paths ────────────────────────────────────────
# os_turn.* events live in the bridge adapter's unified audit chain
FORGE_AUDIT = CORVIN_HOME / "global" / "forge" / "audit.jsonl"
FORGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)

# delegation.*, A2A.*, acs.*, chain.genesis live in the tenant audit chain
TENANT_AUDIT = CORVIN_HOME / "tenants" / TENANT_ID / "global" / "audit.jsonl"
TENANT_AUDIT.parent.mkdir(parents=True, exist_ok=True)

# ── session + ACS run directory ────────────────────────────────────────────
SESSION_DIR = CORVIN_HOME / "tenants" / TENANT_ID / "sessions" / CHAT_KEY_FULL
ACS_RUN_DIR = SESSION_DIR / "acs" / "runs" / RUN_ID
ACS_RUN_DIR.mkdir(parents=True, exist_ok=True)
(ACS_RUN_DIR / "manifest.json").write_text(json.dumps({
    "run_id":     RUN_ID,
    "chat_key":   CHAT_KEY_FULL,
    "status":     "success",
    "started_at": time.time() - 45,
    "ended_at":   time.time() - 2,
}))

# ── session metadata ────────────────────────────────────────────────────────
SESSION_STORE = CORVIN_HOME / "tenants" / TENANT_ID / "global" / "web_chat" / "sessions"
SESSION_STORE.mkdir(parents=True, exist_ok=True)
SESSION_META = SESSION_STORE / f"{SESSION_ID}.json"

_now_meta = time.time()
meta_payload = {
    "sid":            SESSION_ID,
    "created_at":     _now_meta - 120,
    "last_active_at": _now_meta - 2,
    "title":          "E2E Full Audit Demo — alle Panels",
    "turn_count":     1,
    "workdir":        str(SESSION_DIR),
}
fd = _os.open(SESSION_META, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
try:
    _os.write(fd, (json.dumps(meta_payload, indent=2, sort_keys=True) + "\n").encode())
finally:
    _os.close(fd)

print(f"corvin_home  : {CORVIN_HOME}")
print(f"forge audit  : {FORGE_AUDIT}")
print(f"tenant audit : {TENANT_AUDIT}")
print(f"ACS run dir  : {ACS_RUN_DIR}")
print(f"Session meta : {SESSION_META}")

# ── hash-chain helpers ─────────────────────────────────────────────────────
def _seed_hash(path: Path) -> str:
    """Read last hash from an existing chain file (mirrors security_events._last_hash)."""
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        try:
            h = json.loads(line).get("hash")
            if h is not None:
                return h
        except Exception:
            continue
    return ""


class ChainWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._prev = _seed_hash(path)
        print(f"Seeded {path.name} prev_hash: {self._prev[:16]}…")

    def write(self, event_type: str, details: dict, severity: str = "INFO", ts: float | None = None) -> None:
        ts = ts or time.time()
        record = {
            "event_type": event_type,
            "ts":         ts,
            "severity":   severity,
            "details":    details,
            "prev_hash":  self._prev,
        }
        # Mirror security_events.py exactly: sha256(prev + "\n" + canonical)[:16]
        # canonical = json.dumps(record_without_hash, sort_keys=True)
        canonical = json.dumps(record, separators=(",", ":"), sort_keys=True)
        h = hashlib.sha256()
        h.update(self._prev.encode("utf-8"))
        h.update(b"\n")
        h.update(canonical.encode("utf-8"))
        self._prev = h.hexdigest()[:16]
        record["hash"] = self._prev
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


forge  = ChainWriter(FORGE_AUDIT)
tenant = ChainWriter(TENANT_AUDIT)

now = time.time()

# ══════════════════════════════════════════════════════════════════════════
# BLOCK A — chain.genesis (tenant chain — needed for Dual-Track NBAC badge)
# ══════════════════════════════════════════════════════════════════════════
# Only write genesis if the tenant chain has no genesis yet, to keep the
# existing NBAC anchor (if any). If a genesis is already present the
# Dual-Track panel shows whichever genesis is first — that's correct.
_has_genesis = False
if TENANT_AUDIT.exists():
    for _ln in TENANT_AUDIT.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(_ln).get("event_type") == "chain.genesis":
                _has_genesis = True
                break
        except Exception:
            pass

if not _has_genesis:
    tenant.write("chain.genesis", {
        "network_id":        NETWORK_ID,
        "instance_id":       INSTANCE_ID,
        "network_pubkey_fp": "a1b2c3d4e5f6a7b8",
    }, ts=now - 120)
    print("✓ chain.genesis (new)")
else:
    print("✓ chain.genesis (already present — skipped)")

# ══════════════════════════════════════════════════════════════════════════
# BLOCK B — OS Turn (→ global/forge/audit.jsonl)
# The os-turns endpoint reads from this path.
# details.chat_key must be the full "web:<sid>" format (CHAT_KEY_FULL).
# ══════════════════════════════════════════════════════════════════════════
forge.write("os_turn.started", {
    "chat_key": CHAT_KEY_FULL,
    "channel":  CHANNEL,
    "turn_id":  TURN_ID,
    "persona":  "assistant",
    "model":    "claude-haiku-4-5",
}, ts=now - 115)

forge.write("os_turn.tool_called", {
    "chat_key":  CHAT_KEY_FULL,
    "turn_id":   TURN_ID,
    "tool_name": "Read",
    "seq":       1,
}, ts=now - 112)

forge.write("os_turn.tool_called", {
    "chat_key":  CHAT_KEY_FULL,
    "turn_id":   TURN_ID,
    "tool_name": "Bash",
    "seq":       2,
}, ts=now - 108)

forge.write("os_turn.completed", {
    "chat_key":    CHAT_KEY_FULL,
    "turn_id":     TURN_ID,
    "persona":     "assistant",
    "model":       "claude-haiku-4-5",
    "duration_ms": 80000,
    "tools_called": 2,
    "exit_code":   0,
    "timed_out":   False,
}, ts=now - 5)

print("✓ os_turn events → forge audit")

# ══════════════════════════════════════════════════════════════════════════
# BLOCK C — Dual-Track A2A delegation (→ tenants/_default/global/audit.jsonl)
# OS-side events need details.chat_key = CHAT_KEY_BARE (without "web:" prefix)
# because chain_dual_track._matches_session() extracts the bare key from the
# sid path parameter ("e2e-full-audit-demo" has no colon → filter_chat_key =
# "e2e-full-audit-demo").
# ══════════════════════════════════════════════════════════════════════════
tenant.write("delegation.started", {
    "chat_key":      CHAT_KEY_BARE,
    "channel":       CHANNEL,
    "turn_id":       TURN_ID,
    "delegation_id": DELEGATION_ID,
    "task_id":       TASK_ID,
    "target_engine": "delegate_hermes",
    "persona":       "orchestrator",
}, ts=now - 105)

# OS sends envelope to worker
tenant.write("A2A.envelope_sent", {
    "chat_key":      CHAT_KEY_BARE,
    "channel":       CHANNEL,
    "delegation_id": DELEGATION_ID,
    "task_id":       TASK_ID,
    "endpoint_id":   ENDPOINT_ID,
    "ttl_s":         300,
    "nonce_prefix":  "ab12cd34ef56",
}, ts=now - 104)

# Worker receives it (no chat_key on worker side — matched via delegation_id)
tenant.write("A2A.envelope_received", {
    "task_id":           TASK_ID,
    "delegation_id":     DELEGATION_ID,
    "origin_id":         ORIGIN_ID,
    "sender_instance_id": "inst-demo-corvin-sender",
    "ttl_s":             300,
    "nonce_prefix":      "ab12cd34ef56",
}, ts=now - 103)

# Worker spawns engine
tenant.write("A2A.engine_spawned", {
    "task_id":       TASK_ID,
    "delegation_id": DELEGATION_ID,
    "engine_id":     "hermes",
    "persona":       "hermes-local",
    "origin_id":     ORIGIN_ID,
}, ts=now - 102)

# NBAC chain-DNA verification (ADR-0117)
tenant.write("A2A.chain_dna_verified", {
    "task_id":           TASK_ID,
    "delegation_id":     DELEGATION_ID,
    "origin_id":         ORIGIN_ID,
    "instance_id_match": True,
    "status":            "ok",
}, ts=now - 100)

# Worker filters result
tenant.write("A2A.result_filtered", {
    "task_id":             TASK_ID,
    "delegation_id":       DELEGATION_ID,
    "filter_pass_count":   12,
    "filter_reject_count": 2,
    "status":              "filtered",
}, ts=now - 60)

# Worker signs response
tenant.write("A2A.response_signed", {
    "task_id":     TASK_ID,
    "delegation_id": DELEGATION_ID,
    "origin_id":   ORIGIN_ID,
    "instance_id": INSTANCE_ID,
    "http_status": 200,
    "duration_ms": 42150,
}, ts=now - 59)

# OS receives response
tenant.write("A2A.response_received", {
    "chat_key":      CHAT_KEY_BARE,
    "channel":       CHANNEL,
    "delegation_id": DELEGATION_ID,
    "task_id":       TASK_ID,
    "endpoint_id":   ENDPOINT_ID,
    "duration_ms":   42500,
    "status":        "ok",
}, ts=now - 58)

# Delegation ended
tenant.write("delegation.ended", {
    "chat_key":      CHAT_KEY_BARE,
    "channel":       CHANNEL,
    "turn_id":       TURN_ID,
    "delegation_id": DELEGATION_ID,
    "task_id":       TASK_ID,
    "duration_ms":   47000,
    "status":        "ok",
}, ts=now - 57)

print("✓ A2A delegation events → tenant audit (chat_key=bare)")

# ══════════════════════════════════════════════════════════════════════════
# BLOCK D — ACS Workflow (→ tenants/_default/global/audit.jsonl)
# ══════════════════════════════════════════════════════════════════════════
WORKER_IDS = ["w-alpha-001", "w-beta-002", "w-gamma-003"]
NONCE_1    = "nonce-batch-001"

tenant.write("acs.run_start", {
    "run_id":   RUN_ID,
    "chat_key": CHAT_KEY_FULL,
    "channel":  CHANNEL,
}, ts=now - 50)

# Manager iteration 1 — spawns 3 workers
tenant.write("acs.manager_decided", {
    "run_id":        RUN_ID,
    "chat_key":      CHAT_KEY_FULL,
    "iteration":     1,
    "decision_type": "spawn_workers",
    "n_subtasks":    3,
    "spawn_nonce":   NONCE_1,
    "decision_hash": "d1a2b3c4e5f60001",
    "model_id":      "claude-haiku-4-5",
}, ts=now - 49)

for i, wid in enumerate(WORKER_IDS):
    tenant.write("acs.worker_spawned", {
        "run_id":           RUN_ID,
        "chat_key":         CHAT_KEY_FULL,
        "worker_id":        wid,
        "iteration":        1,
        "depth":            1,
        "parent_worker_id": None,
        "can_delegate":     False,
        "spawn_nonce":      NONCE_1,
        "instruction_hash": f"instr{i:04d}hash{wid[-3:]}",
        "model_id":         "claude-haiku-4-5",
    }, ts=now - 48 + i * 0.1)

print("✓ acs.manager_decided + acs.worker_spawned x3")

# Engine lifecycle for each worker
for i, wid in enumerate(WORKER_IDS):
    tenant.write("acs.engine_started", {
        "run_id":    RUN_ID,
        "worker_id": wid,
        "engine_id": "claude_code",
        "model_id":  "claude-haiku-4-5",
        "locality":  "eu_cloud",
    }, ts=now - 46 + i * 5)

    if wid == "w-alpha-001":
        tenant.write("forge.tool_executed", {
            "run_id":    RUN_ID,
            "worker_id": wid,
            "tool_name": "Read",
            "decision":  "allow",
        }, ts=now - 44)
        tenant.write("forge.tool_executed", {
            "run_id":    RUN_ID,
            "worker_id": wid,
            "tool_name": "Bash",
            "decision":  "allow",
        }, ts=now - 43)
        tenant.write("forge.tool_executed", {
            "run_id":    RUN_ID,
            "worker_id": wid,
            "tool_name": "Edit",
            "decision":  "deny",
        }, ts=now - 42)

    tenant.write("acs.engine_completed", {
        "run_id":      RUN_ID,
        "worker_id":   wid,
        "engine_id":   "claude_code",
        "model_id":    "claude-haiku-4-5",
        "locality":    "eu_cloud",
        "duration_ms": int(8000 + i * 1200),
        "tokens_used": int(1800 + i * 250),
        "exit_code":   0,
    }, ts=now - 38 + i * 5)

print("✓ acs.engine_started/completed + forge.tool_executed")

# Worker traces
statuses    = ["success", "success", "partial"]
confidence  = [0.91, 0.87, 0.74]
for i, wid in enumerate(WORKER_IDS):
    tenant.write("acs.worker_traced", {
        "run_id":      RUN_ID,
        "worker_id":   wid,
        "status":      statuses[i],
        "confidence":  confidence[i],
        "output_hash": f"out{i:04d}hash{wid[-3:]}aa",
        "duration_ms": int(9200 + i * 1200),
        "tokens_used": int(1800 + i * 250),
        "engine_attestation": {
            "model_id": "claude-haiku-4-5",
            "locality": "eu_cloud",
            "attested": True,
        },
    }, ts=now - 20 + i * 2)

print("✓ acs.worker_traced x3")

# Manager iteration 2 — done
tenant.write("acs.manager_decided", {
    "run_id":        RUN_ID,
    "chat_key":      CHAT_KEY_FULL,
    "iteration":     2,
    "decision_type": "done",
    "n_subtasks":    0,
    "spawn_nonce":   "nonce-batch-002",
    "decision_hash": "d1a2b3c4e5f60002",
    "model_id":      "claude-haiku-4-5",
}, ts=now - 14)

tenant.write("acs.workflow_complete", {
    "run_id":          RUN_ID,
    "chat_key":        CHAT_KEY_FULL,
    "workers_spawned": 3,
    "iterations":      2,
    "elapsed_s":       36.0,
    "status":          "success",
}, ts=now - 12)

print("✓ acs.workflow_complete")

# ══════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 60)
print(f"Fixture complete. Session: {SESSION_ID}")
print(f"Navigate to: /console/app/chat?sid={SESSION_ID}")
print(f"Or click session '{CHAT_KEY_FULL}' in the chat list.")
print()
print("Expected Audit panel state:")
print("  ① ACS Workflow Graph  : 3 workers, 2 manager iterations, engine + tool nodes")
print("  ② OS-Turn Audit       : 1 OS turn (Haiku-4.5, 80s, 2 tools)")
print("  ③ Execution Log       : all events combined")
print("  ④ Dual-Track          : 1 delegation, OS+Worker swimlanes, NBAC chain DNA ✓")
print(f"\nRun ID: {RUN_ID}")
