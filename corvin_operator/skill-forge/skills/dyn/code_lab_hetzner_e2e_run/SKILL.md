---
name: code_lab_hetzner_e2e_run
description: Hetzner lab test execution: A2A peering, smoke test, CorvinFlow E2E + HITL, ADR-0103 attestation, teardown. Requires code.lab.hetzner_e2e setup to have run first.
---

# Hetzner A2A Lab — Run Tests (Phases 3–7)

Requires: `code.lab.hetzner_e2e` (phases 1–2c) to have completed first.
Pre-test checklist at bottom — run it before every session.

## Phase 3 — A2A Peering (bidirectional)
```bash
for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  NODE_ID="lab-node-$i"
  REMOTE_URL="http://$IP:7433"

  # Local endpoint file + remote origin file
  corvin-a2a pair "$NODE_ID" "$REMOTE_URL" > /tmp/remote-origin-$i.json

  # Authorize local machine to send to node
  scp -i "$HETZNER_SSH_PRIVATE_KEY_PATH" \
    /tmp/remote-origin-$i.json \
    root@$IP:/root/.corvin/tenants/_default/cowork/remote_origins/local-machine.json

  # Reverse: node → local machine
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP \
    "cd /opt/corvin && corvin-a2a pair local-machine $CORVIN_LOCAL_A2A_URL" \
    > ~/.corvin/tenants/_default/cowork/remote_endpoints/lab-node-$i.json
done
```

Add `--offline-pair` if ADR-0103 network attestation is not configured for lab.

## Phase 4 — Smoke Test
```bash
LOCAL_COMMIT=$(git -C /home/shumway/projects/CorvinOS rev-parse HEAD)
for i in $(seq 1 $N); do
  echo "=== lab-node-$i ==="
  corvin-a2a send "lab-node-$i" \
    "Ping. Reply with your instance_id, git commit hash ($LOCAL_COMMIT expected), and UTC time." \
    --ttl 60
done
```

Reply must include `$LOCAL_COMMIT`. If not → re-run Phase 2b then retry.

## Phase 5 — CorvinFlow E2E

Open `/app/corvinflow` in the Web-UI console. Build graph:

1. **Start** node
2. **A2A Task** → lab-node-1: `"Summarize the last 5 git commits of CorvinOS"`
3. **A2A Task** → lab-node-2: `"Translate the previous output to Spanish"`
4. **HITL Checkpoint** (ADR-0121 M4): pause for operator approval
5. **End** node

Execute, approve the HITL checkpoint, verify final output.

Verify audit chain on each node after the run:
```bash
for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  echo "=== lab-$i audit ==="
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP \
    "voice-audit verify 2>&1 | tail -3"
done
```

## Phase 6 — Network Membership Attestation (ADR-0103)
```bash
echo "=== local instance_id ==="
corvin-instance-id show

for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  echo "=== lab-$i ==="
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP \
    "python3 -c 'from operator.bridges.shared.instance_identity import get_or_create; print(get_or_create())'"
done
```

## Phase 7 — Teardown

Always run after testing to stop billing:
```bash
for i in $(seq 1 $N); do
  HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server delete "corvin-lab-$i"
done
rm -f ~/.corvin/tenants/_default/cowork/remote_endpoints/lab-node-*.json
echo "Lab torn down."
```

## Pre-test checklist (run every session)
- [ ] Phase 2b code sync — all nodes at same commit as local HEAD
- [ ] Phase 2c credentials OK — `claude --version` green on each node
- [ ] A2A receiver running on :7433 on each node (`pgrep -f a2a_http_server`)
- [ ] Bidirectional peering verified (Phase 3 done, smoke test replies correct commit)
- [ ] CorvinFlow E2E + HITL checkpoint passed (Phase 5)
- [ ] `voice-audit verify` exit 0 on each node
- [ ] ADR-0103 instance_id checked on each node
- [ ] **Teardown complete** (never leave nodes running — billing accumulates)

## Defaults
- Region: `nbg1` (Nuremberg, EU data residency)
- Server type: `cx21` (2 vCPU / 4 GB); use `cx31` for load tests
- OS: Ubuntu 24.04 LTS
- A2A receiver port: 7433
- CorvinOS path on nodes: `/opt/corvin`
- hcloud CLI: `~/.local/bin/hcloud`
