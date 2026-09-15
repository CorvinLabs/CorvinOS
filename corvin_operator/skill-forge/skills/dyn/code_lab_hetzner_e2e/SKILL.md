---
name: code_lab_hetzner_e2e
description: Hetzner lab setup for A2A/CorvinFlow E2E tests: provision nodes, sync code, install Claude Code + credentials, start A2A receiver. See code.lab.hetzner_e2e_run for peering/test/teardown.
---

# Hetzner A2A Lab — Setup (Phases 1–2c)

Companion: `code.lab.hetzner_e2e_run` covers peering, smoke test, CorvinFlow E2E, teardown.

## When to invoke
- E2E test of A2A / CorvinFlow across real Hetzner nodes
- Multi-node CorvinOS network, ADR-0103 attestation, geo-distributed scenarios

## Code-freshness invariant
Every node must run the exact local HEAD commit. Run Phase 2b before every test.

## Secrets (vault — never in code)
- `HETZNER_API_TOKEN` — hcloud auth
- `HETZNER_SSH_KEY_NAME` — Hetzner registered key name
- `HETZNER_SSH_PRIVATE_KEY_PATH` — local private key path
- `CORVIN_LOCAL_A2A_URL` — public URL of local A2A receiver

Files copied via scp (sensitive — never committed):
- `~/.claude/.credentials.json` → `/root/.claude/.credentials.json` (0600)
- `~/.config/corvin-voice/service.env` → `/root/.config/corvin-voice/service.env` (0600)

## Architecture
```
Local (this host): CorvinOS adapter + CorvinFlow console + A2A receiver :7433
Hetzner nbg1 (EU): corvin-lab-1..N  cx21  Ubuntu 24.04
  each node: CorvinOS + ClaudeCodeEngine + A2A receiver :7433
Peering: bidirectional local ↔ each node
```

## Phase 1 — Provision (N default 2)
```bash
N=2
HCLOUD="HCLOUD_TOKEN=$HETZNER_API_TOKEN ~/.local/bin/hcloud"
for i in $(seq 1 $N); do
  $HCLOUD server create --name "corvin-lab-$i" --type cx21 \
    --image ubuntu-24.04 --location nbg1 --ssh-key "$HETZNER_SSH_KEY_NAME"
done
for i in $(seq 1 $N); do
  $HCLOUD server wait-for-status "corvin-lab-$i" running
  echo "lab-$i: $($HCLOUD server ip corvin-lab-$i)"
done
```

## Phase 2a — First-time install
```bash
LOCAL_COMMIT=$(git -C /home/shumway/projects/CorvinOS rev-parse HEAD)
for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" -o StrictHostKeyChecking=no root@$IP bash <<ENDSSH
    set -e
    apt-get update -qq && apt-get install -y -qq git python3-pip curl build-essential
    git clone https://github.com/CorvinLabs/CorvinOS.git /opt/corvin
    cd /opt/corvin && git checkout $LOCAL_COMMIT
    pip3 install -e . --break-system-packages -q
    mkdir -p /root/.corvin/tenants/_default/{global,sessions,voice,cowork/remote_origins,cowork/remote_endpoints}
    mkdir -p /root/.claude /root/.config/corvin-voice
ENDSSH
done
```

## Phase 2b — Code Sync (MANDATORY before every test)
```bash
LOCAL_COMMIT=$(git -C /home/shumway/projects/CorvinOS rev-parse HEAD)
echo "Syncing all nodes to $LOCAL_COMMIT"
for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP bash <<ENDSSH
    cd /opt/corvin && git fetch origin -q && git checkout $LOCAL_COMMIT
    pip3 install -e . --break-system-packages -q
    python3 -c 'import operator; print("ok")'
ENDSSH
  REMOTE=$(ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP "git -C /opt/corvin rev-parse HEAD")
  [ "$REMOTE" = "$LOCAL_COMMIT" ] && echo "lab-$i: OK" || echo "lab-$i: MISMATCH"
done
```

## Phase 2c — Credentials + Claude Code Engine + A2A Receiver
```bash
for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")

  # Install Node.js LTS + Claude Code CLI
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP bash <<ENDSSH
    set -e
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - -q
    apt-get install -y -qq nodejs
    npm install -g @anthropic-ai/claude-code --quiet
    claude --version
ENDSSH

  # Copy Claude Code OAuth token (0600 — never log, never commit)
  scp -i "$HETZNER_SSH_PRIVATE_KEY_PATH" \
    ~/.claude/.credentials.json root@$IP:/root/.claude/.credentials.json
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP \
    "chmod 0600 /root/.claude/.credentials.json"

  # Copy service API keys (OpenAI STT/TTS etc.)
  scp -i "$HETZNER_SSH_PRIVATE_KEY_PATH" \
    ~/.config/corvin-voice/service.env root@$IP:/root/.config/corvin-voice/service.env
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP \
    "chmod 0600 /root/.config/corvin-voice/service.env"

  # Verify Claude Code works, then start A2A receiver
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP bash <<ENDSSH
    claude -p "pong" --max-turns 1 --no-tools 2>&1 | head -3
    pkill -f a2a_http_server 2>/dev/null || true; sleep 1
    cd /opt/corvin
    set -a; source /root/.config/corvin-voice/service.env; set +a
    nohup python3 -m operator.bridges.shared.a2a_http_server \
      > /var/log/corvin-a2a.log 2>&1 &
    sleep 2 && echo "A2A PID: $(pgrep -f a2a_http_server)"
ENDSSH
  echo "lab-$i: ready"
done
```
