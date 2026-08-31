/**
 * AutoEmailOAuthFlow — Email OAuth2 (Microsoft Graph / Gmail)
 *
 * Category B pattern: Reuses Teams OAuth structure with email-specific scopes
 * Supports both Gmail (Google OAuth) and Exchange (Microsoft Graph)
 */

const https = require('https');
const { URL } = require('url');

const PROVIDERS = {
  microsoft: {
    authority: 'login.microsoftonline.com',
    tokenPath: '/common/oauth2/v2.0/token',
    graphHost: 'graph.microsoft.com',
  },
  google: {
    authority: 'oauth2.googleapis.com',
    tokenPath: '/token',
    graphHost: 'www.googleapis.com',
  },
};

const REQUIRED_SCOPES = {
  microsoft: [
    'https://graph.microsoft.com/.default',
    'Mail.Send',
    'Mail.Read',
    'User.Read',
  ],
  google: [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/userinfo.email',
  ],
};

class AutoEmailOAuthFlow {
  constructor(log, clientId, clientSecret, provider = 'microsoft', tenantId = 'common') {
    this.log = log;
    this.clientId = clientId;
    this.clientSecret = clientSecret;
    this.provider = provider;
    this.tenantId = tenantId;
    this.config = PROVIDERS[provider] || PROVIDERS.microsoft;
  }

  generateAuthorizationUrl(redirectUri = 'http://localhost:8765/bridge-setup/callback') {
    const scopes = REQUIRED_SCOPES[this.provider];

    if (this.provider === 'google') {
      const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
      url.searchParams.append('client_id', this.clientId);
      url.searchParams.append('scope', scopes.join(' '));
      url.searchParams.append('response_type', 'code');
      url.searchParams.append('redirect_uri', redirectUri);
      url.searchParams.append('access_type', 'offline');
      url.searchParams.append('prompt', 'consent');

      return {
        url: url.toString(),
        requiredScopes: scopes,
        provider: this.provider,
      };
    }

    const url = new URL(`https://${this.config.authority}/${this.tenantId}/oauth2/v2.0/authorize`);
    url.searchParams.append('client_id', this.clientId);
    url.searchParams.append('scope', scopes.join(' '));
    url.searchParams.append('response_type', 'code');
    url.searchParams.append('redirect_uri', redirectUri);

    return {
      url: url.toString(),
      requiredScopes: scopes,
      provider: this.provider,
    };
  }

  async exchangeCodeForToken(code, redirectUri = 'http://localhost:8765/bridge-setup/callback') {
    return new Promise((resolve) => {
      const postData = JSON.stringify({
        client_id: this.clientId,
        client_secret: this.clientSecret,
        code: code,
        redirect_uri: redirectUri,
        grant_type: 'authorization_code',
      });

      const options = {
        hostname: this.config.authority,
        port: 443,
        path: this.provider === 'google' ? this.config.tokenPath : `/${this.tenantId}${this.config.tokenPath}`,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
          'User-Agent': 'CorvinOS/Email-Bridge',
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
                error: json.error_description || json.error,
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
          error: 'OAuth timeout (>5s)',
        });
      });

      req.write(postData);
      req.end();
    });
  }

  async validateToken(accessToken) {
    return new Promise((resolve) => {
      const path = this.provider === 'google' ? '/oauth2/v2/userinfo' : '/v1.0/me';
      const options = {
        hostname: this.provider === 'google' ? 'www.googleapis.com' : this.config.graphHost,
        port: 443,
        path: path,
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'User-Agent': 'CorvinOS/Email-Bridge',
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
                error: json.error.message || json.error_description || 'Validation failed',
              });
              return;
            }

            const email = json.email || json.userPrincipalName;
            if (!email) {
              resolve({
                valid: false,
                error: 'No email in response',
              });
              return;
            }

            resolve({
              valid: true,
              email: email,
              user_id: json.id || json.sub,
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
          error: 'Validation timeout (>5s)',
        });
      });

      req.end();
    });
  }
}

module.exports = { AutoEmailOAuthFlow, REQUIRED_SCOPES };
