#!/usr/bin/env bash
# Deploy the public A2A relay (ADR-0258 Stage 3, ADR-2057) to Railway.
#
# The relay is corvin_operator/bridges/shared/a2a_relay.py and nothing else: a
# dumb, AEAD-blind pipe that needs only FastAPI. This script stages that one
# module next to requirements.txt + Procfile in a temp dir and runs
# `railway up` there, so what runs in production is exactly the module in this
# commit. Requires `railway login` and the project `corvin-a2a-relay`.
#
#   ops/a2a-relay/deploy.sh            # deploy + verify /healthz
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp "$repo/corvin_operator/bridges/shared/a2a_relay.py" "$here/requirements.txt" "$here/Procfile" "$stage/"
printf '%s\n' "$(git -C "$repo" rev-parse HEAD)" > "$stage/RELAY_COMMIT"
cd "$stage"
railway link --project corvin-a2a-relay --service corvin-a2a-relay >/dev/null
railway up --detach --service corvin-a2a-relay
url="https://corvin-a2a-relay-production.up.railway.app/healthz"
for _ in $(seq 1 60); do
  sleep 5
  if curl -fsS -m 5 "$url" >/dev/null 2>&1; then
    echo "relay healthy: $(curl -s -m 5 "$url")"
    exit 0
  fi
done
echo "relay did not become healthy at $url" >&2
exit 1
