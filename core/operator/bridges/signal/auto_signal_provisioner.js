/**
 * AutoSignalProvisioner — Signal Linked Device Setup
 *
 * Category C pattern (Stateful): QR code + device linking
 * Uses Signal's linked device protocol (no OAuth, device-based auth)
 */

const https = require('https');

class AutoSignalProvisioner {
  constructor(log) {
    this.log = log;
  }

  /**
   * Generate a unique device ID for this Signal bot instance
   * In production, this would communicate with Signal's provisioning API
   * For MVP: generate a random device link and return QR format
   */
  async generateDeviceLinkQR() {
    return new Promise((resolve) => {
      try {
        const deviceId = this._generateUUID();
        const linkUri = `sgnl://v23/?uuid=${encodeURIComponent(deviceId)}&pub_key=mock_pub_key`;

        resolve({
          valid: true,
          device_link_uri: linkUri,
          device_id: deviceId,
          qr_data: this._encodeQR(linkUri),
        });
      } catch (e) {
        resolve({
          valid: false,
          error: `Failed to generate QR: ${e.message}`,
        });
      }
    });
  }

  /**
   * Validate that device linking succeeded
   * Checks if the linked account has authenticated
   */
  async validateDeviceLink(deviceId) {
    return new Promise((resolve) => {
      // In production: poll Signal's provisioning service
      // For MVP: accept any device ID as "linked"
      resolve({
        valid: true,
        device_id: deviceId,
        linked_at: new Date().toISOString(),
      });
    });
  }

  /**
   * Generate a simple UUID v4
   */
  _generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  /**
   * Encode QR data (simplified)
   * In production: use qrcode library to generate actual QR image
   */
  _encodeQR(linkUri) {
    // Return base64-encoded placeholder
    return `qr:${Buffer.from(linkUri).toString('base64')}`;
  }
}

module.exports = { AutoSignalProvisioner };
