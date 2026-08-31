// cards.js — Adaptive Card builders for the Teams bridge.
//
// All exports are pure functions: (text | data) → Teams message activity.
// No external dependencies — fully testable standalone.
//
// Card schema: Adaptive Cards 1.5 (supported by all modern Teams clients).
// Format: Teams `message` activity with a single adaptive-card attachment.

'use strict';

const SCHEMA = 'http://adaptivecards.io/schemas/adaptive-card.json';
const VERSION = '1.5';

// ── Helpers ──────────────────────────────────────────────────────────────────

function wrapCard(body, actions = []) {
  const content = {
    $schema: SCHEMA,
    type: 'AdaptiveCard',
    version: VERSION,
    body,
  };
  if (actions.length > 0) content.actions = actions;
  return {
    type: 'message',
    attachments: [{
      contentType: 'application/vnd.microsoft.card.adaptive',
      content,
    }],
  };
}

function textBlock(text, opts = {}) {
  return {
    type: 'TextBlock',
    text: String(text),
    wrap: true,
    ...opts,
  };
}

// Split a markdown string into {headers: [{title, body}]} sections.
// A section starts at each '## ' line.
function parseSections(text) {
  const lines = text.split('\n');
  const sections = [];
  let current = null;
  for (const line of lines) {
    if (line.startsWith('## ')) {
      if (current) sections.push(current);
      current = { title: line.slice(3).trim(), lines: [] };
    } else if (current) {
      current.lines.push(line);
    } else {
      // preamble before first heading
      if (!sections._pre) sections._pre = [];
      sections._pre = (sections._pre || []).concat(line);
    }
  }
  if (current) sections.push(current);
  return { sections, pre: (sections._pre || []).join('\n').trim() };
}

// Extract the first fenced code block: returns { code, lang, rest }
function extractCode(text) {
  const m = text.match(/```(\w*)\n?([\s\S]*?)```/);
  if (!m) return { code: text, lang: '', rest: '' };
  const pre = text.slice(0, m.index).trim();
  const post = text.slice(m.index + m[0].length).trim();
  return {
    lang: m[1] || '',
    code: m[2],
    rest: [pre, post].filter(Boolean).join('\n\n'),
  };
}

// ── Card builders ─────────────────────────────────────────────────────────────

/**
 * PlainCard — default for short, unstructured replies.
 * Renders as a simple wrapped TextBlock.
 */
function plainCard(text) {
  return wrapCard([textBlock(text)]);
}

/**
 * CodeCard — for replies that contain a fenced code block.
 * Preamble/postscript rendered as normal text above/below.
 */
function codeCard(text) {
  const { lang, code, rest } = extractCode(text);
  const body = [];
  if (rest) {
    // preamble that comes before the code block
    const preRest = text.slice(0, text.indexOf('```')).trim();
    if (preRest) body.push(textBlock(preRest));
  }
  if (lang) {
    body.push(textBlock(lang.toUpperCase(), {
      size: 'Small', weight: 'Bolder', color: 'Accent', spacing: 'None',
    }));
  }
  body.push(textBlock(code, { fontType: 'Monospace', color: 'Accent', wrap: true }));
  const postRest = text.slice(text.lastIndexOf('```') + 3).trim();
  if (postRest) body.push(textBlock(postRest));
  return wrapCard(body);
}

/**
 * SectionCard — for structured replies with ## headings.
 * Each section becomes a Container with a bold title.
 */
function sectionCard(text) {
  const { sections, pre } = parseSections(text);
  const body = [];
  if (pre) body.push(textBlock(pre));
  for (const sec of sections) {
    body.push(textBlock(sec.title, { weight: 'Bolder', size: 'Medium', spacing: 'Medium' }));
    const body_ = sec.lines.join('\n').trim();
    if (body_) body.push(textBlock(body_));
  }
  return wrapCard(body);
}

/**
 * StatusCard — for /settings, /quota, /role structured data.
 * @param {string} title     — card heading
 * @param {Array<{key,val}>} facts
 * @param {Array<{title,value}>} [actions]  — Action.Submit buttons
 */
function statusCard(title, facts, actions = []) {
  const body = [
    textBlock(title, { weight: 'Bolder', size: 'Large' }),
    { type: 'FactSet', facts: facts.map(({ key, val }) => ({ title: key, value: String(val) })) },
  ];
  const cardActions = actions.map(({ title: t, value }) => ({
    type: 'Action.Submit',
    title: t,
    data: { text: value },
  }));
  return wrapCard(body, cardActions);
}

/**
 * fromText — auto-dispatch: pick the right card type from the reply text.
 * Used by the outbox poller to convert any adapter reply to a Teams card.
 */
function fromText(text) {
  const t = String(text || '');
  if (t.includes('```'))    return codeCard(t);
  if (t.match(/^## /m))     return sectionCard(t);
  return plainCard(t);
}

module.exports = { plainCard, codeCard, sectionCard, statusCard, fromText };
