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
_COLLECT_JS = r"""
(maxMarks) => {
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
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    if (el.getAttribute('placeholder')) return el.getAttribute('placeholder').trim();
    if (el.getAttribute('name')) return el.getAttribute('name').trim();
    if (el.getAttribute('title')) return el.getAttribute('title').trim();
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || el.isContentEditable) {
      return (el.getAttribute('type') || 'text') + ' field';   // never el.value
    }
    const t = (el.innerText || el.textContent || '').trim();
    return t.slice(0, 80);
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
  let idx = 0;
  for (const el of all) {
    if (out.length >= maxMarks) break;
    if (!interactive(el)) continue;
    if (!visible(el)) continue;
    const rl = role(el);
    if (rl === 'password') continue; // never surface password fields as targets to the model
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
