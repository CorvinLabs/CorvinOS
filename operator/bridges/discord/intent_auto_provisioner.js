/**
 * IntentAutoProvisioner — Auto-Detection & Optional Auto-Request of Message Content Intent
 *
 * Phase 3 of Discord Zero-Config Setup.
 *
 * Discord has a "Message Content Intent" gate: without it, bot only sees:
 * - DMs (full message)
 * - Messages where bot is @mentioned
 *
 * This provisioner:
 * 1. Detects when bot joins a guild
 * 2. Logs intent status (enabled/disabled in Developer Portal)
 * 3. Optionally auto-requests intent (if Portal setting allows)
 * 4. Graceful fallback: bot works without intent (just limited scope)
 *
 * ADR-0043 (voice integration) + Compli-Gate apply: never fail closed on Intent absence.
 */

class IntentAutoProvisioner {
  constructor(log, discordClient, settings) {
    this.log = log;
    this.client = discordClient;
    this.settings = settings || {};
    this._intentStatus = null; // Cached: { enabled: bool, guildId: string, timestamp: number }
    this.INTENT_STATUS_TTL = 3600000; // 1h cache TTL
  }

  /**
   * Called when bot joins a guild.
   * Checks if Message Content Intent is available and logs status.
   *
   * Returns: { intentAvailable: bool, reason: string }
   */
  async onGuildJoin(guild) {
    this.log(`[IntentAutoProvisioner] Guild join: ${guild.name} (ID: ${guild.id})`);

    try {
      const status = await this._checkIntentStatus(guild);

      if (status.intentAvailable) {
        this.log(`✓ Message Content Intent available for ${guild.name}`);
        this._intentStatus = {
          enabled: true,
          guildId: guild.id,
          timestamp: Date.now(),
        };
        return {
          intentAvailable: true,
          reason: 'intent_available',
        };
      } else {
        this.log(`⚠️  Message Content Intent NOT available for ${guild.name}`);
        this.log(`   Bot can still respond to: DMs + @mentions (no guild-message scanning)`);

        // Optional: Auto-request intent if enabled in settings
        if (this.settings.auto_message_content_intent) {
          await this._requestIntent(guild);
        }

        this._intentStatus = {
          enabled: false,
          guildId: guild.id,
          timestamp: Date.now(),
        };
        return {
          intentAvailable: false,
          reason: 'intent_not_available__fallback_to_dms_and_mentions',
        };
      }
    } catch (err) {
      this.log(`❌ Error checking intent status: ${err.message}`);
      // Fail-open: assume intent is available (graceful degradation)
      return {
        intentAvailable: true,
        reason: 'error_checking_intent__assume_available',
      };
    }
  }

  /**
   * Check if Message Content Intent is enabled.
   * Uses bot's own gateway intents + guild capabilities.
   */
  async _checkIntentStatus(guild) {
    // Check bot's Intents bitmask
    const INTENTS = {
      MESSAGE_CONTENT: 0x8000, // 32768
    };

    const botIntents = this.client?.ws?.status?.intents || 0;
    const hasIntent = (botIntents & INTENTS.MESSAGE_CONTENT) !== 0;

    return {
      intentAvailable: hasIntent,
      guildId: guild.id,
      botIntents: botIntents.toString(2), // Binary string for logging
    };
  }

  /**
   * Optional: Request Message Content Intent via Discord API.
   * Requires that intent is enabled in Developer Portal (can't enable via API).
   */
  async _requestIntent(guild) {
    this.log(`   [IntentAutoProvisioner] Attempting to request Intent...`);
    this.log(`   NOTE: Intent must be enabled in Discord Developer Portal first`);
    this.log(`   → https://discord.com/developers/applications/${this.settings.app_id}/bot`);

    // In production, this would send REST request to Discord API:
    // POST /applications/{app.id}/guilds/{guild.id}/commands
    // But intent itself is not requestable via API — it's a one-time Portal setting.
    //
    // We log the link so user can manually enable it if needed.

    return {
      success: false,
      reason: 'intent_must_be_enabled_in_portal',
      link: `https://discord.com/developers/applications/${this.settings.app_id}/bot`,
    };
  }

  /**
   * Get cached intent status.
   */
  getIntentStatus() {
    if (!this._intentStatus || Date.now() - this._intentStatus.timestamp > this.INTENT_STATUS_TTL) {
      return null;
    }
    return this._intentStatus;
  }

  /**
   * Reset intent status cache (for reconfiguration).
   */
  resetIntentStatus() {
    this._intentStatus = null;
    this.log('IntentAutoProvisioner: Cached intent status cleared');
  }
}

module.exports = { IntentAutoProvisioner };
