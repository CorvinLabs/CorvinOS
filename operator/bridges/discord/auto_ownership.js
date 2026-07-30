/**
 * AutoOwnershipBridge — automatische Owner-Zuweisung
 *
 * Wenn whitelist leer ist (Standard), wird der erste Sender zum Owner.
 * Wenn whitelist gesetzt ist (Production), wird normal gecheckt.
 *
 * Verhindert Owner-Trap: nur die erste Nachricht zählt.
 */

class AutoOwnershipBridge {
  constructor(log, settings) {
    this.log = log;
    this.settings = settings;
    this._firstMessageProcessed = false;
  }

  /**
   * Bestimmt ob ein User authorized ist.
   * Returns: { authorized: bool, role: 'owner' | 'user' | 'guest', reason: string }
   */
  determineAccess(userId, addressed = true) {
    const whitelistEmpty = !this.settings.whitelist || this.settings.whitelist.length === 0;
    const autoOwnerEnabled = this.settings.auto_owner !== false;

    // Auto-Owner Mode (Development/First-Setup)
    if (whitelistEmpty && autoOwnerEnabled) {
      if (!this._firstMessageProcessed) {
        // Only a message ACTUALLY ADDRESSED to the bot (DM or @mention) may
        // claim ownership. With the MessageContent intent active the bot sees
        // every guild message, so the old unconditional promotion handed
        // ownership to the first random chatter — who may never have meant to
        // talk to the bot — and locked the real operator out to guest with no
        // recovery but hand-editing settings.json (2026-07-30 finding B1). A
        // non-addressed message must NOT consume the one-shot promotion.
        if (!addressed) {
          return {
            authorized: false,
            role: 'guest',
            reason: 'auto_owner_awaiting_addressed_message',
            userId: userId,
          };
        }
        this._firstMessageProcessed = true;
        if (this.log) this.log(`✓ First message from ${userId} — promoting to Owner (auto_owner mode)`);

        // Update settings so whitelist is locked
        if (!this.settings.whitelist) this.settings.whitelist = [];
        this.settings.whitelist.push(userId);
        this.settings.owner_id = userId;

        return {
          authorized: true,
          role: 'owner',
          reason: 'auto_owner_first_message',
          promoted: true,
        };
      } else {
        // Auto-Owner wurde bereits gesetzt, später kommende User sind blocked
        return {
          authorized: false,
          role: 'guest',
          reason: 'auto_owner_already_used',
          userId: userId,
        };
      }
    }

    // Whitelist Mode (Production)
    if (this.settings.whitelist && this.settings.whitelist.includes(userId)) {
      return {
        authorized: true,
        role: 'owner',
        reason: 'whitelisted',
      };
    }

    // Guest: nicht autorisiert
    return {
      authorized: false,
      role: 'guest',
      reason: 'not_whitelisted',
      userId: userId,
    };
  }

  /**
   * Reset die Auto-Owner Flag (für Tests/Reconfiguration)
   */
  resetFirstMessage() {
    this._firstMessageProcessed = false;
    if (this.log) this.log('⚠️  Auto-Owner flag reset (development only)');
  }
}

module.exports = { AutoOwnershipBridge };
