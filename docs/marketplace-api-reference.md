# Custom Repository API Reference

## Base URL

All endpoints are relative to `https://your-corvin-instance/v1/console/api/v1/marketplace`.

The `/v1/console` segment is the console mount — the gateway includes the console
router under it, so a path written without it does not exist. The console SPA
builds these URLs from `BASE` (`src/lib/api/client.ts`) for exactly that reason.

Authentication is the console session cookie; every endpoint resolves the tenant
from that session, never from an environment variable.

## Endpoints

### GET /custom-repositories

List all custom repositories for the current tenant.

**Response:**
```json
{
  "repositories": [
    {
      "repo_url": "https://github.com/owner/repo",
      "status": "healthy",
      "extension_count": 5,
      "error_message": null,
      "last_checked": "2026-08-30T10:00:00Z",
      "enabled": true
    }
  ]
}
```

**Status codes:**
- `200`: Success
- `500`: Server error

---

### POST /custom-repositories

Add a new custom repository.

**Request:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "token_ref": "ghp_xxxxxxxxxxxx"  // optional, for private repos
}
```

**Response:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "status": "healthy",
  "extension_count": 5,
  "error_message": null,
  "last_checked": "2026-08-30T10:00:00Z"
}
```

**Status codes:**
- `200`: Repository added successfully
- `400`: Invalid URL, duplicate, or token error (see error_message)
- `401`: Unauthorized (invalid or expired token)
- `404`: Repository not found
- `429`: GitHub rate limited
- `500`: Server error

---

### POST /custom-repositories/validate

Validate a repository URL (without adding it).

**Request:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "token_ref": "ghp_xxxxxxxxxxxx"  // optional
}
```

**Response:**
```json
{
  "valid": true,
  "repo_url": "https://github.com/owner/repo"
}
```

**A malformed URL is a `200` with `valid: false`, not a `4xx`.** The form
revalidates on every typing pause, so a status code per character would be noise
rather than signal:

```json
{ "valid": false, "error": "Invalid GitHub repository URL: ..." }
```

**Status codes:**
- `200`: Validation ran — read `valid` for the verdict
- `400`: `repo_url` missing from the request
- `500`: Validation could not run
- `503`: Custom repository backend unavailable

---

### PATCH /custom-repositories

Update a repository (enable/disable).

**Request:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "enabled": false  // true to enable, false to disable
}
```

**Response:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "enabled": false,
  "status": "healthy"
}
```

**Status codes:**
- `200`: Updated
- `404`: Repository not found
- `500`: Server error

---

### DELETE /custom-repositories

Remove a repository.

**Request:**
```json
{
  "repo_url": "https://github.com/owner/repo"
}
```

**Response:**
```json
{
  "deleted": true,
  "repo_url": "https://github.com/owner/repo"
}
```

**Status codes:**
- `200`: Deleted
- `404`: Repository not found
- `500`: Server error

---

### POST /custom-repositories/refresh

Manually refresh a repository's metadata.

**Request:**
```json
{
  "repo_url": "https://github.com/owner/repo"
}
```

**Response:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "status": "healthy",
  "extension_count": 5,
  "error_message": null,
  "last_checked": "2026-08-30T10:01:00Z"
}
```

**Status codes:**
- `200`: Refreshed
- `400`: Invalid URL
- `401`: Token error
- `404`: Repository not found
- `429`: GitHub rate limited
- `500`: Server error

---

## Error Response Format

All error responses follow this format:

```json
{
  "error": "error_type",
  "error_message": "Human-readable error description",
  "status_code": 400
}
```

### Error Types (ADR-0453)

| Error Type | HTTP Status | Cause |
|---|---|---|
| `invalid_url` | 400 | URL format is not `https://github.com/owner/repo` |
| `duplicate_repo` | 400 | Repository URL already exists |
| `auth_failed` | 401 | GitHub token invalid, expired, or insufficient permissions |
| `repo_not_found` | 404 | Repository does not exist or is inaccessible |
| `rate_limited` | 429 | GitHub API rate limit exceeded |
| `network_error` | 503 | Temporary network connectivity issue |
| `server_error` | 500 | Internal server error |

---

## Authentication

All requests use the same authentication as the Console (session-based). No additional API keys required.

GitHub tokens are passed as `token_ref` and encrypted with AES-256-GCM before storage.

---

## Caching

Repository metadata is cached for **30 seconds**. If a request returns cached data, the response includes:

```json
{
  "repositories": [...],
  "cached": true,
  "cache_valid_until": "2026-08-30T10:00:30Z"
}
```

---

## Rate Limiting

Requests are subject to GitHub's API rate limits:
- **Anonymous**: 60 requests/hour
- **Authenticated**: 5,000 requests/hour

If rate limited, the response includes:

```json
{
  "error": "rate_limited",
  "error_message": "GitHub API rate limited. Retry after 3600 seconds",
  "retry_after": 3600
}
```

---

## Multi-Tenant Isolation

All requests are automatically scoped to the current tenant. `tenant_id` is extracted from the session context.

No cross-tenant access is possible.

---

## Examples

### Add a private repository with token

```bash
curl -X POST https://your-corvin/v1/console/api/v1/marketplace/custom-repositories \
  -H "Content-Type: application/json" \
  -H "Cookie: session=..." \
  -d '{
    "repo_url": "https://github.com/myorg/private-plugins",
    "token_ref": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  }'
```

### List repositories

```bash
curl https://your-corvin/v1/console/api/v1/marketplace/custom-repositories \
  -H "Cookie: session=..."
```

### Refresh a repository

```bash
curl -X POST https://your-corvin/v1/console/api/v1/marketplace/custom-repositories/refresh \
  -H "Content-Type: application/json" \
  -H "Cookie: session=..." \
  -d '{
    "repo_url": "https://github.com/owner/repo"
  }'
```

---

## Related Documentation

- [User Guide](./marketplace-custom-repos.md)
- [Security & Scope (ADR-0450)](../Corvin-ADR/decisions/ADR-0450-custom-github-repository-discovery-scope.md)
- [API Contract (ADR-0451)](../Corvin-ADR/decisions/ADR-0451-custom-github-repository-api-storage.md)
- [Error Taxonomy (ADR-0453)](../Corvin-ADR/decisions/ADR-0453-custom-repository-error-handling.md)
