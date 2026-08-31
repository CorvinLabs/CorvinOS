# Keycloak smoke for ADR-0007 Phase 3.6

Opt-in operator-side validation: spin up a real Keycloak in Docker,
provision a realm with a service-account client, and run
`keycloak_smoke.py` to verify the gateway accepts the issuer's JWTs
end-to-end.

Hermetic equivalents in `tests/test_oidc.py` and
`tests/test_keycloak_smoke.py` cover the same paths via a stub
HTTP server — they run automatically in `run-all-tests.sh`. This
directory is for the *real* IdP confirmation.

## Quick recipe

```bash
cd core/gateway/scripts
docker compose -f docker-compose.keycloak.yml up -d

# wait ~30 s for Keycloak to finish bootstrapping
curl -fs http://localhost:8080/health/ready

# Bootstrap a realm + client (admin REST API). Tweak the CLIENT_SECRET.
KC_ADMIN_PW=admin-only-for-smoke
ACCESS_TOKEN=$(curl -sf -X POST \
  -d "client_id=admin-cli" \
  -d "grant_type=password" \
  -d "username=admin" -d "password=${KC_ADMIN_PW}" \
  http://localhost:8080/realms/master/protocol/openid-connect/token \
  | jq -r .access_token)

# Create realm 'acme'
curl -sf -X POST -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"realm":"acme","enabled":true}' \
  http://localhost:8080/admin/realms

# Create client 'corvin-acme' with service account
curl -sf -X POST -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"clientId":"corvin-acme","serviceAccountsEnabled":true,
       "directAccessGrantsEnabled":false,
       "publicClient":false,"secret":"smoke-secret-acme"}' \
  http://localhost:8080/admin/realms/acme/clients

# Install the tenant's oidc.yaml on the gateway side
python -m corvin_gateway.cli  # see below; or write the YAML directly
```

Then point the gateway's `<corvin_home>/tenants/acme/global/auth/oidc.yaml`
at the Keycloak realm:

```yaml
apiVersion: corvin/v1
kind: TenantOIDC
metadata:
  id: acme
spec:
  issuers:
    - issuer: http://localhost:8080/realms/acme
      audience: account                 # Keycloak default
      jwks_uri: http://localhost:8080/realms/acme/protocol/openid-connect/certs
      jwks: {keys: []}                  # pinned fallback (Keycloak)
      tenant_claim: azp                 # 'authorized party' = client_id;
                                        # alternatively 'preferred_username'
      allowed_algorithms: [RS256]
      jwks_cache_ttl_s: 300
```

Note: Keycloak's default token `aud` claim is `account`. Adjust the
gateway's `audience` field to match (or configure a Keycloak Token
Mapper to set an explicit audience).

## Run the smoke

```bash
# from this directory, with the gateway running on port 8000
.venv/bin/python keycloak_smoke.py \
  --gateway        http://127.0.0.1:8000 \
  --keycloak       http://127.0.0.1:8080 \
  --tenant         acme \
  --client-id      corvin-acme \
  --client-secret  smoke-secret-acme
```

Exit code 0 on success.

## Tear-down

```bash
docker compose -f docker-compose.keycloak.yml down --volumes
```
