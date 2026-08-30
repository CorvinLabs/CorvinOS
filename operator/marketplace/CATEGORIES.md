# Marketplace Plugin Categories — ADR-0511

**Reference:** ADR-0511 (Marketplace Plugin-First Architecture)  
**Derived from:** CorvinOS Layer Stack Analysis (L4, L10, L16, L25, L28, L34-36, L38, ADR-0243, ADR-0314-0321)

---

## Category Taxonomy

### 1. **Memory**
**Purpose:** Session recall, user modeling, learning systems, context preservation

**Scope:**
- Embeddings-based session recall (L28)
- User preference modeling
- Learning event collection (confidence, feedback, outcomes — ADR-0314-0321)
- Cross-session context (conversation snippets, user insights)

**CorvinOS Layer Reference:** L28 (Conversation Recall + User Modeling), ADR-0314–0321 (Learning Infrastructure)

**Example Plugins (to be added):**
- `plugin:buildin-memory-cel_session_memory` — CEL-based embeddings recall
- `plugin:buildin-memory-user_model` — User preference tracking
- `plugin:buildin-memory-learning_events` — Learning event persistence
- `plugin:contributor-memory-custom_recall` — Community custom recall strategy

**Key Contracts:**
- Session ID → embedding table lookup
- User profile → preference values
- Turn ID → learning event record

---

### 2. **Security & Compliance**
**Purpose:** Authentication, authorization, audit trail, encryption, consent, data governance

**Scope:**
- Consent gating (GDPR Art. 6, 7 — L16)
- Audit trail hash-chaining (GDPR Art. 30, 32 — L16)
- Path-gate (FS-write protection — L10)
- Flow guard (data classification — L34)
- Encryption primitives (L32)
- Attestation (A2A security — L38)

**CorvinOS Layer Reference:** L10 (Path-Gate), L16 (Audit + Consent), L32 (Encryption), L34 (Flow Guard), L38 (RemoteTrigger + A2A), ADR-0232–0233 (Plugin Registry + Boot-Layer)

**Example Plugins (to be added):**
- `plugin:buildin-security_compliance-consent_gate` — GDPR consent enforcement
- `plugin:buildin-security_compliance-audit_chain` — Hash-chained audit log
- `plugin:buildin-security_compliance-path_gate` — Filesystem write protection
- `plugin:buildin-security_compliance-flow_guard` — Data flow classification
- `plugin:contributor-security_compliance-custom_auth_saml` — Community SAML plugin

**Key Contracts:**
- Consent state → gate decision
- Audit event → hash chain validation
- Data path → classification label

---

### 3. **Integration**
**Purpose:** Extension points, bridges, MCP servers, multi-persona coordination, webhooks

**Scope:**
- Hook registry (pre/post request hooks — L4)
- Cowork hub (multi-persona coordination — L4)
- Bridge manager (Discord/Slack/Telegram/HTTP — L38)
- MCP server registry (tool delegation — L6)
- Webhook bridges (asynchronous notifications)
- Custom extension points

**CorvinOS Layer Reference:** L4 (Cowork + Hooks), L6 (Forge + MCP), L38 (Bridge Registry + RemoteTrigger), ADR-0243 (Plugin Boot-Layer Taxonomy)

**Example Plugins (to be added):**
- `plugin:buildin-integration-hook_registry` — Pre/post request hooks
- `plugin:buildin-integration-cowork_hub` — Multi-persona routing
- `plugin:buildin-integration-bridge_manager` — Bridge lifecycle
- `plugin:buildin-integration-mcp_server` — MCP protocol integration
- `plugin:contributor-integration-custom_webhook` — Community webhook bridge

**Key Contracts:**
- Hook ID → handler invocation
- Bridge type → router selection
- MCP method → tool delegation

---

### 4. **Data Processing**
**Purpose:** Extraction, classification, transformation, anonymization, ETL pipelines

**Scope:**
- Artifact extraction (from logs, transcripts, databases — L25)
- PII classification (detection, masking — L34)
- Anonymization transforms (bucketing, hashing, tokenization — L36)
- Data snapshot/restore (big-data staging — L25)
- Compute worker delegation (L25)

**CorvinOS Layer Reference:** L25 (Data Snapshot + Compute Worker), L34 (Data Classification + Flow Guard), L36 (Anonymization + Erasure), ADR-0036 (Data Residency)

**Example Plugins (to be added):**
- `plugin:buildin-data_processing-artifact_extraction` — Extract from conversational logs
- `plugin:buildin-data_processing-pii_classifier` — Detect PII patterns
- `plugin:buildin-data_processing-anonymization` — Remove/mask sensitive data
- `plugin:buildin-data_processing-snapshot_compute` — Prepare data for batch jobs
- `plugin:contributor-data_processing-custom_etl` — Community ETL pipeline

**Key Contracts:**
- Data shape → extraction template
- Field type → classification rule
- Anonymization rule → transform function

---

### 5. **Observability**
**Purpose:** Telemetry, health monitoring, diagnostics, self-healing, alerting

**Scope:**
- Telemetry collection (anonymous pings, error signals — L35)
- Heartbeat emission (5-min cadence, presence tracking — L36)
- Health diagnostics (system state, error classification — ACO L5)
- Self-repair actions (automated healing — L36, ADR-0178)
- Alerting/escalation (operator notification)
- Metrics aggregation (KPI dashboards)

**CorvinOS Layer Reference:** L35 (Network Egress Lockdown), L36 (Telemetry + Heartbeat), ACO L5 (Self-Repair), ADR-0178 (Self-Improvement), ADR-0186 (Heartbeat Protocol)

**Example Plugins (to be added):**
- `plugin:buildin-observability-telemetry_collector` — Anonymous usage telemetry
- `plugin:buildin-observability-heartbeat_emitter` — Presence heartbeat (5-min)
- `plugin:buildin-observability-health_diagnostics` — System health scoring
- `plugin:buildin-observability-aco_remediation` — ACO L5 self-repair actions
- `plugin:contributor-observability-custom_alerting` — Community Slack/PagerDuty alerts

**Key Contracts:**
- Event type → signal filtering
- Health metric → threshold rule
- Repair action → precondition + apply + undo

---

## Category Placement Decision Matrix

**Use this to decide which category a plugin belongs in:**

| Question | Yes → Category | No → Consider |
|----------|---|---|
| Does it remember/persist user state across turns? | **Memory** | Observability |
| Does it enforce rules/gates/compliance? | **Security** | Integration |
| Does it connect to external systems? | **Integration** | Data Processing |
| Does it transform/mask/extract data? | **Data Processing** | Security |
| Does it monitor/alert/heal the system? | **Observability** | Memory |

**Example:** A plugin that "logs all API calls, detects anomalies, sends alerts"
- Primary: Observability (anomaly detection + alerting)
- Secondary: Security (audit logging)
- → Classify as **Observability**, document security aspects in description

---

## Adding New Categories (Future)

**When to add a category:**
- ≥ 5 planned plugins (minimum cluster)
- Distinct from existing 5 categories
- Needs separate discovery/filtering UX

**Process:**
1. Propose in ADR (related to ADR-0511)
2. Add row to this matrix
3. Update generate_index.py
4. Create `plugins/buildin/[new_category]/` + `plugins/contributor/[new_category]/`
5. Bump plugin-schema.json enum

---

## Discovery UX (Phase 2 UI)

### Browse View
- Category tabs at top (5 tabs)
- Within each tab: grid of plugin cards
- Filter by tier (Buildin / Contributor)
- Search within category

### Search Results
- Facet by category (left sidebar)
- Facet by tier (left sidebar)
- Facet by status (Installed / Available)

### Detail View
- Prominent category label
- Tier badge (Supported / Community)
- Related plugins in same category

---

## Categorization Review Checklist

Before merging a plugin.json:

- [ ] Category field matches one of the 5 defined categories
- [ ] Category is appropriate for plugin purpose (use decision matrix above)
- [ ] Plugin description explains why it's in this category
- [ ] Category examples in docs include this plugin (or docs updated)
- [ ] No "other" category used (must fit one of 5)

---

**Last Updated:** 2026-08-30  
**Related:** ADR-0511, plugin-schema.json  
**Owner:** Marketplace architecture (ADR-0511 authors)
