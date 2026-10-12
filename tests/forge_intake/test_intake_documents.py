"""Documents through the intake stages (ADR-2254 slice 2, T-0157): real files (the OOXML ones built with zipfile), the real
extractors, secret scan and redactor. The gate is a stand-in; the console's own is exercised in the route tests."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from core.forge_intake.intake import analyze_item, build_pack

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@pytest.fixture
def area(tmp_path):
    up = tmp_path / "uploads"
    up.mkdir()
    return up


def run(area, p, gate=lambda t: None):
    return analyze_item(p, item_id="d1", upload_root=area, tenant_id="_default", gate=gate)


def zipped(area, name, parts: dict[str, str | bytes]) -> Path:
    p = area / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in parts.items():
            z.writestr(n, d)
    return p


def docx(area, text, name="spec.docx"):
    paras = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in text)
    return zipped(area, name, {"word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{paras}</w:body></w:document>',
                               "[Content_Types].xml": "<Types/>"})


def test_markdown_is_admitted_and_its_email_and_phone_are_redacted(area):
    p = area / "notes.md"
    p.write_text("# Runbook\nContact anna.schmidt@example.org or +49 30 1234567 when the job fails.\n")
    r = run(area, p)
    assert r.report.verdict == "admitted_redacted" and set(r.report.pii_kinds) == {"email", "phone"}
    text = r.projection["text"]
    assert "anna.schmidt" not in text and "1234567" not in text and "Runbook" in text and "<redacted:email>" in text


def test_a_plain_document_without_pii_is_admitted_unchanged(area):
    p = area / "plain.txt"
    p.write_text("How to deploy: build, test, ship.\n")
    r = run(area, p)
    assert r.report.verdict == "admitted" and r.projection["text"].startswith("How to deploy")


def test_docx_text_is_extracted_in_paragraph_order(area):
    r = run(area, docx(area, ["First paragraph.", "Second one with bob@example.org."]))
    assert r.report.verdict == "admitted_redacted" and r.report.fmt == "docx"
    assert r.projection["text"].index("First") < r.projection["text"].index("Second")
    assert "bob@example.org" not in r.projection["text"]


def test_pptx_and_xlsx_are_extracted(area):
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    s = zipped(area, "deck.pptx", {"ppt/slides/slide1.xml": f'<p:sld xmlns:p="x" xmlns:a="{a}"><a:p><a:r><a:t>Slide one title</a:t></a:r></a:p></p:sld>'})
    assert "Slide one title" in run(area, s).projection["text"]
    x = zipped(area, "book.xlsx", {"xl/workbook.xml": "<workbook/>",
                                   "xl/sharedStrings.xml": '<sst xmlns="m"><si><t>Quarterly revenue</t></si></sst>'})
    r = run(area, x)
    assert r.report.verdict.startswith("admitted") and "Quarterly revenue" in r.projection["text"]


def test_a_secret_anywhere_in_the_extracted_text_refuses_the_document(area):
    r = run(area, docx(area, ["harmless intro", "key: AKIAIOSFODNN7EXAMPLE"]))
    assert r.report.verdict == "refused" and r.report.reason_code == "contains_secret" and r.projection is None


def test_a_secret_past_the_part_shown_to_a_generator_is_still_found(area):
    p = area / "long.md"
    p.write_text("word " * 20000 + "\nAKIAIOSFODNN7EXAMPLE\n")   # the secret sits far beyond DOC_TEXT_MAX
    assert run(area, p).report.reason_code == "contains_secret"


def test_a_zip_bomb_and_an_xml_entity_file_are_refused_before_they_expand(area):
    bomb = zipped(area, "bomb.docx", {"word/document.xml": b"<w:document/>" + b"A" * (60 * 1024 * 1024)})
    t = time.time()
    r = run(area, bomb)
    assert r.report.verdict == "refused" and r.report.reason_code == "archive_too_large" and time.time() - t < 5
    evil = zipped(area, "evil.docx", {"word/document.xml":
                  '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><w:document xmlns:w="%s"><w:t>&x;</w:t></w:document>' % W})
    assert run(area, evil).report.reason_code == "unsafe_xml"


def test_a_zip_renamed_docx_without_the_document_part_is_a_mismatch_and_a_plain_zip_is_still_an_archive(area):
    assert run(area, zipped(area, "fake.docx", {"hello.txt": "hi"})).report.reason_code == "format_mismatch"
    assert run(area, zipped(area, "data.zip", {"a.txt": "hi"})).report.reason_code == "archive_not_supported"


def test_a_long_unbroken_run_cannot_stall_the_redactor(area):
    p = area / "blob.txt"
    p.write_text("a" * 1_500_000 + " end\n")
    t = time.time()
    r = run(area, p)
    assert r.report.verdict.startswith("admitted") and time.time() - t < 10, "regexes never see a megabyte-long word"


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_a_pdf_goes_through_pdftotext_and_without_it_is_refused_with_a_reason(area, monkeypatch):
    pdf = area / "r.pdf"
    pdf.write_bytes(b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
                    b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 10 50 Td (Hello intake) Tj ET\nendstream endobj\n"
                    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    r = run(area, pdf)
    assert r.report.verdict.startswith("admitted") and "Hello intake" in r.projection["text"]
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert run(area, pdf).report.reason_code == "extractor_unavailable"


def test_a_pdf_extension_on_other_bytes_is_a_mismatch(area):
    p = area / "x.pdf"
    p.write_text("not a pdf")
    assert run(area, p).report.reason_code == "format_mismatch"


def test_the_gate_sees_the_redacted_text_and_a_refusal_or_crash_refuses(area):
    p = area / "g.md"
    p.write_text("mail me: zed@example.org\n")
    seen = []
    run(area, p, gate=lambda t: seen.append(t))
    assert seen and "zed@example.org" not in seen[0]
    assert run(area, p, gate=lambda t: "no").report.reason_code == "classification_refused"
    def boom(_t): raise RuntimeError("x")
    assert run(area, p, gate=boom).report.verdict == "refused"


def test_a_pack_of_big_documents_stays_under_the_cap_and_says_it_shrank(area):
    items = []
    for i in range(5):
        p = area / f"d{i}.md"
        p.write_text(("lorem ipsum dolor sit amet " * 900))
        items.append(analyze_item(p, item_id=f"d{i}", upload_root=area, tenant_id="_default", gate=lambda t: None))
    pack = build_pack(items)
    assert len(json.dumps(pack).encode()) <= 24 * 1024 and "document_text" in pack["degraded"]
