---
name: code_lab_integration_social
description: CorvinOS social/org integration test: personal actor bootstrap (L39), org creation + membership + grants (L42), cross-org A2A flow, social graph verification, L18/L19/L20/L21. Mandatory: code.lab.verify_compliance after every phase.
---

# CorvinOrg Social Layer — Integration Test (L18–L21 + L39 + L42)

Companion to `code.lab.integration_sim`. Requires 3 nodes provisioned + code-synced.
Tests everything in the social/org stack with real signed actor documents and REST calls.

**Mandatory after every phase: run `code.lab.verify_compliance`**
(EU AI Act · GDPR · audit chain · key safety). A phase is only green when all
compliance checks pass.

## Prerequisites
```bash
bridge.sh console
CONSOLE=${CONSOLE:-http://localhost:8080}
N=3
SSH="ssh -i $HETZNER_SSH_PRIVATE_KEY_PATH"
IP() { HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$1"; }
```

## Layers covered
L18 Roles · L19 Disclosure · L20 Quota · L21 Proposals
L39 CorvinFed (personal actors, Ed25519) · L42 CorvinOrg (orgs, members, endorsements, grants)

---

## Phase A — Personal Actor Bootstrap (L39 CorvinFed)
```bash
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  ROLE=$([ $i -eq 1 ] && echo "DataAnalyst" || ([ $i -eq 2 ] && echo "BoardWriter" || echo "QARouter"))
  $SSH root@$IP_I python3 - <<PYEOF
import json, sys
sys.path.insert(0, "/opt/corvin")
from operator.bridges.shared.social_actor import generate_keypair_and_save, generate_actor_document
home = "/root/.corvin"
generate_keypair_and_save(home, "datacorp")
doc = generate_actor_document(home, "datacorp",
    display_name="$ROLE (lab-$i)", host="http://$IP_I:7433")
print(json.dumps({"actor_id": doc["instance_id"], "is_ai": doc["is_ai"],
                  "compliance_zone": doc.get("compliance_zone"), "key_type": doc["public_key"]["type"]}))
PYEOF
done
```
Verify: `is_ai: true`, `compliance_zone: eu`, key type `Ed25519` on each node.

---

## Phase B — Org Creation (L42 CorvinOrg)
```bash
curl -sf -X POST $CONSOLE/orgs -H "Content-Type: application/json" \
  -d '{"handle":"datacorp","display_name":"DataCorp Analytics","summary":"Music data analysis org","host":"http://localhost:7433"}' | jq .

curl -sf -X POST $CONSOLE/orgs -H "Content-Type: application/json" \
  -d '{"handle":"insightcorp","display_name":"InsightCorp","summary":"Analytics consumer org","host":"http://localhost:7433"}' | jq .

curl -sf $CONSOLE/orgs | jq '[.[] | {handle, display_name, member_count}]'
```

---

## Phase C — Member + Agent Registration
```bash
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  ACTOR_ID=$($SSH root@$IP_I python3 -c "
import sys; sys.path.insert(0,'/opt/corvin')
from operator.bridges.shared.social_actor import load_actor_document
print(load_actor_document('/root/.corvin', 'datacorp')['instance_id'])")

  curl -sf -X POST $CONSOLE/orgs/datacorp/members \
    -H "Content-Type: application/json" \
    -d "{\"actor_id\":\"$ACTOR_ID\",\"role\":\"agent\"}" | jq .

  curl -sf -X POST $CONSOLE/orgs/datacorp/agents \
    -H "Content-Type: application/json" \
    -d "{\"agent_actor_id\":\"$ACTOR_ID\",\"scope\":[\"domain.*.read\",\"a2a.send\"],\"ttl_days\":30}" | jq .
done
```

---

## Phase D — Cross-Org Grant (InsightCorp ← DataCorp)
```bash
DC_ACTOR=$(curl -sf $CONSOLE/orgs/datacorp | jq -r '.actor.instance_id')

curl -sf -X POST $CONSOLE/orgs/insightcorp/grants \
  -H "Content-Type: application/json" \
  -d "{\"grantee_actor\":\"$DC_ACTOR\",\"capabilities\":[\"domain.analytics.read\",\"a2a.send\"],\"conditions\":{}}" | jq .

curl -sf $CONSOLE/orgs/insightcorp/grants | jq '[.[] | {grant_id, grantee_actor, capabilities}]'
```

---

## Phase E — Social Graph Verification
```bash
curl -sf "$CONSOLE/orgs/datacorp/network" | jq '{
  node_count: (.nodes | length),
  edge_count: (.edges | length),
  node_types: [.nodes[].type] | unique,
  edge_types: [.edges[].type] | unique
}'
```

Open `/app/corvinflow` → Organisation tab → `datacorp`:
verify 4 nodes (1 org + 3 agents), agent→org edges, physics stabilises,
node click → detail panel with actor_id + scope caps.

---

## Phase F — Cross-Org A2A Flow
```bash
corvin-a2a send "lab-node-1" \
  "DataCorp analyst commissioned by InsightCorp: return top 3 genres by streams from /tmp/datacorp_spotify.csv as JSON {rankings:[{genre,streams}]}." \
  --ttl 180 --schema '{"type":"object","properties":{"rankings":{"type":"array"}}}'

corvin-a2a send "lab-node-2" \
  "InsightCorp received this analysis. Write a 2-sentence executive summary." \
  --ttl 120
```
Verify: reply signed by `datacorp` agent; `instance_id_match: true` in A2A audit event.

---

## Phase G — L18/L19/L20/L21 Tests
```bash
# L18 — role check
curl -sf $CONSOLE/members | jq '[.[:3][] | {chat_key, member_count}]'

# L19 — disclosure shown_at present
curl -sf $CONSOLE/members | jq '.[0].chat_key' -r | xargs -I{} \
  curl -sf "$CONSOLE/members/{}" | jq '[.[] | {uid, disclosed: .disclosure.shown_at}]'

# L20 — quota record present
curl -sf $CONSOLE/members | jq '.[0]'

# L21 — proposal flow
# /propose "Extend analysis to artist popularity" → /go → verify task executed
```

**→ Run `code.lab.verify_compliance` now. Do not tear down until all checks pass.**

---

## Phase H — Teardown
```bash
for handle in datacorp insightcorp; do
  EID_LIST=$(curl -sf $CONSOLE/orgs/$handle | jq -r '.agents[].endorsement_id')
  for eid in $EID_LIST; do curl -sf -X DELETE "$CONSOLE/orgs/$handle/agents/$eid"; done
  curl -sf -X DELETE "$CONSOLE/orgs/$handle"
done
curl -sf $CONSOLE/orgs | jq 'length'  # must be 0
```

---

## Pass Criteria — Social Layer
- [ ] All 3 nodes: actor doc `is_ai: true`, `compliance_zone: eu`, Ed25519
- [ ] `datacorp` + `insightcorp` orgs created
- [ ] 3 agents affiliated (scope: domain.*.read + a2a.send, 30d TTL)
- [ ] Cross-org grant: DataCorp → InsightCorp with `domain.analytics.read`
- [ ] `/orgs/datacorp/network` → 4 nodes, member + agent + grant edges
- [ ] Social graph: physics stable, search works, node detail panel correct
- [ ] Cross-org A2A: `rankings` JSON returned, signed by DataCorp agent
- [ ] L19 `disclosure.shown_at` present, L21 proposal consumed via `/go`
- [ ] Orgs dissolved cleanly, `GET /orgs` → length 0
- [ ] **`code.lab.verify_compliance` all checks green** ← required for GREEN
