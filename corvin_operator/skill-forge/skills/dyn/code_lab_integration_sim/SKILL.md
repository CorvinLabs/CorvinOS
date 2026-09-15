---
name: code_lab_integration_sim
description: Full-stack CorvinOS integration simulation: DataCorp org with Hetzner nodes, fresh pip install, real LLM calls, Spotify compute analysis, A2A flows, persona routing, CorvinFlow graph. Mandatory: code.lab.verify_compliance after every phase.
---

# CorvinOrg Integration Simulation — Full-Stack Test

Simulates a real organisation ("DataCorp") running on a live multi-node
CorvinOS network. Every layer is exercised in sequence and chained into one
coherent flow. Uses real Hetzner infrastructure, real LLM calls, real data.

**Mandatory after every phase: run `code.lab.verify_compliance`**
(compliance + audit chain + key safety). A phase is only green when the
compliance verifier passes — never declare done before that.

## Layers exercised
L6 Forge · L7 SkillForge · L16 Audit · L22 WorkerEngine ·
L24 DSI/Data · L25 Compute · L28 Recall · L29 Delegation ·
L33 Artifacts · L34 Data-Classification · L38 A2A

## Architecture — "DataCorp"
```
local          = orchestrator + CorvinFlow console + HITL operator
corvin-lab-1   = DataAnalyst  — L25 compute worker, Spotify analysis
corvin-lab-2   = BoardWriter  — LLM narrative, report generation
corvin-lab-3   = QARouter     — persona routing judge, fact-check
```

## Flow Graph (DataCorp Pipeline)
```
START
 ├─ [DSI] Register synthetic Spotify CSV (L24)
 ├─ [Route] Auto-route to "analyst" persona (L5 auto-routing)
 ├─ [Compute] lab-1: genre frequency → histogram PNG (L25)
 ├─ [A2A] lab-2: "Narrate this chart for the board" (L38)
 ├─ [Artifact] Register PNG + narrative in L33
 ├─ [HITL] Human reviews narrative → approve/reject (ADR-0121 M4)
 ├─ [A2A] lab-3: fact-check lab-2 output vs raw data
 ├─ [Recall] Index full pipeline result in L28 FTS5
 ├─ [Compliance] code.lab.verify_compliance ← MANDATORY
 └─ END
```

## Phase 0 — Fresh Environment (run every time)
```bash
LOCAL_COMMIT=$(git -C /home/shumway/projects/CorvinOS rev-parse HEAD)
N=3

for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP bash <<ENDSSH
    set -e
    cd /opt/corvin
    git fetch origin -q && git checkout $LOCAL_COMMIT
    pip3 uninstall -y corvin corvinOS 2>/dev/null || true
    pip3 install -e . --break-system-packages --no-cache-dir -q
    python3 -c 'import operator.bridges.shared.adapter; print("fresh install ok")'
    rm -rf /root/.corvin/tenants/datacorp
    mkdir -p /root/.corvin/tenants/datacorp/{global,sessions,voice,cowork/remote_origins,cowork/remote_endpoints,forge,skill-forge}
ENDSSH
  echo "lab-$i: fresh @ $LOCAL_COMMIT"
done
```

## Phase 1 — Org Setup (DataCorp tenant)
```bash
for i in $(seq 1 $N); do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  ROLE=$([ $i -eq 1 ] && echo "analyst" || ([ $i -eq 2 ] && echo "boardwriter" || echo "qarouter"))
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP bash <<ENDSSH
    cat > /root/.corvin/tenants/datacorp/global/tenant.corvin.yaml <<YAML
spec:
  tenant_id: datacorp
  display_name: DataCorp Analytics
  data_residency: eu
  default_engine: claude_code
  allowed_engines: [claude_code]
  persona: $ROLE
YAML
ENDSSH
done
```

## Phase 2 — Data Setup (Synthetic Spotify CSV)
```bash
python3 - <<'EOF'
import csv, random, pathlib
genres = ["Pop","Rock","Electronic","Hip-Hop","Jazz","Classical","R&B","Metal"]
artists = [f"Artist_{i:03d}" for i in range(50)]
rows = [{"track_id": f"T{random.randint(10000,99999)}", "artist": random.choice(artists),
         "genre": random.choice(genres), "streams": random.randint(1000, 50_000_000),
         "duration_ms": random.randint(120000, 360000), "release_year": random.randint(2018, 2025)}
        for _ in range(2000)]
out = pathlib.Path("/tmp/datacorp_spotify.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(f"Generated {len(rows)} rows → {out}")
EOF
# Register via MCP: data_register(name='spotify_datacorp', path='/tmp/datacorp_spotify.csv')
```

## Phase 3 — Network (A2A Mesh Peering)
```bash
for i in 1 2 3; do
  for j in 1 2 3; do
    [ $i -eq $j ] && continue
    IP_J=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$j")
    IP_I=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
    ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP_I \
      "cd /opt/corvin && corvin-a2a pair lab-node-$j http://$IP_J:7433 --offline-pair" \
      > ~/.corvin/tenants/datacorp/cowork/remote_endpoints/lab-node-$j-from-$i.json
  done
done
echo "Full mesh peering complete (3×3 minus self)"
```

## Phase 4 — Execute Flow Graph

Build in `/app/corvinflow` or run via CLI:
```bash
# Compute on lab-1
corvin-a2a send "lab-node-1" \
  "Analyse /tmp/datacorp_spotify.csv: count streams per genre, output PNG to /tmp/genre_histogram.png, return JSON {genres: {genre: streams}}." \
  --ttl 300 --attach /tmp/datacorp_spotify.csv \
  --schema '{"type":"object","properties":{"genres":{"type":"object"}}}'

# Narrate on lab-2
corvin-a2a send "lab-node-2" \
  "BoardWriter: Summarise the genre analysis for the executive board in exactly 3 bullet points." \
  --ttl 120

# QA fact-check on lab-3
corvin-a2a send "lab-node-3" \
  "QA: Does the narrative match the genre data? Reply JSON {pass: bool, issues: [string]}." \
  --ttl 120 --schema '{"type":"object","properties":{"pass":{"type":"boolean"},"issues":{"type":"array"}}}'
```

## Phase 5 — Verification + Compliance Gate
```bash
# Audit chain on all 3 nodes
for i in 1 2 3; do
  IP=$(HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$i")
  ssh -i "$HETZNER_SSH_PRIVATE_KEY_PATH" root@$IP "voice-audit verify 2>&1 | tail -3"
done
```

**→ Run `code.lab.verify_compliance` now. Do not proceed to teardown until all checks pass.**

## Pass Criteria
- [ ] All 3 nodes: fresh install at same commit, `import operator` OK
- [ ] 2000-row CSV generated, registered as DSI
- [ ] Compute worker: genre JSON + histogram PNG returned
- [ ] Board narrative: exactly 3 bullet points
- [ ] QA fact-check: `{"pass": true, "issues": []}`
- [ ] HITL checkpoint approved in CorvinFlow UI
- [ ] Histogram PNG pinned as L33 artifact
- [ ] **`code.lab.verify_compliance` all checks green** ← required for GREEN

## Phase 6 — Teardown
```bash
for i in 1 2 3; do
  HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server delete "corvin-lab-$i"
done
rm -f /tmp/datacorp_spotify.csv /tmp/genre_histogram.png
echo "DataCorp simulation complete — all nodes torn down"
```
