"""Single sanitisation chokepoint for everything that enters a report PDF.

These three reports are **evidence**: a regulator reads them as a statement
of what the operator's audit chain contains. Every value they render other
than the code's own literal template strings comes from outside the code —
the audit chain (``event_type``, ``details.*``, ``hash``, ``prev_hash``,
``severity``), ``verify_chain``'s problem dicts, or the caller's
``tenant_id``. None of it was escaped before 2026-09-07.

Why that mattered (all three reproduced against the real generators):

* ``reportlab``'s :class:`~reportlab.platypus.Paragraph` interprets a
  mini-HTML dialect — ``<font>``, ``<b>``, ``<i>``, ``<para>``, ``<a>``,
  ``<img>``. ``templates.signed_footer_block`` interpolated the chain's
  last-event ``hash`` into ``<font …>{h}</font>`` and
  ``templates.integrity_banner`` interpolated ``verify_chain``'s problem
  strings into ``&bull; {p}``. A record carrying
  ``"hash": '<font color="#ffffff">0000…</font><font color="#000000">CHAIN
  VERIFIED CLEAN BY AUDITOR</font>'`` made the **hash-chain anchor** — the
  one evidentiary anchor of the document — read "CHAIN VERIFIED CLEAN BY
  AUDITOR", with the real value painted white-on-white. That is content
  injection into the text layer of an evidence document.
* The same surface with *unbalanced* markup (a bare ``<``) raises
  ``ValueError: Parse error`` out of ``doc.build()``, so a single crafted
  record makes ALL THREE regulator-facing reports ungeneratable — the
  chain-attestation report most of all, which is exactly the document an
  operator reaches for when tampering is suspected.
* Control characters, NUL and the bidi overrides (U+202A–U+202E,
  U+2066–U+2069) reorder or corrupt displayed text in both the Paragraph
  and the plain ``Table``/canvas surfaces.

The rule is: sanitise where a value ENTERS the document, never at each call
site. Two functions, applied inside ``templates`` — :func:`pdf_markup` for
anything interpolated into a ``Paragraph``, :func:`pdf_text` for anything
handed to a ``Table`` cell, a ``canvas`` string or a PDF metadata field.
The code's own literal template strings (intro paragraphs, section headings)
deliberately carry markup and are NOT routed through here.

Escaping must stay reversible in the reader's eye: an escaped ``<``
displays as ``<``. Values are never silently dropped — a hostile value
renders as its own literal text, which is the truthful rendering.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE = _REPO / "operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))


class ReportValueRejected(ValueError):
    """A value outside its closed vocabulary reached the report boundary."""


# C0 (minus nothing — no control char is legitimate in any of these fields),
# DEL, C1, and the Unicode format/bidi characters that reorder or hide text:
# zero-width space/joiners + LRM/RLM (200B–200F), the bidi embeddings and
# overrides (202A–202E), word-joiner + invisible operators (2060–2064), the
# bidi isolates (2066–2069), and BOM/ZWNBSP (FEFF).
_UNSAFE_CHARS = re.compile(
    "[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤⁦-⁩﻿]"
)

#: Default cap. Long enough for every legitimate value the reports render
#: (a 16-char chain hash, an engine id, a ``verify_chain`` problem dict),
#: short enough that a hostile record cannot blow up a table cell or push
#: real content off the page.
MAX_LEN = 400

#: Table cells sit in fixed-width columns; cap them harder.
MAX_LEN_CELL = 200


def _clean(value: object, max_len: int) -> str:
    """Coerce → strip control/bidi → cap. Shared by both public helpers."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = _UNSAFE_CHARS.sub("", text)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def pdf_text(value: object, *, max_len: int = MAX_LEN) -> str:
    """Plain text for a ``Table`` cell, a ``canvas`` string or PDF metadata.

    These surfaces do NOT parse markup, so ``<`` stays ``<`` and reads
    correctly; only control and bidi characters are removed.
    """
    return _clean(value, max_len)


def pdf_markup(value: object, *, max_len: int = MAX_LEN) -> str:
    """Escaped text safe to interpolate into a ``Paragraph``.

    ``&``, ``<`` and ``>`` become entities, so reportlab's paraparser reads
    them as text rather than markup and the reader still sees ``&``/``<``/``>``.
    Escaping happens AFTER the length cap so a cap can never split an entity.
    """
    text = _clean(value, max_len)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def validated_tenant_id(tenant_id: object) -> str:
    """Return ``tenant_id`` if it is inside the closed tenant-id vocabulary.

    ``tenant_id`` is not free text: it is ``[a-z0-9_][a-z0-9_-]{0,62}`` per
    ``forge.tenants.validate_tenant_id``, the same contract the on-disk
    tenant-home resolver enforces. A value outside it is a bug at the
    caller, so it is REJECTED rather than escaped — escaping would let a
    report be produced for a tenant that can never own an audit chain.

    The generators reach the resolver's own validation only when they
    resolve the chain path themselves; passing ``chain_path=`` explicitly
    (a public parameter of all three ``generate()`` functions) short-circuits
    it, and the unvalidated id then flowed into the cover Paragraph, the
    per-page canvas header and the PDF ``Subject`` metadata field.
    """
    from forge.tenants import InvalidTenantID, validate_tenant_id  # noqa: PLC0415

    try:
        return validate_tenant_id(tenant_id)  # type: ignore[arg-type]
    except InvalidTenantID as exc:
        raise ReportValueRejected(f"tenant_id rejected: {exc}") from exc


__all__ = [
    "MAX_LEN",
    "MAX_LEN_CELL",
    "ReportValueRejected",
    "pdf_markup",
    "pdf_text",
    "validated_tenant_id",
]
