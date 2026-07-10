"""Set-of-Marks perception (ADR-0182 Pillar A).

Instead of pixel-coordinate clicking, we project the live page to a *numbered
list* of interactive elements (the accessibility-relevant subset of the DOM) and
optionally paint those numbers as overlay boxes on a screenshot. The model then
acts by INDEX — ``click(7)`` / ``fill(3, "…")`` — which is robust to layout
changes and works with any engine (no vision required for the list itself).

The heavy lifting is one injected JS pass (`_COLLECT_JS`) that runs in the page
context and returns a compact, token-bounded list. Nothing here reaches the
network or the filesystem; it is pure DOM introspection.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

# Maximum marks returned per observation — keeps the model prompt bounded.
MAX_MARKS = 120

# One DOM pass: collect visible, interactive elements with a stable index, an
# accessible role + name, and a bounding box. Also stamps ``data-corvin-mark`` on
# each element so a later action can resolve an index back to the exact node
# without a second heuristic pass (avoids index drift between observe and click).
#
# ADR-0183 S2 iframe traversal: this same pass runs once per FRAME (main
# document + every same-page iframe, same-origin or cross-origin — Playwright's
# ``Frame.evaluate`` has privileged access regardless of origin). Each frame's
# marks must still land on a single, globally-unique index across the whole
# observation, so the caller passes an ``offset`` (the running count of marks
# already collected in earlier frames) and this pass numbers its own marks
# starting there instead of always at 0. The ``data-corvin-mark`` attribute
# value written into that frame's DOM is therefore already the GLOBAL index —
# ``session.py`` only needs to remember which frame owns which index
# (``BrowserSession._mark_frame``) to resolve it back correctly later.
_COLLECT_JS = r"""
(opts) => {
  const maxMarks = (opts && opts.maxMarks) || 0;
  const offset = (opts && opts.offset) || 0;
  const INTERACTIVE = new Set([
    'a','button','input','select','textarea','summary','option','label'
  ]);
  const ROLE_INTERACTIVE = new Set([
    'button','link','textbox','checkbox','radio','combobox','menuitem',
    'tab','switch','searchbox','option','slider','spinbutton'
  ]);
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = window.getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
    // must intersect the viewport
    if (r.bottom < 0 || r.top > window.innerHeight) return false;
    if (r.right < 0 || r.left > window.innerWidth) return false;
    return true;
  }
  function accName(el) {
    // SECURITY: never echo a field's *value* — a typed secret/PII (card number,
    // TOTP, email) would otherwise be read back as the element name on the next
    // observe() and leak into the model context + action log. Use only the
    // stable label attributes; for editable fields fall back to their type.
    //
    // Every branch is hard-capped to NAME_CAP chars. An UNBOUNDED accessible
    // name (aria-label/name/title/placeholder were previously returned whole)
    // is an indirect-prompt-injection vector: a page can stuff a multi-line
    // aria-label containing the agent's UNTRUSTED-CONTENT fence delimiter plus
    // forged operator instructions and break out of the fence (ADR-0183 S1
    // hardening). Capping every field bounds that payload to one short label.
    const NAME_CAP = 100;
    const cap = (s) => s.slice(0, NAME_CAP);
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return cap(aria.trim());
    if (el.getAttribute('placeholder')) return cap(el.getAttribute('placeholder').trim());
    if (el.getAttribute('name')) return cap(el.getAttribute('name').trim());
    if (el.getAttribute('title')) return cap(el.getAttribute('title').trim());
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || el.isContentEditable) {
      return (el.getAttribute('type') || 'text') + ' field';   // never el.value
    }
    const t = (el.innerText || el.textContent || '').trim();
    return cap(t);
  }
  function role(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || (tag === 'input' && el.type === 'button')) return 'button';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'submit') return 'button';
      if (t === 'password') return 'password';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    return tag;
  }
  function interactive(el) {
    const tag = el.tagName.toLowerCase();
    if (INTERACTIVE.has(tag)) return true;
    const r = (el.getAttribute('role') || '').toLowerCase();
    if (ROLE_INTERACTIVE.has(r)) return true;
    if (el.hasAttribute('onclick')) return true;
    if (el.getAttribute('tabindex') !== null && el.getAttribute('tabindex') !== '-1') return true;
    if (el.isContentEditable) return true;
    return false;
  }
  // clear any stale marks from a previous observe
  document.querySelectorAll('[data-corvin-mark]').forEach(e => e.removeAttribute('data-corvin-mark'));

  const all = Array.from(document.querySelectorAll('*'));
  const out = [];
  let idx = offset;
  for (const el of all) {
    if (out.length >= maxMarks) break;
    if (!interactive(el)) continue;
    if (!visible(el)) continue;
    const rl = role(el);
    // Password fields ARE surfaced (role 'password') so a login/credential
    // flow has a reachable target for fill_secret — WITHOUT this a vault
    // credential could never be aimed at the password box (ADR-0183 S1: the
    // advertised fill_secret capability was structurally unusable). This is
    // safe because accName() NEVER reads el.value — a password mark carries
    // only its static label ("password field" / its placeholder), never the
    // typed secret. Consumers gate these to fill_secret; plain fill/click on a
    // password mark stays possible but is never what the agent is steered to.
    const r = el.getBoundingClientRect();
    el.setAttribute('data-corvin-mark', String(idx));
    out.push({
      index: idx,
      role: rl,
      name: accName(el),
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
    });
    idx++;
  }
  return { url: location.href, title: document.title, marks: out };
}
"""

# Paint numbered boxes over the marked elements (for the screenshot / live view).
# Purely cosmetic; removed again by `_UNPAINT_JS`.
_PAINT_JS = r"""
() => {
  const id = 'corvin-marks-overlay';
  document.getElementById(id)?.remove();
  const layer = document.createElement('div');
  layer.id = id;
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  document.querySelectorAll('[data-corvin-mark]').forEach(el => {
    const r = el.getBoundingClientRect();
    const n = el.getAttribute('data-corvin-mark');
    const box = document.createElement('div');
    box.style.cssText = `position:absolute;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;border:2px solid #e11d48;box-sizing:border-box;`;
    const tag = document.createElement('div');
    tag.textContent = n;
    tag.style.cssText = `position:absolute;left:${r.left}px;top:${Math.max(0,r.top-14)}px;background:#e11d48;color:#fff;font:11px/14px monospace;padding:0 3px;`;
    layer.appendChild(box); layer.appendChild(tag);
  });
  document.body.appendChild(layer);
}
"""

_UNPAINT_JS = "() => document.getElementById('corvin-marks-overlay')?.remove()"

# Stale-mark self-healing (ADR-0183 S1): re-derive the SAME accessible-name
# fingerprint `accName()` computes above, but as a standalone snippet evaluated
# directly on an already-resolved element handle (``el.evaluate(...)``) rather
# than during the full-page collection pass. Session.py compares this live
# value against the ``Mark.name`` captured at the last observe() before acting
# on an index, to detect an in-place DOM re-render (SPA index drift).
# SECURITY: mirrors accName() exactly — never reads el.value, so a fingerprint
# check can never leak a typed secret back into the comparison / model context.
_FINGERPRINT_JS = r"""
(el) => {
  const NAME_CAP = 100;                       // must match accName()'s cap exactly,
  const cap = (s) => s.slice(0, NAME_CAP);    // else a long label reads as "stale"
  const aria = el.getAttribute('aria-label');
  if (aria && aria.trim()) return cap(aria.trim());
  if (el.getAttribute('placeholder')) return cap(el.getAttribute('placeholder').trim());
  if (el.getAttribute('name')) return cap(el.getAttribute('name').trim());
  if (el.getAttribute('title')) return cap(el.getAttribute('title').trim());
  const tag = el.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || el.isContentEditable) {
    return (el.getAttribute('type') || 'text') + ' field';   // never el.value
  }
  const t = (el.innerText || el.textContent || '').trim();
  return cap(t);
}
"""

# Sensitivity model v2 (ADR-0183 S1): does the <form> enclosing this element
# contain a password field or a card-number-labelled field? Used as an
# additional, best-effort signal so an ambiguously-labelled commit button
# ("Continue", "OK") inside a payment/credential form is still flagged
# sensitive even though its own accessible name matches no keyword. Scoped to
# the enclosing form only (not the whole page) to avoid over-flagging every
# click on a page that merely CONTAINS a login form elsewhere.
_FORM_SENSITIVE_JS = r"""
(el) => {
  const form = el.closest ? el.closest('form') : null;
  if (!form) return false;
  if (form.querySelector('input[type="password"]')) return true;
  const pattern = /card.?number|cvv|cvc|card.?code|kreditkarte|kartennummer/i;
  const fields = form.querySelectorAll('input, select, textarea');
  for (const f of fields) {
    const label = (f.getAttribute('aria-label') || f.getAttribute('placeholder') ||
                   f.getAttribute('name') || f.getAttribute('id') || '');
    if (pattern.test(label)) return true;
  }
  return false;
}
"""

# Commit-gating (ADR-0183 S1 hardening): does the form enclosing the CURRENTLY
# FOCUSED element contain a password / card-number field? Used to gate a
# key("Enter"/"Space") press — which can submit a form without any click ever
# happening — through the SAME human-in-the-loop confirm as a sensitive click,
# closing the "Enter bypasses the sensitivity gate" hole. SECURITY: reads only
# static labels of the focused element's form, never any field value.
_ACTIVE_FORM_SENSITIVE_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || !el.closest) return false;
  const form = el.closest('form');
  if (!form) return false;
  if (form.querySelector('input[type="password"]')) return true;
  const pattern = /card.?number|cvv|cvc|card.?code|kreditkarte|kartennummer/i;
  for (const f of form.querySelectorAll('input, select, textarea')) {
    const label = (f.getAttribute('aria-label') || f.getAttribute('placeholder') ||
                   f.getAttribute('name') || f.getAttribute('id') || '');
    if (pattern.test(label)) return true;
  }
  return false;
}
"""

# Structured extraction (ADR-0183 S2): turn a <table> (or an ARIA role="table"/
# "grid" container that wraps one, or is one itself) into {headers, rows} —
# NEVER reaching into form-field *values* (there are none in a table's own
# markup) so this stays inside the same "text/labels only" compliance model as
# the rest of Set-of-Marks. Runs via ``ElementHandle.evaluate`` on an already
# ``_resolve()``-d element, so it inherits the same stale-mark protection as
# every other action. Bounded to ``maxRows`` (session.py caps this at 200) so a
# huge table can't blow the model's context.
_EXTRACT_TABLE_JS = r"""
(el, maxRows) => {
  function cellText(c) { return (c.innerText || c.textContent || '').trim(); }
  let table = el;
  if (!el.tagName || el.tagName.toLowerCase() !== 'table') {
    const inner = el.querySelector('table');
    if (inner) table = inner;
  }
  let rows = Array.from(table.querySelectorAll(':scope > thead > tr, :scope > tbody > tr, :scope > tr'));
  if (rows.length === 0) {
    rows = Array.from(table.querySelectorAll('[role="row"]'));
  }
  let headers = [];
  let bodyRows = rows;
  if (rows.length > 0) {
    const headCells = rows[0].querySelectorAll('th, [role="columnheader"]');
    if (headCells.length > 0) {
      headers = Array.from(headCells).map(cellText);
      bodyRows = rows.slice(1);
    }
  }
  const out = [];
  for (const r of bodyRows) {
    if (out.length >= maxRows) break;
    const cells = Array.from(
      r.querySelectorAll('td, th, [role="cell"], [role="gridcell"], [role="columnheader"]'));
    out.push(cells.map(cellText));
  }
  return { headers, rows: out };
}
"""

# Structured extraction (ADR-0183 S2): walk every <form> on the CURRENT
# top-level document (does not descend into iframes — use extract_table or a
# per-frame observe() for iframe-embedded forms) and describe its shape —
# action/method + one entry per field (name/type/required/label). SECURITY:
# reads only static attributes and the associated <label>; NEVER ``f.value``,
# so an in-progress password/PII entry can never leak through this path.
# Hidden inputs are skipped (usually CSRF tokens / internal state, not
# something the model needs to reason about). Bounded to 50 forms / 100
# fields per form so a pathological page can't blow the response.
_EXTRACT_FORMS_JS = r"""
() => {
  function fieldInfo(f) {
    const tag = f.tagName.toLowerCase();
    let type = (f.getAttribute('type') ||
                (tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : 'text')).toLowerCase();
    let label = (f.getAttribute('aria-label') || f.getAttribute('placeholder') || '').trim();
    if (!label && f.labels && f.labels.length > 0) {
      label = (f.labels[0].innerText || f.labels[0].textContent || '').trim();
    }
    if (!label) label = (f.getAttribute('name') || '').trim();
    return {
      name: f.getAttribute('name') || '',
      type,
      required: f.hasAttribute('required'),
      label,
    };
  }
  const forms = Array.from(document.querySelectorAll('form'));
  return forms.slice(0, 50).map(form => {
    const fields = Array.from(form.querySelectorAll('input, select, textarea'))
      .filter(f => (f.getAttribute('type') || '').toLowerCase() !== 'hidden')
      .slice(0, 100)
      .map(fieldInfo);
    return {
      action: form.getAttribute('action') || '',
      method: (form.getAttribute('method') || 'get').toLowerCase(),
      fields,
    };
  });
}
"""


@dataclass
class Mark:
    index: int
    role: str
    name: str
    bbox: list[int]  # [x, y, w, h] in CSS px, viewport-relative


@dataclass
class Observation:
    url: str
    title: str
    marks: list[Mark]

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "marks": [asdict(m) for m in self.marks],
        }

    def as_text(self) -> str:
        """Compact numbered list for the model prompt."""
        lines = [f"[{m.index}] {m.role}: {m.name}" if m.name else f"[{m.index}] {m.role}"
                 for m in self.marks]
        header = f"page: {self.title}  ({self.url})  — {len(self.marks)} interactive elements"
        return header + "\n" + "\n".join(lines)
