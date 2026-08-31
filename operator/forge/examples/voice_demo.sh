#!/usr/bin/env bash
# voice_demo.sh — single-command end-to-end demo of the forge runtime
# tool factory in the voice-skill repo. Spawns the MCP server in an
# isolated FORGE_ROOT, registers a deterministic echo tool, calls it,
# inspects the on-disk audit chain.
#
# Run:
#   bash operator/forge/examples/voice_demo.sh
#
# Cleans up after itself. No live audit log is touched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/forge-voice-demo.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

export FORGE_ROOT="$TMP"
export VOICE_AUDIT_PATH="$TMP/audit.jsonl"

cyan() { printf '\033[1;36m%s\033[0m\n' "$1"; }
green() { printf '\033[1;32m%s\033[0m\n' "$1"; }

cyan "=== forge voice demo ==="
echo "isolated workspace : $FORGE_ROOT"
echo

cyan "[1/5] spawn forge MCP server"
python3 "$ROOT/forge.py" mcp --permission-mode yes >"$TMP/mcp.stdout" 2>"$TMP/mcp.stderr" <<'EOF' &
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"voice-demo","version":"0.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"forge_tool","arguments":{"name":"echo_demo","description":"echo the input string","input_schema":{"type":"object","required":["msg"],"properties":{"msg":{"type":"string"}}},"impl":"#!/usr/bin/env python3\nimport json,sys\np=json.loads(sys.stdin.read())\nprint(json.dumps({\"data\":{\"echoed\":p[\"msg\"]}}))","meta":{"deterministic":true,"side_effects":false}}}}
{"jsonrpc":"2.0","id":4,"method":"tools/list"}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"echo_demo","arguments":{"msg":"hallo aus dem demo"}}}
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"echo_demo","arguments":{"msg":"hallo aus dem demo"}}}
EOF
wait
echo "  ✓ MCP server ran $(grep -c '"id":' "$TMP/mcp.stdout") JSON-RPC responses"

cyan "[2/5] check tools/list before/after forge"
before=$(python3 -c "import json,sys; d=open('$TMP/mcp.stdout').read().splitlines(); r=[l for l in d if '\"id\":2' in l][0]; print(len(json.loads(r)['result']['tools']))")
after=$(python3 -c "import json,sys; d=open('$TMP/mcp.stdout').read().splitlines(); r=[l for l in d if '\"id\":4' in l][0]; print(len(json.loads(r)['result']['tools']))")
echo "  before forge_tool: $before tools (forge_tool, forge_promote)"
echo "  after  forge_tool: $after tools (+ echo_demo)"
test "$after" = "3"

cyan "[3/5] first call: real sandbox; second call: cache hit"
sandbox1=$(python3 -c "import json; d=open('$TMP/mcp.stdout').read().splitlines(); r=[l for l in d if '\"id\":5' in l][0]; print(json.loads(r)['result']['structuredContent']['sandbox'])")
sandbox2=$(python3 -c "import json; d=open('$TMP/mcp.stdout').read().splitlines(); r=[l for l in d if '\"id\":6' in l][0]; print(json.loads(r)['result']['structuredContent']['sandbox'])")
echo "  call 1 sandbox = $sandbox1"
echo "  call 2 sandbox = $sandbox2"
test "$sandbox1" = "bwrap" -o "$sandbox1" = "rlimits"
test "$sandbox2" = "cache"

cyan "[4/5] audit chain"
audit_file="$TMP/audit.jsonl"
# bridge audit.py defaults to FORGE_ROOT/audit.jsonl when forge runs;
# the voice-audit CLI verifies it.
python3 "$ROOT/../voice/scripts/voice_audit.py" --path "$FORGE_ROOT/audit.jsonl" verify
echo "  events recorded: $(wc -l < "$FORGE_ROOT/audit.jsonl")"
echo "  event types:"
python3 -c "
import json
for line in open('$FORGE_ROOT/audit.jsonl'):
    rec = json.loads(line)
    print(f\"    {rec['severity']:8s}  {rec['event_type']}\")"

cyan "[5/5] tampering proof — flip one byte, verify catches it"
python3 -c "
import json
p = '$FORGE_ROOT/audit.jsonl'
lines = open(p).read().splitlines()
rec = json.loads(lines[0])
rec['tool'] = 'evil_persona'
lines[0] = json.dumps(rec)
open(p, 'w').write('\n'.join(lines) + '\n')
"
if python3 "$ROOT/../voice/scripts/voice_audit.py" --path "$FORGE_ROOT/audit.jsonl" verify >"$TMP/verify.stdout" 2>"$TMP/verify.stderr"; then
  echo "  ✗ verify did NOT catch the tamper — RED" >&2
  exit 1
else
  echo "  ✓ verify exit 1, integrity violation reported:"
  grep -E "line|tampered" "$TMP/verify.stderr" | head -2 | sed 's/^/    /'
fi

green "=== voice forge demo OK ==="
