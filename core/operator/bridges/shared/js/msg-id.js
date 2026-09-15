// msg-id.js — kollisionsarme inbox message-Id (Datum + 6 hex chars).
// Gleiche Form wie die alten Inline-Versionen, damit existierende
// processed/-Files weiterhin lesbar sind und nichts an adapter.py
// Inbox-Erkennung bricht.

const crypto = require('crypto');

function newMsgId() {
  return Date.now().toString(36) + '_' + crypto.randomBytes(3).toString('hex');
}

module.exports = { newMsgId };
