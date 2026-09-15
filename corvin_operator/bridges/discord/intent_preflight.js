// intent_preflight.js — decide BEFORE the gateway IDENTIFY whether the bot
// may request the privileged MessageContent intent.
//
// Why: a brand-new Discord application has the "MESSAGE CONTENT INTENT"
// toggle OFF in the Developer Portal. Requesting the intent anyway makes the
// gateway reject the login (close 4014, "used disallowed intents"), which the
// daemon used to treat as a terminal token failure — a fresh install with a
// perfectly valid token died on boot. The portal state is readable via REST:
// GET /applications/@me returns the application `flags`, and the two
// GATEWAY_MESSAGE_CONTENT bits mirror the toggle. With the answer in hand the
// daemon requests the intent only when it will actually be granted, so
// token-only onboarding works with zero portal changes (DMs + @mentions carry
// content without the privileged intent).

'use strict';

// Application flag bits (Discord API docs, "Application Flags"):
//   GATEWAY_MESSAGE_CONTENT          — toggle ON, app verified
//   GATEWAY_MESSAGE_CONTENT_LIMITED  — toggle ON, app unverified (<100 guilds)
const FLAG_GATEWAY_MESSAGE_CONTENT = 1 << 18;
const FLAG_GATEWAY_MESSAGE_CONTENT_LIMITED = 1 << 19;

const APP_ME_URL = 'https://discord.com/api/v10/applications/@me';

// Returns:
//   true  — portal toggle is ON, requesting MessageContent is safe
//   false — portal toggle is OFF, requesting it WILL kill the login
//   null  — unknown (network error, non-2xx, malformed body); caller decides
// Never throws. `fetchImpl` is injectable for tests.
async function messageContentAvailable(token, fetchImpl, timeoutMs = 8000) {
  const f = fetchImpl || globalThis.fetch;
  if (typeof f !== 'function' || !token) return null;
  try {
    const opts = { headers: { Authorization: `Bot ${token}` } };
    if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
      opts.signal = AbortSignal.timeout(timeoutMs);
    }
    const resp = await f(APP_ME_URL, opts);
    if (!resp || !resp.ok) return null;
    const app = await resp.json();
    const flags = Number(app && app.flags);
    if (!Number.isFinite(flags)) return null;
    return (flags & (FLAG_GATEWAY_MESSAGE_CONTENT | FLAG_GATEWAY_MESSAGE_CONTENT_LIMITED)) !== 0;
  } catch {
    return null;
  }
}

module.exports = {
  messageContentAvailable,
  FLAG_GATEWAY_MESSAGE_CONTENT,
  FLAG_GATEWAY_MESSAGE_CONTENT_LIMITED,
  APP_ME_URL,
};
