/**
 * AutoTelegramTokenProvisioner — Validate Telegram bot token
 *
 * Mirror of AutoOAuth2Generator (Discord). Same pattern:
 * 1. Validate token via Telegram API
 * 2. Extract bot info (username, first_name)
 * 3. Return structured response
 */

const https = require('https');
const { URL } = require('url');

const TELEGRAM_API_BASE = 'api.telegram.org';

class AutoTelegramTokenProvisioner {
  constructor(log) {
    this.log = log;
  }

  /**
   * Validate a Telegram bot token via REST API.
   * Calls getMe endpoint to verify token + extract bot info.
   *
   * Returns: {
   *   valid: bool,
   *   botId?: string,
   *   botUsername?: string,
   *   botName?: string,
   *   error?: string
   * }
   */
  async validateToken(token) {
    return new Promise((resolve) => {
      const path = `/bot${token}/getMe`;
      const options = {
        hostname: TELEGRAM_API_BASE,
        port: 443,
        path: path,
        method: 'GET',
        headers: {
          'User-Agent': 'CorvinOS/Telegram-Bridge',
        },
        timeout: 5000,
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);

            // Telegram API returns { ok: true/false, result: {...} or description: "..." }
            if (!json.ok) {
              resolve({
                valid: false,
                error: json.description || 'Invalid token',
              });
              return;
            }

            const result = json.result;
            resolve({
              valid: true,
              botId: result.id.toString(),
              botUsername: result.username || 'unknown',
              botName: result.first_name || 'Bot',
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
          error: 'Telegram API timeout (>5s)',
        });
      });

      req.end();
    });
  }

  /**
   * Main entry point: validate token and return provisioning result
   *
   * Returns: {
   *   valid: bool,
   *   botId?: string,
   *   botUsername?: string,
   *   botName?: string,
   *   error?: string
   * }
   */
  async validateAndProvision(token) {
    this.log('Validating Telegram bot token...');
    const validation = await this.validateToken(token);

    if (!validation.valid) {
      return {
        error: validation.error,
        valid: false,
      };
    }

    this.log(`✓ Token validated. Bot: @${validation.botUsername} (ID: ${validation.botId})`);

    return {
      valid: true,
      botId: validation.botId,
      botUsername: validation.botUsername,
      botName: validation.botName,
    };
  }
}

module.exports = { AutoTelegramTokenProvisioner };
