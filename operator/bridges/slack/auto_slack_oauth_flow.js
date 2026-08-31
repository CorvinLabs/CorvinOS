/**
 * AutoSlackOAuthFlow — Slack OAuth2 + Permission Validation
 *
 * Category B pattern (OAuth bridges): Token exchange + scope validation
 *
 * Flow:
 * 1. Console generates OAuth URL (client_id + scopes)
 * 2. User authorizes in browser (Slack OAuth)
 * 3. Browser redirects to Console callback (localhost:8765/callback)
 * 4. Console exchanges code for tokens (access_token, refresh_token optional)
 * 5. Validate scopes via Slack API (auth.test)
 * 6. Save tokens to settings.json
 */

const https = require('https');
const { URL } = require('url');

const SLACK_API_BASE = 'slack.com';

// Required scopes for bot to function
const REQUIRED_SCOPES = [
  'chat:write',
  'files:read',
  'files:write',
  'reactions:write',
  'channels:history',
  'groups:history',
  'im:history',
  'mpim:history',
  'im:read',
  'im:write',
];

class AutoSlackOAuthFlow {
  constructor(log, clientId, clientSecret) {
    this.log = log;
    this.clientId = clientId;
    this.clientSecret = clientSecret;
  }

  /**
   * Generate OAuth authorization URL
   * User opens this in browser to grant permissions
   */
  generateAuthorizationUrl(redirectUri = 'http://localhost:8765/bridge-setup/callback') {
    const url = new URL('https://slack.com/oauth/v2/authorize');
    url.searchParams.append('client_id', this.clientId);
    url.searchParams.append('scope', REQUIRED_SCOPES.join(' '));
    url.searchParams.append('redirect_uri', redirectUri);
    url.searchParams.append('user_scope', '');  // For user auth, if needed

    return {
      url: url.toString(),
      requiredScopes: REQUIRED_SCOPES,
    };
  }

  /**
   * Exchange OAuth code for access token
   * Called after user authorizes in browser
   *
   * Returns: { access_token: string, team_id: string, team_name: string }
   */
  async exchangeCodeForToken(code) {
    return new Promise((resolve) => {
      const postData = JSON.stringify({
        client_id: this.clientId,
        client_secret: this.clientSecret,
        code: code,
        redirect_uri: 'http://localhost:8765/bridge-setup/callback',
      });

      const options = {
        hostname: SLACK_API_BASE,
        port: 443,
        path: '/api/v2/oauth.v2.access',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
          'User-Agent': 'CorvinOS/Slack-Bridge',
        },
        timeout: 5000,
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);

            if (!json.ok) {
              resolve({
                valid: false,
                error: json.error || 'OAuth exchange failed',
              });
              return;
            }

            if (!json.team || !json.team.id || !json.team.name) {
              resolve({
                valid: false,
                error: 'Invalid response: missing team info',
              });
              return;
            }

            resolve({
              valid: true,
              access_token: json.access_token,
              team_id: json.team.id,
              team_name: json.team.name,
              bot_user_id: json.bot_user_id,
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
          error: 'Slack API timeout (>5s)',
        });
      });

      req.write(postData);
      req.end();
    });
  }

  /**
   * Validate that bot has all required scopes
   * Calls auth.test to check actual permissions
   */
  async validateScopes(accessToken) {
    return new Promise((resolve) => {
      const options = {
        hostname: SLACK_API_BASE,
        port: 443,
        path: '/api/v2/auth.test',
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'User-Agent': 'CorvinOS/Slack-Bridge',
        },
        timeout: 5000,
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);

            if (!json.ok) {
              resolve({
                valid: false,
                error: json.error || 'Auth test failed',
              });
              return;
            }

            // Check scopes (Slack returns in auth.test response)
            if (!Array.isArray(json.scopes)) {
              resolve({
                valid: false,
                error: 'Invalid response: missing scopes list',
              });
              return;
            }

            const grantedScopes = json.scopes;
            const missing = REQUIRED_SCOPES.filter(s => !grantedScopes.includes(s));

            if (missing.length > 0) {
              resolve({
                valid: false,
                error: `Missing scopes: ${missing.join(', ')}`,
                missing_scopes: missing,
              });
              return;
            }

            resolve({
              valid: true,
              bot_id: json.user_id,
              team_id: json.team_id,
              scopes: grantedScopes,
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
          error: 'Slack API timeout (>5s)',
        });
      });

      req.end();
    });
  }
}

module.exports = { AutoSlackOAuthFlow, REQUIRED_SCOPES };
