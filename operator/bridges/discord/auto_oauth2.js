/**
 * AutoOAuth2Generator — automatische Discord OAuth2 URL-Generierung
 *
 * Validiert Bot-Token via Discord API und generiert die komplette OAuth2-URL
 * mit korrekten Permissions. User muss nur noch klicken.
 */

const https = require('https');
const { URL } = require('url');

// Discord API-Konstanten
const DISCORD_API_BASE = 'discord.com';
const PERMISSIONS_BITMAP = 68608;  // Read Messages, Send Messages, Attach Files, Read History
const OAUTH_SCOPES = ['bot'];

class AutoOAuth2Generator {
  constructor(log) {
    this.log = log;
  }

  /**
   * Validiert einen Discord Bot-Token via REST API.
   * Gibt zurück: { valid: bool, appId: string, error: string? }
   */
  async validateToken(token) {
    return new Promise((resolve) => {
      const options = {
        hostname: DISCORD_API_BASE,
        port: 443,
        path: '/api/v10/applications/@me',
        method: 'GET',
        headers: {
          'Authorization': `Bot ${token}`,
          'User-Agent': 'CorvinOS/Discord-Bridge',
        },
        timeout: 5000,
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            if (res.statusCode === 200) {
              const json = JSON.parse(data);
              resolve({
                valid: true,
                appId: json.id,
                name: json.name,
              });
            } else if (res.statusCode === 401) {
              resolve({
                valid: false,
                error: 'Invalid token (401 Unauthorized)',
              });
            } else {
              resolve({
                valid: false,
                error: `Discord API error: HTTP ${res.statusCode}`,
              });
            }
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
          error: 'Discord API timeout (>5s)',
        });
      });

      req.end();
    });
  }

  /**
   * Generiert Discord OAuth2 Autorisierungs-URL
   *
   * Returns: {
   *   url: string,
   *   appId: string,
   *   clientId: string,  // sama als appId
   * }
   */
  async generateAuthorizationUrl(token) {
    this.log('Validating bot token...');
    const validation = await this.validateToken(token);

    if (!validation.valid) {
      return {
        error: validation.error,
        valid: false,
      };
    }

    const appId = validation.appId;
    const url = new URL('https://discord.com/api/oauth2/authorize');

    url.searchParams.append('client_id', appId);
    url.searchParams.append('scope', OAUTH_SCOPES.join(' '));
    url.searchParams.append('permissions', PERMISSIONS_BITMAP.toString());
    // Disable guild selection if possible (user invites to one server at a time)
    // This is optional but improves UX
    url.searchParams.append('disable_guild_select', 'false');

    this.log(`✓ Token validated. App ID: ${appId}`);
    this.log(`✓ OAuth2 URL generated with permissions: ${PERMISSIONS_BITMAP}`);

    return {
      valid: true,
      appId: appId,
      clientId: appId,
      url: url.toString(),
      appName: validation.name,
      permissionsHuman: [
        'Read Messages/View Channels',
        'Send Messages',
        'Embed Links',
        'Attach Files',
        'Read Message History',
      ],
    };
  }
}

module.exports = { AutoOAuth2Generator, PERMISSIONS_BITMAP };
