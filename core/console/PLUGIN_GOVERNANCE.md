# Plugin Governance & Trust UI

## Overview

The Plugin Governance UI implements ADR-0249 (Plugin Trust Anchor) by providing transparent disclosure of plugin trust levels, permissions, and reporting mechanisms.

## Components

### PluginTrustBadge.tsx

React component that displays:
1. **Trust Level** (builtin | vetted ✓ | community ⚠)
   - Builtin: Blue badge, shipped with CorvinOS
   - Vetted: Green badge with ✓, reviewed by maintainer
   - Community: Orange badge with ⚠, unreviewed third-party

2. **Author Information**
   - Name and optional URL
   - Only for vetted/community (builtin omitted)

3. **Permissions Disclosure**
   - Data Locality: local, EU cloud, US cloud, unknown
   - Network Egress: none, local, external
   - Allowed Hosts: declared egress hosts (if restricted)
   - PII Risk: none, low, medium, high

4. **Rating Display** (read-only in MVP)
   - 0-5 star rating
   - Report count badge
   - Informational only

5. **Report Button**
   - Visible for community & vetted plugins
   - Hidden for builtin plugins
   - Opens report modal

### Report Plugin Modal

Allows users to flag inappropriate/malicious plugins:

**Valid Reasons:**
- `malicious`: Steals data, injects code, etc.
- `inappropriate`: Inappropriate content
- `permission_abuse`: Excessive permissions
- `misrepresentation`: Misrepresented capabilities
- `other`: Other issues

**Details Validation:**
- Required: 10-500 characters
- Enforced at form level (HTML5) and API level

## Backend Integration

### Report Endpoint

```
POST /v1/vibe/plugins/<plugin_id>/report
Content-Type: application/json

{
  "reason": "malicious|inappropriate|permission_abuse|misrepresentation|other",
  "details": "Description of the issue (10-500 characters)"
}

Response (200):
{
  "status": "success",
  "message": "Report submitted. Thank you for reporting this plugin.",
  "report_id": "550e8400-e29b-41d4-a716-446655440000"
}

Error Responses:
- 400: Missing/invalid reason or details
- 500: Server error (audit trail write failure, etc.)
```

### Audit Integration

Reports are recorded as audit events:

```python
event_type: "plugin.reported"
details: {
  "plugin_id": "suspicious-plugin",
  "reason": "malicious",
  "report_id": "uuid"
}
```

**Privacy Note:** The user's detailed report text is NOT included in the audit trail (GDPR Art. 5/6). Only the reason category and report ID are recorded. This prevents leaking user-provided content into the audit chain while maintaining traceability.

## UI Integration

### VibePluginsPanel Updates

The main plugins panel now includes:

1. **Trust Column** in table
   - Shows origin badge for each plugin
   - Color-coded: blue/green/orange

2. **Info Button** (ℹ️)
   - Opens governance drawer
   - Shows full trust information
   - Allows reporting

3. **Governance Drawer**
   - Right-side panel showing all governance details
   - Trust level, author, permissions, rating
   - Report button (if applicable)
   - Persists when switching between plugins

## Data Model

### Plugin Governance Fields

```typescript
interface PluginGovernanceInfo {
  origin: "builtin" | "vetted" | "community";
  author?: string;
  author_url?: string;
  pii_risk: "none" | "low" | "medium" | "high";
  locality: "local" | "eu_cloud" | "us_cloud" | "unknown";
  network_egress: "none" | "local" | "external";
  egress_hosts?: string[];
  requires_consent: boolean;
  rating?: number; // 0-5 stars (read-only)
  report_count?: number;
}
```

## Testing

### Unit Tests

**Python:**
- `tests/test_plugin_governance_e2e.py` — Governance logic tests
- `tests/test_plugin_report_endpoint.py` — Report endpoint validation

**Files Tested:**
- Trust badge display for all origins
- Permission disclosure rendering
- Report reason validation
- Details length validation (10-500 chars)
- Audit event structure
- Error handling (400/500 responses)

### E2E Tests

**Playwright (browser-based):**
- `web-next/tests/e2e/plugin-governance.spec.ts`

**Test Coverage:**
- ✅ Trust badge displays correctly per origin
- ✅ Governance drawer opens/closes
- ✅ Author info displays in drawer
- ✅ Permissions disclosure shows all fields
- ✅ Report button visible for community/vetted
- ✅ Report button hidden for builtin
- ✅ Report modal opens on button click
- ✅ Report validation (reason selection, details length)
- ✅ Report submission succeeds with success message
- ✅ Report submission fails gracefully
- ✅ Star rating displays as read-only
- ✅ Report count badge displays
- ✅ Consent warning shows when required
- ✅ Multiple plugins display trust badges
- ✅ Drawer state maintained when switching plugins

## Compliance Notes

### ADR-0249: Plugin Trust Anchor
- Implements origin display (builtin | vetted | community)
- Implements manifest signature verification (backend)
- Implements community reporting mechanism
- Provides transparent disclosure

### GDPR
- PII Risk disclosure (Art. 13)
- Consent gating for high-risk plugins (Art. 6, 7)
- Audit trail for reporting (Art. 30, 32)
- No user details in audit (Art. 5)

### EU AI Act
- Bot disclosure (trust level)
- Permitted use documentation (permissions)
- Malicious plugin reporting channel

## Future Enhancements

### Phase 2 (Post-MVP)
- [ ] Rating write capability (users can rate)
- [ ] Report aggregation dashboard
- [ ] Automated malicious plugin detection
- [ ] Rate limiting on reports
- [ ] Report status tracking (user-facing)
- [ ] Community moderation tools

### Phase 3 (Long-term)
- [ ] Plugin signature verification UI
- [ ] Cryptographic verification of vetted status
- [ ] Update notifications for reported plugins
- [ ] Automated plugin quarantine on N reports
- [ ] Appeals process for plugin authors

## Configuration

No configuration required. The component uses data from the plugin registry:

```yaml
# From plugin.json manifest
plugin:
  id: my-plugin
  origin: community  # or: builtin, vetted
  pii_risk: medium
  locality: unknown
  network_egress: external
  egress_hosts:
    - api.example.com
  requires_consent: true
```

## Troubleshooting

### Trust Badge Not Showing
- Ensure plugin has `origin` field in manifest
- Check that VibePluginsPanel is loading plugins correctly
- Verify governance data is being passed to PluginTrustBadge

### Report Button Not Visible
- Button only shows for origin=community or origin=vetted
- Builtin plugins cannot be reported
- Verify plugin origin in manifest

### Report Modal Validation Failing
- Reason must be one of: malicious, inappropriate, permission_abuse, misrepresentation, other
- Details must be exactly 10-500 characters
- Check browser console for validation errors

### Audit Event Not Written
- Audit write failures do NOT block report submission
- Check audit logs: `~/.corvin/global/forge/audit.jsonl`
- Verify audit system is running

## References

- **ADR-0249:** Plugin Trust Anchor (provenance + consent)
- **ADR-0233:** Plugin Lifecycle & Registry
- **CLAUDE.md § Plugin Trust Anchor:** Custody procedures
- **compliance-baseline.md:** GDPR/EU AI Act obligations
