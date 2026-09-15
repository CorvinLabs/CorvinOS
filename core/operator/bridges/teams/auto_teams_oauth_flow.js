/**
 * AutoTeamsOAuthFlow — Microsoft Teams OAuth2 + Permission Validation
 *
 * Category B pattern (OAuth bridges): Token exchange + scope validation
 * Azure AD variant: Uses Microsoft identity platform instead of Slack
 *
 * Flow:
 * 1. Console generates OAuth URL (client_id + scopes, Azure AD endpoint)
 * 2. User authorizes in browser (Azure AD OAuth)
 * 3. Browser redirects to Console callback (localhost:8765/callback)
 * 4. Console exchanges code for tokens (access_token, refresh_token optional)
 * 5. Validate scopes via Microsoft Graph API (me endpoint)
 * 6. Save tokens to settings.json
 */

const https = require('https');
const { URL } = require('url');

const AZURE_AUTHORITY = 'login.microsoftonline.com';
const GRAPH_API_BASE = 'graph.microsoft.com';

// Required scopes for bot to function
// https://learn.microsoft.com/en-us/graph/permissions-reference
const REQUIRED_SCOPES = [
  'https://graph.microsoft.com/.default',  // Full access (AAD app-level permission)
  // Individual scopes for more granular control:
  // 'Chat.Create',
  // 'ChannelMessage.Send',
  // 'Team.ReadWrite.All',
  // 'User.Read',
];

class AutoTeamsOAuthFlow {
  constructor(log, clientId, clientSecret, tenantId = 'common') {
    this.log = log;
    this.clientId = clientId;
    this.clientSecret = clientSecret;
    this.tenantId = tenantId;
  }

  /**
   * Generate OAuth authorization URL
   * Uses Azure AD v2.0 endpoint
   * User opens this in browser to grant permissions
   */
  generateAuthorizationUrl(redirectUri = 'http://localhost:8765/bridge-setup/callback') {
    const url = new URL(`https://${AZURE_AUTHORITY}/${this.tenantId}/oauth2/v2.0/authorize`);
    url.searchParams.append('client_id', this.clientId);
    url.searchParams.append('scope', REQUIRED_SCOPES.join(' '));
    url.searchParams.append('response_type', 'code');
    url.searchParams.append('redirect_uri', redirectUri);
    url.searchParams.append('response_mode', 'query');

    return {
      url: url.toString(),
      requiredScopes: REQUIRED_SCOPES,
    };
  }

  /**
   * Exchange OAuth code for access token
   * Called after user authorizes in browser
   *
   * Returns: { access_token: string, refresh_token?: string, user_id: string, user_email: string }
   */
  async exchangeCodeForToken(code) {
    return new Promise((resolve) => {
      const postData = JSON.stringify({
        client_id: this.clientId,
        client_secret: this.clientSecret,
        code: code,
        redirect_uri: 'http://localhost:8765/bridge-setup/callback',
        grant_type: 'authorization_code',
        scope: REQUIRED_SCOPES.join(' '),
      });

      const options = {
        hostname: AZURE_AUTHORITY,
        port: 443,
        path: `/${this.tenantId}/oauth2/v2.0/token`,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
          'User-Agent': 'CorvinOS/Teams-Bridge',
        },
        timeout: 5000,
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);

            if (json.error) {
              resolve({
                valid: false,
                error: json.error_description || json.error || 'OAuth exchange failed',
              });
              return;
            }

            if (!json.access_token) {
              resolve({
                valid: false,
                error: 'No access_token in response',
              });
              return;
            }

            resolve({
              valid: true,
              access_token: json.access_token,
              refresh_token: json.refresh_token || null,
              expires_in: json.expires_in,
            });
          } catch (e) {
            resolve({
              valid: false,
              error: `Failed to parse response: ${e.message}`,
            });
          }
        });
      });

      req.on('error', (err) => {
        resolve({
          valid: false,
          error: `Network error: ${err.message}`,
        });
      });

      req.on('timeout', () => {
        req.destroy();
        resolve({
          valid: false,
          error: 'Azure AD timeout (>5s)',
        });
      });

      req.write(postData);
      req.end();
    });
  }

  /**
   * Validate that bot has required permissions
   * Calls Microsoft Graph /me endpoint to verify token is valid
   */
  async validateToken(accessToken) {
    return new Promise((resolve) => {
      const options = {
        hostname: GRAPH_API_BASE,
        port: 443,
        path: '/v1.0/me',
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'User-Agent': 'CorvinOS/Teams-Bridge',
        },
        timeout: 5000,
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);

            if (json.error) {
              resolve({
                valid: false,
                error: json.error.message || 'Token validation failed',
              });
              return;
            }

            if (!json.id || !json.userPrincipalName) {
              resolve({
                valid: false,
                error: 'Invalid response: missing user info',
              });
              return;
            }

            resolve({
              valid: true,
              user_id: json.id,
              user_email: json.userPrincipalName,
              user_name: json.displayName || 'User',
            });
          } catch (e) {
            resolve({
              valid: false,
              error: `Failed to parse response: ${e.message}`,
            });
          }
        });
      });

      req.on('error', (err) => {
        resolve({
          valid: false,
          error: `Network error: ${err.message}`,
        });
      });

      req.on('timeout', () => {
        req.destroy();
        resolve({
          valid: false,
          error: 'Microsoft Graph timeout (>5s)',
        });
      });

      req.end();
    });
  }
}

module.exports = { AutoTeamsOAuthFlow, REQUIRED_SCOPES };
