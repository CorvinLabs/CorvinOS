#!/bin/bash
# a2a_member_pin.sh NAME [off] — make a fresh-system container's console run as a MEMBER install
# for the A2A network gate (a2a.network is member-only, gate 28ad07b54), or remove the pin again.
#
# Nothing in the product changes: a systemd drop-in adds one PYTHONPATH entry whose sitecustomize
# replaces the two licence-tier resolvers with "member" (the repo's own A2A E2E fixture,
# corvin_operator/bridges/shared/a2a_test_tier.py, does the same under pytest). HMAC, pairing,
# every gate and the audit run as shipped. Without the pin a fresh install is Free and refuses
# every A2A exchange with 402 "member seat required" — test_fresh_a2a_two_instances_live.py
# proves THAT path first, then needs the pin for the rest.
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
n="$1"; mode="${2:-on}"
unit_dir=/home/tester/.config/systemd/user/corvin-webui.service.d
pypath="/home/tester/CorvinOS/core/console:/home/tester/CorvinOS/core/gateway:/home/tester/CorvinOS/core/license:/home/tester/CorvinOS/core/compliance:/home/tester/CorvinOS/corvin_operator/forge:/home/tester/CorvinOS/corvin_operator/skill-forge:/home/tester/CorvinOS/core/plugins"
if [ "$mode" = "off" ]; then
    docker exec -u tester "$n" rm -f "$unit_dir/override.conf"
else
    docker exec -u tester "$n" mkdir -p /home/tester/a2a-member-pin "$unit_dir"
    docker cp "$HERE/a2a_member_pin/sitecustomize.py" "$n:/home/tester/a2a-member-pin/sitecustomize.py"
    printf '[Service]\nEnvironment="PYTHONPATH=/home/tester/a2a-member-pin:%s"\n' "$pypath" \
        | docker exec -i -u tester "$n" sh -c "cat > $unit_dir/override.conf"
    docker exec -u root "$n" chown -R tester:tester /home/tester/a2a-member-pin "$unit_dir"
fi
"$HERE/fresh.sh" ex "$n" 'systemctl --user daemon-reload && systemctl --user restart corvin-webui'
echo "$n: member pin $mode"
