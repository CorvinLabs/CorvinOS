// health-server.js — uniformer HTTP /status-Server for die Bridge-daemons.
//
// Vorher: 4× ~15 Zeilen identischer http.createServer-Boilerplate. Hier
// zentralisiert; daemons passed nur kind + getStatus()-Closure.
//
// Wichtig: hat einen 'error'-Handler, so that ein Port-Konflikt nicht den
// ganzen daemon mit unhandled exception killt. Former Variante warf bei
// EADDRINUSE direkt.

const http = require('http');

/**
 * @param {object} cfg
 * @param {number}   cfg.port
 * @param {string}   cfg.kind        — 'whatsapp' | 'telegram' | 'discord' | 'slack'
 * @param {function} cfg.getStatus   — sync () => Object, gemerged in die Response
 * @param {function} [cfg.logger]
 * @returns {http.Server}
 */
function startHealthServer({ port, kind, getStatus, logger }) {
  const server = http.createServer((req, res) => {
    if (req.url === '/status') {
      res.setHeader('Content-Type', 'application/json');
      try {
        res.end(JSON.stringify({ kind, ...getStatus() }));
      } catch (e) {
        res.statusCode = 500;
        res.end(JSON.stringify({ kind, error: e.message }));
      }
      return;
    }
    res.statusCode = 404;
    res.end('not found');
  });
  server.on('error', (e) => {
    if (logger) logger(`health server error: ${e.message}`);
  });
  server.listen(port, '127.0.0.1', () => {
    if (logger) logger(`HTTP API on http://127.0.0.1:${port}`);
  });
  return server;
}

module.exports = { startHealthServer };
