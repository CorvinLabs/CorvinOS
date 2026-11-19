// http_app.js — the Teams bridge's Express app (webhook endpoint), kept apart
// from daemon.js so the HTTP boundary can be driven by a test without booting
// the daemon (which needs real Azure credentials).
//
// CloudAdapter.process() reads the activity from `req.body` (a parsed object)
// and answers 400 without it. The former hand-rolled middleware in daemon.js
// only set `req.rawBody`, so every activity was refused — and it buffered an
// unbounded body from any client before the JWT check (review 2026-11-19,
// R3-NEW-7). express.json() parses with a hard size limit; the raw bytes stay
// available on req.rawBody.

'use strict';

const express = require('express');

const MAX_BODY_BYTES = 1024 * 1024;  // Bot Framework activities are small JSON
const REQUEST_TIMEOUT_MS = 30 * 1000;
const HEADERS_TIMEOUT_MS = 15 * 1000;

/**
 * @param {object}   cfg
 * @param {object}   cfg.adapter  — botbuilder CloudAdapter (or anything with
 *                                  process(req, res, logic))
 * @param {function} cfg.onTurn   — async (turnContext) => void
 * @param {number}   [cfg.maxBodyBytes]
 */
function createApp({ adapter, onTurn, maxBodyBytes }) {
  const app = express();
  app.disable('x-powered-by');

  app.use(express.json({
    limit: maxBodyBytes || MAX_BODY_BYTES,
    type: () => true,  // Bot Framework posts JSON; do not depend on the header spelling
    verify: (req, _res, buf) => { req.rawBody = buf; },
  }));

  app.post('/api/messages', async (req, res) => {
    await adapter.process(req, res, onTurn);
  });

  // Oversized / malformed bodies and handler errors: answer and stop. Never
  // echo the error (it can quote the request body).
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    if (res.headersSent) return res.end();
    const status = err && err.type === 'entity.too.large' ? 413
      : (err && err.status >= 400 && err.status < 500 ? 400 : 500);
    res.status(status).end();
  });

  return app;
}

// Bound slow / stalled clients (the listener is on 0.0.0.0 behind a proxy).
function applyServerTimeouts(server) {
  server.requestTimeout = REQUEST_TIMEOUT_MS;
  server.headersTimeout = HEADERS_TIMEOUT_MS;
  return server;
}

module.exports = { createApp, applyServerTimeouts, MAX_BODY_BYTES, REQUEST_TIMEOUT_MS, HEADERS_TIMEOUT_MS };
