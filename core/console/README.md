# corvin-console

Owner-self-service web UI for Corvin. Mounted onto the existing
`corvin-gateway` ASGI app under `/v1/console/*` (REST) + `/console/*`
(SPA). Opt-in: a fresh Corvin install does not bootstrap this
plugin.

See [`docs/decisions/0015-console-self-service-ui.md`](../../docs/decisions/0015-console-self-service-ui.md)
for the architecture decision and
[`outputs/corvin-console-konzept.md`](../../outputs/corvin-console-konzept.md)
for the original design document.

## Status

All seven phases shipped:

| Phase | Inhalt | Status |
|---|---|---|
| **A** | Plugin skeleton + auth bridge + dashboard | ✓ |
| **B** | Read-only viewers (Sessions, Audit, Runs, Personas) | ✓ |
| **C** | Drilldowns (Persona-, Tool-, Skill-detail, Memory) | ✓ |
| **D** | Realtime SSE (Agents-Live + Audit-Live) | ✓ |
| **E** | Mutations (Memory edit, Persona edit, Tool/Skill promote) | ✓ |
| **F** | Compute / Workspaces / Members / Settings (read-only) | ✓ |
| **E2** | Settings-Editor mit YAML/JSON-Validation + Re-Auth | ✓ |
| **G** | Closure (`/healthz`, ADR-0015, README) | ✓ |

13 sections live in the SPA — none disabled.

> **Console (ADR-0037).** The single console frontend is the Vite +
> React + Tailwind + shadcn app under `corvin_console/web-next/`. The
> legacy vanilla-JS SPA and the `CORVIN_CONSOLE_UI` selection switch
> have been removed — `web-next/` is the only UI.

## Install

```bash
bash core/console/bootstrap.sh
```

Creates `.venv/`, installs FastAPI + itsdangerous + PyYAML + PyJWT
+ pytest. When `npm` is on the PATH the bootstrap also builds the
frontend (`npm ci && npm run build` → `web-next/dist/`). Opt out with
`CORVIN_SKIP_WEBNEXT_BUILD=1`. Without a built `dist/`, the SPA is not
mounted (the `/v1/console` REST API stays available regardless).

### Working on web-next/ locally

```bash
cd core/console/corvin_console/web-next
npm install
npm run dev          # http://127.0.0.1:5173, proxies /v1 to gateway :8765
```

## Run

The console mounts onto the gateway's ASGI app automatically when
both plugin trees are present. To start the gateway with both:

```bash
PYTHONPATH="core/console:core/gateway:operator/forge:operator/skill-forge" \
  core/console/.venv/bin/python -m uvicorn \
  corvin_gateway.app:app --host 127.0.0.1 --port 8765
```

Endpoints come up at:
- `http://127.0.0.1:8765/console/`        — SPA
- `http://127.0.0.1:8765/v1/console/...`  — REST API
- `http://127.0.0.1:8765/v1/console/healthz` — liveness probe (unauth)

## Issue an owner token

Console accepts the same tenant-tier bearer-tokens the gateway
already issues (`atlr_<32 hex>`, ADR-0007 Phase 2.1):

```python
from corvin_gateway import auth
plain = auth.issue_token("_default", label="owner-console")
print(plain)  # shown ONCE; revoke + reissue if lost
```

Operator tokens (`atlr_op_*`) are explicitly rejected at
`/auth/login` — the console is tenant-tier only.

## Architecture summary

- **Tenant-tier auth only** — `atlr_<32 hex>`. Operator-tier
  tokens (`atlr_op_*`) are rejected.
- **Session cookies** — `corvin_console_sid` (HttpOnly,
  SameSite=Strict). 1 h idle, 8 h absolute.
- **CSRF** — per-session secret, HMAC-derived `X-CSRF-Token`
  header on every mutation. GET exempt.
- **Re-Auth** — Memory-Edit, Persona-Edit, Tool/Skill-Promote and
  Settings-Edit all require the bearer token typed fresh in the
  request body. Constant-time fingerprint compare.
- **Audit chain** — five new event types (`console.session_started`,
  `console.session_ended`, `console.session_denied`,
  `console.action_performed`, `console.action_failed`) feed the
  unified hash chain.
- **React SPA** — Vite + React + Tailwind + shadcn (ADR-0037),
  built to `web-next/dist/`. BrowserRouter with index.html fallback.

## Sections (sidebar order)

1. Dashboard — health-card with bridges-matrix, engine, STT chain, audit-chain status, today's counts
2. Agents Live — SSE stream filtered to `gateway.* · bridge.* · tool.* · skill.* · console.* · compute.* · voice.*`
3. Sessions — every bridge-session on disk
4. Audit Tail — hash-chain viewer with severity + event-prefix filter, ▶ Live toggle for SSE
5. Runs (Gateway) — gateway-submitted runs with status + duration
6. Personas — bundle + user-override; per-persona drilldown with full JSON, edit if user-scope
7. Tools (Forge) — multi-scope aggregation; per-tool detail with input_schema + impl preview; Promote
8. Skills — Skill-Forge inventory across all scopes; Body, grade-history; Promote
9. Memory — auto-memory store browser; Create / Edit / Delete with Re-Auth (MEMORY.md protected)
10. Compute Worker — Layer-25 socket probe + per-tenant run-list with manifest + summary
11. Workspaces — read-only filesystem tree-browser of the tenant tree, 🔒-marked path-gate-protected entries
12. Members — fan-in of roles + quota + consent + disclosure across all chats
13. Settings — six known config files (`tenant.corvin.yaml`, `data_policy.yaml`, `ldd.json`, `dialectic.json`, `relay.json`, `branding.yaml`); structural validation on Edit

## What this plugin must NOT do

See ADR-0015's "must NOT do" section. Headlines:

- Don't bypass any compliance gate (Disclosure / Consent /
  Path-Gate / Engine-Policy / Audit-Chain stay structurally
  unchanged).
- Don't accept operator tokens.
- Don't write cleartext tokens / SIDs / CSRF secrets / file content
  to the audit chain.
- Don't introduce passwords or JWT signing keys.
- Don't write outside the six known settings files.
- Don't drop the `MEMORY.md` protection on DELETE.
- Don't accept Persona PUT against a bundle persona without a
  prior `copy-from-bundle` POST.
- Don't introduce `prometheus_client` or websockets — SSE covers
  every realtime need.

## Dev convenience

Run the Vite dev server (`npm run dev` in `web-next/`, proxies `/v1`
to the gateway on :8765) for local frontend work with hot reload.
