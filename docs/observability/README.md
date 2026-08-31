# Corvin Observability

The observability phase closes the loop: Corvin runtime state is
projected from the unified audit hash chain into Prometheus
exposition format. There is **no parallel telemetry pipeline** —
the chain is the source of truth, the `/metrics` endpoint is a
read-side projection.

This page is the operator guide: scrape config, auth, dashboard
import.

## Endpoints

| Path | Returns | Auth |
|---|---|---|
| `GET /v1/tenants/{tid}/metrics` | `text/plain; version=0.0.4` | Bearer (same as every other Gateway endpoint) |
| `voice-audit metrics …` | `table` / `json` / `prom` | Operator-side; single-operator CLI |

Optional query param `?since=<duration>` trims the aggregation
window. Duration syntax: `30s`, `5m`, `2h`, `7d`, or a bare
integer (seconds). Garbage returns `400 invalid-since`.

## Metric families (curated)

```
corvin_gateway_runs_total{status}                       counter
corvin_gateway_run_duration_seconds_{bucket,sum,count}  histogram
corvin_gateway_webhooks_total{outcome}                  counter
corvin_gateway_auth_failures_total{reason}              counter
corvin_gateway_cross_tenant_denied_total                counter
corvin_gateway_engine_denied_total{reason}              counter
corvin_gateway_zone_denied_total                        counter
corvin_forge_tools_created_total{persona}               counter
corvin_skills_created_total{scope}                      counter
corvin_dialectic_decisions_total{site,mode,choice}      counter
corvin_consent_drops_total                              counter
corvin_quota_exceeded_total{bundle}                     counter
corvin_path_gate_denied_total{tool_name}                counter
corvin_audit_chain_events_total                         counter
corvin_audit_chain_intact                               gauge
```

Every label value comes from a curated allow-list in
`audit_metrics._ALLOWLIST`. Values outside the set collapse to
`"other"`; cardinality per label ≤ 32. The whitelist is the
structural defence against unbounded series — adding a label
requires an ADR amendment.

## Scrape config (Prometheus)

A per-tenant scrape job per tenant the operator wants to observe.
The bearer token MUST match the tenant; mismatched tokens get 403.

```yaml
scrape_configs:
  - job_name: 'corvin-gateway-acme'
    metrics_path: /v1/tenants/acme/metrics
    scheme: https
    static_configs:
      - targets: ['gateway.example.com:443']
    bearer_token_file: /etc/prometheus/tokens/acme.token
    scrape_interval: 30s
    scrape_timeout: 10s
```

Notes:

- **15 s minimum scrape interval recommended.** The response is
  TTL-cached for 15 s by default (`CORVIN_METRICS_TTL_S` env);
  faster scrapes pay the cache hit but don't get fresher data.
- **No public scrape.** The endpoint is bearer-gated for the same
  reasons every other Gateway endpoint is: a public scrape leaks
  per-tenant activity volume + security-incident cadence to
  anyone with network reach.
- **Per-tenant tokens** — issue via `python -m corvin_gateway.cli
  token issue <tenant> --label prometheus-scrape`. Store the
  plaintext in a Prometheus-side secret (HashiCorp Vault, K8s
  Secret + projected volume, etc.).

## Dashboards

Two Grafana dashboards under `docs/observability/grafana/`:

| File | Purpose |
|---|---|
| `corvin-overview.json` | Gateway health: run throughput, run-duration histogram, webhook delivery, queue depth |
| `corvin-security.json` | Auth failures, cross-tenant denials, engine / zone policy denials, path-gate blocks, consent drops, audit-chain integrity |

Import via the Grafana UI ("Dashboards → Import → Upload JSON
file") or via the HTTP API:

```bash
curl -X POST \
  -H "Authorization: Bearer ${GRAFANA_TOKEN}" \
  -H "Content-Type: application/json" \
  --data @corvin-overview.json \
  https://grafana.example.com/api/dashboards/db
```

Both dashboards expect a Prometheus data source named
`Prometheus`. Adjust the `datasource.uid` field on import if your
data source has a different uid.

## Chain integrity surfaced via gauge

`corvin_audit_chain_intact{tenant_id="…"}` is `1` when
`verify_chain()` returns clean, `0` when the chain has gaps /
hash-mismatches. The security dashboard alerts on
`min_over_time(corvin_audit_chain_intact[5m]) < 1`.

Without this gauge, a chain tamper would only show up on the
once-daily `voice-audit verify` cron — Phase 6's gauge gives
every scraper visibility into chain health within the scrape
window.

## Single-operator path

Operators without Prometheus get the same projection via the
voice-audit CLI:

```bash
# default tenant + table format
voice-audit metrics

# JSON for piping
voice-audit metrics --format json --since 24h | jq

# Prometheus text for ad-hoc curl-based scraping
voice-audit metrics --format prom --tenant acme
```

The CLI uses the same aggregator as the HTTP endpoint; only the
renderer differs.

## What you, as an operator, must NOT do

- **Don't expose `/metrics` publicly.** The bearer gate is
  load-bearing; bypassing it means leaking per-tenant activity to
  anyone with network reach.
- **Don't scrape every second.** The TTL-cache makes 15 s the
  minimum useful resolution; faster scrapes burn CPU re-reading
  the audit chain for no fresher data.
- **Don't grant Prometheus scrape tokens write privileges.** Use a
  dedicated `--label prometheus-scrape` token per tenant; if
  rotated, revoke the old one. The token resolves to the tenant
  and that's the full extent of its trust.
- **Don't add tenant-scope tokens to a shared Grafana data
  source.** One data source → one token → one tenant. Cross-tenant
  rollups belong on a future operator-only endpoint with its own
  auth gate.

## References

- *(see Corvin-ADR repo)* — sub-phase fanout
- `core/gateway/corvin_gateway/audit_metrics.py` — aggregator + renderer
- `core/gateway/corvin_gateway/app.py` — `GET /v1/tenants/{tid}/metrics`
- `operator/voice/scripts/voice_audit.py` — `metrics` subcommand
- CLAUDE.md — Observability (audit-chain projection, complete)
