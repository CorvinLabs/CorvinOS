# Custom GitHub Repositories in Marketplace

## Overview

CorvinOS Marketplace allows you to add custom GitHub repositories to discover plugins beyond the default Corvin-Marketplace registry. This is useful for:

- **Private repositories**: Add plugins stored in private GitHub repos
- **Organization repositories**: Access organization-specific extensions
- **Custom forks**: Use custom forks of public plugins
- **Testing**: Discover plugins from test/staging repositories

## Adding a Repository

1. Open **Marketplace** → **Custom Repos** tab
2. Click **[Add Repository]**
3. Enter the repository URL: `https://github.com/owner/repository-name`
4. (Optional) Enter your GitHub Personal Access Token (for private repos)
5. Click **[Add Repository]**

The URL is validated in real-time. A green checkmark indicates a valid GitHub repository URL.

## Authentication

### Public Repositories
No token required. Simply enter the repository URL.

### Private Repositories
You must provide a GitHub Personal Access Token (PAT).

**To create a PAT:**
1. Go to GitHub → Settings → Developer settings → Personal access tokens
2. Click **Generate new token (classic)**
3. Select scope: `repo` (full control of private repositories)
4. Copy the token (starts with `ghp_`)
5. Paste into the "GitHub Token" field in Marketplace

**Security:**
- Tokens are encrypted with AES-256-GCM before storage
- Never logged or exposed in API responses
- Stored securely in `secrets.yaml` (never in config files)

## Managing Repositories

### View Repository Status
Each repository card shows:
- **Status**: Healthy ✓ | Loading ⟳ | Error ⚠
- **Extension count**: Number of plugins discovered
- **Last checked**: When metadata was last fetched
- **Enabled badge**: Disabled repositories are grayed out

### Actions

**[Refresh]** — Manually update repository metadata
- Fetches latest plugin metadata from GitHub
- Updates extension count and error status
- Non-blocking; continues showing cached data if fetch fails

**[Disable]** — Temporarily disable a repository
- Plugins from disabled repos won't appear in browse
- Can be re-enabled later
- No data is deleted

**[Remove]** — Delete the repository configuration
- Permanently removes the repository
- Plugins from this repo won't be available
- Cannot be undone (must re-add to use again)

## Troubleshooting

### "Invalid URL. Expected: https://github.com/owner/repo"
- URL must be a valid GitHub repository URL
- Use format: `https://github.com/owner/repository-name`
- Trailing slash is optional

### "Repository not found" or "404"
- Repository URL is incorrect or repository was deleted
- If private, check that you provided a valid GitHub token
- Verify the repository exists: visit the URL in a browser

### "Invalid token" or "401 Unauthorized"
- GitHub token is invalid, expired, or has insufficient permissions
- Generate a new token with `repo` scope
- Ensure token starts with `ghp_`

### "GitHub API rate limited"
- GitHub API rate limit exceeded (60 requests/hour anonymous, 5000/hour authenticated)
- Wait 1 hour, then click **[Refresh]** to retry
- Use a GitHub token to increase rate limit

### "Connection timeout"
- Network connectivity issue
- Check your internet connection
- Click **[Refresh]** to retry

## Best Practices

1. **Use GitHub tokens for private repos**: Public repos work without auth but may hit rate limits
2. **Disable unused repos**: Keep only needed repos enabled to reduce load
3. **Monitor repo status**: Check for error indicators (⚠) regularly
4. **Refresh occasionally**: Click **[Refresh]** monthly to update metadata
5. **Document your repos**: Use descriptive organization names in GitHub to make repos easy to find

## Limits & Constraints

- **One repository per URL**: Duplicates are rejected
- **Flat discovery**: Nested/recursive repository resolution not supported
- **Cache TTL**: Repository data is cached for 30 seconds
- **Rate limiting**: Subject to GitHub API rate limits
- **Graceful degradation**: If repo fetch fails, cached data is shown (if available)

## API Reference

See `docs/marketplace-api-reference.md` for endpoint documentation.

## Related Docs

- [ADR-0450](../Corvin-ADR/decisions/ADR-0450-custom-github-repository-discovery-scope.md): Security & Scope Boundary
- [ADR-0451](../Corvin-ADR/decisions/ADR-0451-custom-github-repository-api-storage.md): API Contract & Storage
- [ADR-0452](../Corvin-ADR/decisions/ADR-0452-custom-repository-github-authentication.md): Token Encryption & Lifecycle
- [ADR-0453](../Corvin-ADR/decisions/ADR-0453-custom-repository-error-handling.md): Error Handling & Taxonomy
- [ADR-0454](../Corvin-ADR/decisions/ADR-0454-custom-github-repository-implementation.md): Implementation (Weeks 1-4)
