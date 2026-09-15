---
name: code_lab_verify_compliance
description: Mandatory post-phase verification: EU AI Act/GDPR compliance, L16 audit chain integrity on all nodes, secret/key safety (0600 perms, no leaks in logs or audit). Run after every integration test phase — never skip.
---

# Mandatory Compliance + Audit-Chain + Key Safety Verification

**Run after EVERY integration test phase.** Never declare a test green without this.
Covers: EU AI Act · GDPR · L16 audit chain · L19 disclosure · L34 data classification · key/secret safety.

## Setup
```bash
SSH="ssh -i $HETZNER_SSH_PRIVATE_KEY_PATH"
IP() { HCLOUD_TOKEN="$HETZNER_API_TOKEN" ~/.local/bin/hcloud server ip "corvin-lab-$1"; }
CONSOLE=${CONSOLE:-http://localhost:8080}
N=${N:-3}
```

---

## Block 1 — EU AI Act + GDPR Compliance

### 1a. Actor documents: is_ai always true (EU AI Act Art. 50)
```bash
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  IS_AI=$($SSH root@$IP_I python3 -c "
import sys; sys.path.insert(0,'/opt/corvin')
from operator.bridges.shared.social_actor import load_actor_document
doc = load_actor_document('/root/.corvin', 'datacorp')
print(doc.get('is_ai', 'MISSING'))")
  [ "$IS_AI" = "True" ] && echo "lab-$i is_ai: OK" || echo "lab-$i is_ai: FAIL ($IS_AI)"
done
```

### 1b. Bot disclosure card intact (L19 — structural lock)
```bash
# Verify disclosure module importable and card text contains AI-nature statement
python3 -c "
import sys; sys.path.insert(0, '$(git -C /home/shumway/projects/CorvinOS rev-parse --show-toplevel)')
from operator.bridges.shared import disclosure
src = open(disclosure.__file__).read()
assert 'is_ai' in src or 'AI' in src, 'disclosure card missing AI statement'
print('disclosure: OK')"
```

### 1c. Consent gate deny-by-default (L16 Phase 4 — GDPR Art. 6/7)
```bash
python3 -c "
import sys; sys.path.insert(0, '$(git -C /home/shumway/projects/CorvinOS rev-parse --show-toplevel)')
from operator.bridges.shared.consent import is_granted
# Without explicit grant, must return False
import unittest.mock as m
with m.patch('operator.bridges.shared.consent._load_consent', return_value=None):
    result = is_granted('test-chan', 'test-chat', 'unknown-uid')
assert result is False, f'consent deny-by-default BROKEN: got {result}'
print('consent deny-by-default: OK')"
```

### 1d. Engine allowlist in tenant config (EU AI Act Art. 14)
```bash
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  ALLOWED=$($SSH root@$IP_I "python3 -c \"
import yaml, sys
cfg = yaml.safe_load(open('/root/.corvin/tenants/datacorp/global/tenant.corvin.yaml'))
print(cfg['spec'].get('allowed_engines', 'MISSING'))\"")
  echo "lab-$i allowed_engines: $ALLOWED"
  [[ "$ALLOWED" == *"claude_code"* ]] || echo "  WARNING: claude_code not in allowlist"
done
```

---

## Block 2 — Audit Chain Integrity (L16)

### 2a. voice-audit verify on all nodes (hash chain continuity)
```bash
AUDIT_FAIL=0
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  RC=$($SSH root@$IP_I "voice-audit verify > /tmp/audit_verify.log 2>&1; echo \$?")
  if [ "$RC" = "0" ]; then
    echo "lab-$i audit chain: OK"
  else
    echo "lab-$i audit chain: FAIL (exit $RC)"
    $SSH root@$IP_I "tail -5 /tmp/audit_verify.log"
    AUDIT_FAIL=$((AUDIT_FAIL+1))
  fi
done
[ "$AUDIT_FAIL" -eq 0 ] && echo "All audit chains green" || echo "AUDIT CHAIN FAILURES: $AUDIT_FAIL"
```

### 2b. Audit event field allowlist — no prompt/output/text/instruction in details
```bash
FORBIDDEN_FIELDS="prompt|output|instruction|transcript|text|password|token|secret"
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  HITS=$($SSH root@$IP_I "grep -E '\"($FORBIDDEN_FIELDS)\"' \
    /root/.corvin/tenants/datacorp/forge/audit.jsonl 2>/dev/null \
    | grep -v '\"event\"' | wc -l")
  [ "$HITS" -eq 0 ] && echo "lab-$i audit fields: OK" \
    || echo "lab-$i audit fields: FAIL ($HITS forbidden fields found)"
done
```

### 2c. audit.jsonl file permissions (GDPR Art. 32)
```bash
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  PERM=$($SSH root@$IP_I "stat -c '%a' /root/.corvin/tenants/datacorp/forge/audit.jsonl 2>/dev/null || echo MISSING")
  [ "$PERM" = "600" ] && echo "lab-$i audit.jsonl perm: OK (0600)" \
    || echo "lab-$i audit.jsonl perm: FAIL ($PERM)"
done
```

---

## Block 3 — Key and Secret Safety

### 3a. Credentials file permissions on all nodes (0600)
```bash
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  for FILE in /root/.claude/.credentials.json /root/.config/corvin-voice/service.env; do
    PERM=$($SSH root@$IP_I "stat -c '%a' $FILE 2>/dev/null || echo MISSING")
    [ "$PERM" = "600" ] && echo "lab-$i $FILE: OK (0600)" \
      || echo "lab-$i $FILE: FAIL ($PERM)"
  done
done
```

### 3b. Local vault permissions (GDPR Art. 32)
```bash
VAULT="$HOME/.config/corvin-voice/secrets.json"
PERM=$(stat -c '%a' "$VAULT" 2>/dev/null || echo MISSING)
[ "$PERM" = "600" ] && echo "local vault: OK (0600)" || echo "local vault: FAIL ($PERM)"
```

### 3c. No API keys in A2A receiver logs
```bash
# Check for known key patterns (first 8 chars of token)
HETZNER_PREFIX="${HETZNER_API_TOKEN:0:8}"
for i in $(seq 1 $N); do
  IP_I=$(IP $i)
  HITS=$($SSH root@$IP_I "grep -c '$HETZNER_PREFIX' /var/log/corvin-a2a.log 2>/dev/null || echo 0")
  [ "$HITS" -eq 0 ] && echo "lab-$i a2a log key leak: OK" \
    || echo "lab-$i a2a log key leak: FAIL ($HITS hits)"
done
```

---

## Summary Gate

```bash
echo "=== Compliance Verification Summary ==="
echo "Run voice-audit verify on each node: see Block 2a above"
echo "Check all FAIL/WARNING lines above — zero failures = GREEN"
echo ""
echo "Required for GREEN:"
echo "  [ ] is_ai: true on all actor docs"
echo "  [ ] consent deny-by-default holds"
echo "  [ ] voice-audit verify exit 0 on all nodes"
echo "  [ ] No forbidden fields in audit details"
echo "  [ ] audit.jsonl mode 0600 on all nodes"
echo "  [ ] .credentials.json + service.env mode 0600 on all nodes"
echo "  [ ] local vault mode 0600"
echo "  [ ] No API key prefix in A2A logs"
```
