// net_probe.js — local-network-outage detection for bridge daemons.
//
// Background (incident 2026-07-10): the host lost its uplink (hotspot drop,
// total DNS outage 23:07–23:50). Every resilience mechanism in the Discord
// daemon misread the local outage as a Discord-side failure:
//   - the stuck-reconnect detector exited for a systemd restart,
//   - loginWithBackoff climbed its conservative ladder to a 900 s wait and
//     stayed blind for 12 minutes AFTER the network was back,
//   - the zombie watchdog would have exited again 3 minutes later.
// The conservative ladder exists to protect the daily IDENTIFY budget after
// a real Discord/Cloudflare error — but connection-level failures never
// reach Discord at all, so they consume no budget and may be retried fast.
//
// This module gives daemons two primitives:
//   isNetworkError(msg)  — classify an error message as connection-level
//   networkUp(opts)      — cheap DNS probe: "does the local uplink resolve?"

const dns = require('dns');

// Connection-level error signatures (Node syscall codes + libc/getaddrinfo
// phrasings). Deliberately does NOT match HTTP-level failures (4xx/5xx,
// rate limits) — those reached the remote side and must keep conservative
// backoff handling.
// The undici tokens (UND_ERR_*, "connect timeout", "headers timeout",
// "operation was aborted") cover the blackhole outage variant: AP still
// associated, cellular uplink dead — DNS answers from cache but TCP
// blackholes, so @discordjs/rest surfaces undici timeout errors instead
// of getaddrinfo failures.
const NET_ERR_RE = /getaddrinfo|ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ECONNREFUSED|ECONNRESET|ENETUNREACH|EHOSTUNREACH|network is unreachable|name or service not known|socket hang up|UND_ERR_|connect timeout|headers timeout|operation was aborted/i;

function isNetworkError(msg) {
  return NET_ERR_RE.test(String(msg || ''));
}

/**
 * Resolve a well-known host to decide whether the local uplink works.
 * @param {object} [opts]
 * @param {string} [opts.host='discord.com']
 * @param {number} [opts.timeoutMs=3000]
 * @param {function} [opts.lookup=dns.promises.lookup] — injectable for tests
 * @returns {Promise<boolean>}
 */
async function networkUp({ host = 'discord.com', timeoutMs = 3000, lookup = dns.promises.lookup } = {}) {
  let timer;
  try {
    await Promise.race([
      lookup(host),
      new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('probe timeout')), timeoutMs); }),
    ]);
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { isNetworkError, networkUp, NET_ERR_RE };
