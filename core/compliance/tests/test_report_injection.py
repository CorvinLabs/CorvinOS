"""Regression: hostile values must not become report CONTENT.

These three PDFs are evidence a regulator reads as a statement about the
operator's audit chain. Before 2026-09-07 the package had no sanitisation
anywhere (``grep -E 'sanitiz|escape'`` returned nothing) while it rendered
values that originate outside the code:

* ``templates.signed_footer_block`` interpolated the chain's last-event
  ``hash`` into ``<font …>{h}</font>`` and ``templates.integrity_banner``
  interpolated ``verify_chain``'s problem dicts into ``&bull; {p}``.
  ``reportlab``'s ``Paragraph`` interprets that mini-HTML, so a record
  carrying ``"hash": '<font color="#ffffff">0000…</font><font
  color="#000000">CHAIN VERIFIED CLEAN BY AUDITOR</font>'`` repainted the
  **hash-chain anchor** — the document's one evidentiary anchor — to read
  "CHAIN VERIFIED CLEAN BY AUDITOR", real value white-on-white.
* The same surface with *unbalanced* markup (a bare ``<``) raised
  ``ValueError: Parse error`` out of ``doc.build()``, so one crafted record
  made all three reports ungeneratable — the chain-attestation report
  included, i.e. exactly the document reached for when tampering is
  suspected.
* ``tenant_id`` reached the cover Paragraph, the per-page canvas header and
  the PDF ``Subject`` metadata field. Passing ``chain_path=`` explicitly (a
  public parameter of all three ``generate()`` functions) short-circuits the
  tenant-home resolver that would otherwise have validated it.

Every test here drives the REAL operator boundary — a ``python -m
corvin_compliance_reports.cli`` subprocess — except the two that assert the
closed-vocabulary rejection at the library entry point that the CLI cannot
reach (``chain_path=``).

The PDF is read back with a dependency-free text-layer extractor
(ASCII85 + Flate + ``(…) Tj``), so the assertions do not need poppler.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

from corvin_compliance_reports import audit_attestation, sanitize

_REPORT_TYPES = ["ai-act-50", "gdpr-30", "audit-attestation"]

# Balanced markup: reportlab parses it happily, so pre-fix this was rendered
# rather than shown. The white run hides the real digest; the black run
# states something the chain does not say.
_REPAINT_ANCHOR = (
    '<font color="#ffffff">0000000000000000</font>'
    '<font color="#000000">CHAIN VERIFIED CLEAN BY AUDITOR</font>'
)
# Unbalanced markup: pre-fix this raised out of doc.build() and the CLI
# exited 3 with "report generation failed" for every report type.
_CRASH_ANCHOR = '</font><font color="white">HIDDEN</font><font name="Courier" size="8">'


# ── PDF text-layer extraction (no external tooling) ───────────────────

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_PDFSTR_RE = re.compile(rb"\((?:\\.|[^()\\])*\)", re.S)


def _decode_stream(body: bytes) -> bytes:
    b = body.strip()
    candidates = [b]
    try:
        candidates.append(base64.a85decode(b, adobe=True))
    except Exception:  # noqa: BLE001 - not an ASCII85 stream
        pass
    for c in candidates:
        try:
            return zlib.decompress(c)
        except Exception:  # noqa: BLE001 - not a Flate stream
            continue
    return b


_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t",
                b"b": b"\b", b"f": b"\f"}


def _unescape(s: bytes) -> bytes:
    """PDF literal-string escapes: \\ooo octal, \\n and friends, \\( \\) \\\\."""
    def repl(m: "re.Match[bytes]") -> bytes:
        body = m.group(1)
        if body.isdigit():
            return bytes([int(body, 8) & 0xFF])
        return _PDF_ESCAPES.get(body, body)
    return re.sub(rb"\\([0-7]{1,3}|.)", repl, s, flags=re.S)


def pdf_text_layer(pdf: Path) -> str:
    """Concatenate every show-text string in the document.

    reportlab writes WinAnsi (cp1252) bytes for the standard fonts, so the
    result is decoded that way — an umlaut must come back as an umlaut.
    """
    raw = pdf.read_bytes()
    chunks: list[bytes] = []
    for m in _STREAM_RE.finditer(raw):
        decoded = _decode_stream(m.group(1))
        for s in _PDFSTR_RE.finditer(decoded):
            chunks.append(_unescape(s.group(0)[1:-1]))
    return b" ".join(chunks).decode("cp1252", "replace")


def nows(text: str) -> str:
    """Whitespace-free view.

    reportlab splits a Paragraph into one show-text run per word, and the
    extractor rejoins them with a space — so a *substring* assertion about
    escaped markup has to ignore run boundaries.
    """
    return re.sub(r"\s+", "", text)


def pdf_info_dict(pdf: Path) -> str:
    """The raw bytes of the /Info object — Title, Author, Subject, Keywords."""
    raw = pdf.read_bytes()
    out = []
    for m in re.finditer(rb"/(Title|Author|Subject|Keywords)\s*(\((?:\\.|[^()\\])*\)|<[0-9A-Fa-f\s]*>)", raw, re.S):
        out.append(m.group(0))
    return b" ".join(out).decode("latin-1")


# ── CLI driver ────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_PLUGIN_ROOT = _HERE.parent          # core/compliance/
_REPO = _PLUGIN_ROOT.parent.parent
_FORGE = _REPO / "operator" / "forge"


def run_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_PLUGIN_ROOT), str(_FORGE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-m", "corvin_compliance_reports.cli", *args],
        capture_output=True, text=True, env=env, timeout=180,
    )


def append_raw_record(chain: Path, **fields) -> None:
    """Append a record verbatim — the only way to put a chosen string in
    ``hash``/``prev_hash``, which ``write_event`` otherwise computes."""
    rec = {
        "ts": time.time(), "event_type": "consent.granted",
        "severity": "INFO", "details": {"uid": "u1"},
        "prev_hash": "deadbeefdeadbeef",
    }
    rec.update(fields)
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


@pytest.fixture
def hostile_chain(sandbox_home, seed_chain, chain_path):
    """A chain carrying every externally-influenced field we render."""
    seed_chain("disclosure.shown", channel="discord", chat_key="c1",
               uid="LEGITuser")
    seed_chain("disclosure.action", channel="discord", chat_key="c1",
               uid="<b>ADMIN</b>", action='<font color="white">joined</font>')
    seed_chain("gateway.run_created", engine="claude_code",
               compliance_zone="eu-central")
    return chain_path


# ── 1. The crash — evidence suppression ───────────────────────────────

@pytest.mark.parametrize("report_type", _REPORT_TYPES)
def test_unbalanced_markup_in_chain_does_not_kill_the_report(
    hostile_chain, sandbox_home, tmp_path, report_type,
):
    """A crafted record must not make a regulator report ungeneratable."""
    append_raw_record(hostile_chain, hash=_CRASH_ANCHOR)
    out = tmp_path / f"{report_type}.pdf"

    proc = run_cli("generate", report_type, "--tenant", "_default",
                   "--since", "3650d", "--output", str(out),
                   home=sandbox_home)

    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes().startswith(b"%PDF")


# ── 2. The repaint — content injection into the anchor ────────────────

def test_hash_anchor_cannot_be_repainted(
    hostile_chain, sandbox_home, tmp_path,
):
    """The anchor must show the value ON DISK, not markup it was made of."""
    append_raw_record(hostile_chain, hash=_REPAINT_ANCHOR)
    out = tmp_path / "attest.pdf"

    proc = run_cli("generate", "audit-attestation", "--tenant", "_default",
                   "--since", "3650d", "--output", str(out),
                   home=sandbox_home)
    assert proc.returncode == 0, proc.stderr

    text = nows(pdf_text_layer(out))
    # The attestation prints the last-event hash TWICE: once in the
    # "Chain anchors" table (a plain Table cell, which reportlab has always
    # rendered literally) and once in the "Hash-chain anchor" Paragraph.
    # Pre-fix the Paragraph consumed the payload as markup, so the literal
    # appeared exactly once. Post-fix both places show the value that is on
    # disk — and escaping is reversible in the reader's eye, so the `<`
    # displays as `<` rather than vanishing.
    payload = nows(_REPAINT_ANCHOR)
    assert text.count(payload) >= 2, text
    assert "CHAINVERIFIEDCLEANBYAUDITOR</font>" in text


def test_verify_chain_problem_strings_are_escaped(
    hostile_chain, sandbox_home, tmp_path,
):
    """The integrity banner lists ``verify_chain`` problems, whose
    ``actual_hash`` / ``actual_prev`` / ``event_type`` come verbatim from a
    chain that is broken — i.e. by definition untrusted."""
    append_raw_record(hostile_chain, hash=_REPAINT_ANCHOR)
    out = tmp_path / "aiact.pdf"

    proc = run_cli("generate", "ai-act-50", "--tenant", "_default",
                   "--since", "3650d", "--output", str(out),
                   home=sandbox_home)
    assert proc.returncode == 0, proc.stderr

    text = nows(pdf_text_layer(out))
    assert "integrityFAILED" in text        # the banner still tells the truth
    assert "'issue':'tampered'" in text     # ... and still names the problem
    assert '<fontcolor="#ffffff">' in text  # ... with the payload as text


# ── 3. Control + bidi characters ──────────────────────────────────────

def test_control_and_bidi_characters_are_stripped(
    sandbox_home, seed_chain, tmp_path,
):
    """NUL, C0 and the bidi overrides never reach the page.

    Defence in depth, not a demonstrated pre-fix exploit: reportlab's
    standard fonts are single-byte WinAnsi, so these codepoints already fell
    out as ``notdef`` before the fix. They would start reordering text the
    day the branding tokens switch to an embedded TTF (``templates`` already
    anticipates operator-supplied branding), and they DO survive verbatim
    into UTF-16 PDF metadata strings — so they are removed at the
    chokepoint rather than left to the font's accidental protection."""
    seed_chain("disclosure.shown", channel="discord", chat_key="c1",
               uid="alice‮detacihtua‬\x00\x07")
    out = tmp_path / "aiact.pdf"

    proc = run_cli("generate", "ai-act-50", "--tenant", "_default",
                   "--since", "3650d", "--output", str(out),
                   home=sandbox_home)
    assert proc.returncode == 0, proc.stderr

    text = pdf_text_layer(out)
    assert "alice" in text                      # legitimate part survives
    for bad in ("‮", "‬", "\x00", "\x07"):
        assert bad not in text


# ── 4. tenant_id is a closed vocabulary, not free text ────────────────

def test_cli_rejects_hostile_tenant_id(sandbox_home, tmp_path):
    out = tmp_path / "nope.pdf"
    proc = run_cli("generate", "audit-attestation",
                   "--tenant", '<b>ACME AG (certified)</b>',
                   "--since", "30d", "--output", str(out),
                   home=sandbox_home)
    assert proc.returncode != 0
    assert not out.exists()


def test_explicit_chain_path_does_not_bypass_tenant_validation(
    sandbox_home, chain_path, tmp_path,
):
    """``chain_path=`` short-circuits the tenant-home resolver, which used
    to be the only thing validating ``tenant_id`` before it reached the
    cover Paragraph, the canvas header and the PDF Subject field."""
    chain_path.write_text("")
    with pytest.raises(sanitize.ReportValueRejected):
        audit_attestation.generate(
            tenant_id='x</b><font color="#ffffff">',
            start_ts=0, end_ts=int(time.time()) + 1000,
            output_path=tmp_path / "nope.pdf",
            chain_path=chain_path,
        )
    assert not (tmp_path / "nope.pdf").exists()


def test_pdf_metadata_fields_carry_no_injected_markup(
    hostile_chain, sandbox_home, tmp_path,
):
    """Title / Author / Subject / Keywords are a document surface of their
    own (tab caption, DMS index entry)."""
    append_raw_record(hostile_chain, hash=_REPAINT_ANCHOR)
    out = tmp_path / "ropa.pdf"

    proc = run_cli("generate", "gdpr-30", "--tenant", "_default",
                   "--since", "3650d", "--output", str(out),
                   home=sandbox_home)
    assert proc.returncode == 0, proc.stderr

    info = pdf_info_dict(out)
    assert "/Subject" in info
    assert "_default" in info
    assert "<font" not in info
    assert "CHAIN VERIFIED" not in info


# ── 5. The reports must still READ correctly ──────────────────────────

@pytest.mark.parametrize("report_type", _REPORT_TYPES)
def test_legitimate_content_is_unchanged(
    sandbox_home, seed_chain, tmp_path, report_type,
):
    """Sanitisation must not corrupt real audit data: a regulator has to be
    able to read it, non-ASCII names included."""
    seed_chain("disclosure.shown", channel="discord", chat_key="c1",
               uid="jürgen-müller")
    seed_chain("consent.granted", channel="discord", chat_key="c1",
               uid="jürgen-müller", mode="durable")
    seed_chain("gateway.run_created", engine="claude_code",
               compliance_zone="eu-central")
    out = tmp_path / f"{report_type}.pdf"

    proc = run_cli("generate", report_type, "--tenant", "_default",
                   "--since", "3650d", "--output", str(out),
                   home=sandbox_home)
    assert proc.returncode == 0, proc.stderr

    text = pdf_text_layer(out)
    assert "Corvin  ·  Compliance Report" in text
    assert "Tenant _default" in text
    assert "integrity verified" in text     # intact chain reads intact
    # The 16-hex anchor is printed verbatim, not mangled by the escaper.
    anchor = re.search(r"anchor\s+:\s+([0-9a-f]{16})", proc.stdout)
    assert anchor, proc.stdout
    assert anchor.group(1) in text
    if report_type == "ai-act-50":
        assert "jürgen-müller" in text


# ── 6. The chokepoint itself ──────────────────────────────────────────

def test_pdf_markup_escape_is_reversible_in_the_readers_eye():
    assert sanitize.pdf_markup("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_pdf_text_leaves_angle_brackets_alone():
    """Table cells and canvas strings do not parse markup, so escaping there
    would show the reader ``&lt;`` where the data says ``<``."""
    assert sanitize.pdf_text("a < b") == "a < b"


def test_length_cap_never_splits_an_entity():
    out = sanitize.pdf_markup("<" * 500, max_len=10)
    assert "&lt" not in out.replace("&lt;", "")
    assert out.count("&lt;") == 9
