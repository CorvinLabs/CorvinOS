#!/usr/bin/env python3
"""Operator-side smoke test: validate the gateway against a real Keycloak.

ADR-0007 Phase 3.6 — opt-in. NOT auto-run in ``run-all-tests.sh``
because it requires Docker. The hermetic equivalent
(``tests/test_keycloak_smoke.py``) covers the JWKS-URI fetch + SCIM
PATCH paths against a stub HTTP server; this script confirms the
same surface works against a live IdP.

Usage::

    cd core/gateway/scripts
    docker compose -f docker-compose.keycloak.yml up -d
    # wait ~30 s for Keycloak to bootstrap
    .venv/bin/python keycloak_smoke.py

Exit code 0 on success; non-zero on any verification failure.

What this script does NOT do:
* Provision realms / clients in Keycloak — the operator runs the
  Keycloak Admin REST API themselves. See the README in this dir
  for the recipe (or use the bundled ``kc_provision.sh``).
* Run the gateway itself. Spin it up via
  ``uvicorn corvin_gateway.app:app`` in another shell.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _eprint(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the Corvin Gateway against a live Keycloak.",
    )
    parser.add_argument(
        "--gateway",
        default="http://127.0.0.1:8000",
        help="Gateway base URL (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--keycloak",
        default="http://127.0.0.1:8080",
        help="Keycloak base URL (default: http://127.0.0.1:8080).",
    )
    parser.add_argument(
        "--tenant", default="acme",
        help="Tenant id to verify against (default: acme).",
    )
    parser.add_argument(
        "--client-id", default="corvin-acme",
        help="Keycloak client id with a service-account binding.",
    )
    parser.add_argument(
        "--client-secret", required=True,
        help="Client secret for the service-account grant.",
    )
    args = parser.parse_args(argv)

    # We import httpx lazily so importing this module on a system
    # without httpx doesn't error.
    try:
        import httpx
    except ImportError:
        _eprint("ERROR: httpx is required. "
                "Run core/gateway/bootstrap.sh first.")
        return 1

    # 1. Fetch a service-account access token from Keycloak
    token_url = f"{args.keycloak}/realms/{args.tenant}/protocol/openid-connect/token"
    _eprint(f"[smoke] POST {token_url}")
    r = httpx.post(
        token_url,
        data={
            "grant_type":     "client_credentials",
            "client_id":      args.client_id,
            "client_secret":  args.client_secret,
        },
        timeout=10.0,
    )
    if r.status_code != 200:
        _eprint(f"ERROR: Keycloak token endpoint returned "
                f"{r.status_code}: {r.text}")
        return 1
    access_token = r.json()["access_token"]
    _eprint(f"[smoke] got access token (length={len(access_token)})")

    # 2. Hit /healthz unauthenticated to confirm the gateway is up
    h = httpx.get(f"{args.gateway}/healthz", timeout=5.0)
    if h.status_code != 200:
        _eprint(f"ERROR: gateway /healthz returned {h.status_code}")
        return 1

    # 3. Issue a no-op run with the OIDC token
    run_url = f"{args.gateway}/v1/tenants/{args.tenant}/runs"
    body = {
        "apiVersion": "corvin/v1",
        "kind":       "Run",
        "spec":       {"persona": "docs", "input": "keycloak-smoke"},
    }
    sr = httpx.post(
        run_url,
        json=body,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    if sr.status_code != 202:
        _eprint(f"ERROR: POST /runs returned {sr.status_code}: {sr.text}")
        return 1
    run_id = sr.json()["run_id"]
    _eprint(f"[smoke] run accepted: {run_id}")

    # 4. Poll until terminal
    poll_url = f"{run_url}/{run_id}"
    deadline = time.time() + 60.0
    final = None
    while time.time() < deadline:
        rs = httpx.get(
            poll_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5.0,
        )
        if rs.status_code == 200 and rs.json()["status"] in (
            "completed", "failed", "budget_exceeded",
        ):
            final = rs.json()
            break
        time.sleep(0.5)
    if final is None:
        _eprint("ERROR: run never reached terminal state in 60 s")
        return 1
    _eprint(f"[smoke] run terminal: status={final['status']}")
    if final["status"] != "completed":
        _eprint(f"ERROR: expected status=completed, got {final}")
        return 1

    # 5. Cross-tenant denial check: hit another tenant URL with the
    #    same token. Skip if --tenant is "globex" (would need a
    #    second tenant in Keycloak); the smoke is opportunistic.
    other = "globex" if args.tenant == "acme" else "acme"
    cross = httpx.get(
        f"{args.gateway}/v1/tenants/{other}/runs/{run_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=5.0,
    )
    if cross.status_code not in (401, 403):
        _eprint(f"ERROR: cross-tenant call should return 401/403, "
                f"got {cross.status_code}")
        return 1
    _eprint(f"[smoke] cross-tenant {other} correctly denied "
            f"({cross.status_code})")

    _eprint("[smoke] OK -- Keycloak/gateway round-trip succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
