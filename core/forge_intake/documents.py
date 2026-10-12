"""Text extraction for documents (ADR-2254, slice 2). Dependency-free where it can be: md/txt are read, docx/pptx/xlsx are
zip+XML read with hard caps and no entity expansion, pdf goes through the `pdftotext` binary when it exists.

Every function raises ``DocumentRefused(code)`` with a closed code; the caller turns that into a refusal. Nothing here
returns a file name or a value in an error.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

DOC_EXTENSIONS = {".md": "text", ".markdown": "text", ".txt": "text", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx", ".pdf": "pdf"}
MAX_TEXT_BYTES = 2 * 1024 * 1024          # extracted text kept for the secret scan
DOC_TEXT_MAX = 16 * 1024                  # extracted text that may reach a generator (before the pack cap)
MAX_ZIP_ENTRIES = 400
MAX_ZIP_PART_BYTES = 8 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 40 * 1024 * 1024
MAX_PARTS_READ = 120
_PDF_TIMEOUT_S = 20
_MAX_RUN = 256                            # no unbroken run of non-space characters longer than this reaches a regex


class DocumentRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _decode(raw: bytes) -> str:
    return raw[:MAX_TEXT_BYTES].decode("utf-8", "replace")


def _xml_text(raw: bytes, *, text_tags: tuple[str, ...], break_tags: tuple[str, ...]) -> str:
    # No DTD, no entities: an XML bomb or external entity is refused before the parser sees it.
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head or b"<!entity" in raw.lower():
        raise DocumentRefused("unsafe_xml")
    out: list[str] = []
    try:
        for _ev, el in ET.iterparse(__import__("io").BytesIO(raw), events=("end",)):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag in text_tags and el.text:
                out.append(el.text)
            elif tag in break_tags:
                out.append("\n")
            el.clear()
    except ET.ParseError:
        raise DocumentRefused("unreadable")
    return "".join(out)


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise DocumentRefused("unreadable")
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DocumentRefused("archive_too_large")
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL_BYTES or any(i.file_size > MAX_ZIP_PART_BYTES for i in infos):
        raise DocumentRefused("archive_too_large")
    if any(i.filename.startswith("/") or ".." in i.filename.split("/") for i in infos):
        raise DocumentRefused("unsafe_archive")
    return z


def _read_part(z: zipfile.ZipFile, name: str) -> bytes:
    with z.open(name) as fh:
        data = fh.read(MAX_ZIP_PART_BYTES + 1)   # the declared size is not trusted: the read itself is bounded
    if len(data) > MAX_ZIP_PART_BYTES:
        raise DocumentRefused("archive_too_large")
    return data


def _ooxml(path: Path, kind: str) -> str:
    z = _open_zip(path)
    names = {i.filename for i in z.infolist()}
    if kind == "docx":
        if "word/document.xml" not in names:
            raise DocumentRefused("format_mismatch")
        return _xml_text(_read_part(z, "word/document.xml"), text_tags=("t",), break_tags=("p", "br"))
    if kind == "pptx":
        slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if not slides:
            raise DocumentRefused("format_mismatch")
        return "\n\n".join(_xml_text(_read_part(z, n), text_tags=("t",), break_tags=("p",)) for n in slides[:MAX_PARTS_READ])
    if "xl/workbook.xml" not in names:
        raise DocumentRefused("format_mismatch")
    parts = ["xl/sharedStrings.xml"] if "xl/sharedStrings.xml" in names else []
    parts += sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))[:MAX_PARTS_READ]
    return "\n".join(_xml_text(_read_part(z, n), text_tags=("t", "v"), break_tags=("si", "row")) for n in parts)


def _pdf(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise DocumentRefused("extractor_unavailable")
    try:
        p = subprocess.run([exe, "-q", "-l", "60", "-nopgbrk", str(path), "-"], capture_output=True, timeout=_PDF_TIMEOUT_S, check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise DocumentRefused("unreadable")
    if p.returncode != 0:
        raise DocumentRefused("unreadable")
    return _decode(p.stdout)


def extract_text(path: Path, kind: str) -> str:
    """The plain text of a document, bounded. ``kind`` is a value of ``DOC_EXTENSIONS``."""
    if kind == "text":
        return _decode(path.read_bytes()[:MAX_TEXT_BYTES])
    if kind in ("docx", "pptx", "xlsx"):
        return _ooxml(path, kind)[:MAX_TEXT_BYTES]
    if kind == "pdf":
        return _pdf(path)
    raise DocumentRefused("unsupported_type")


def bound_runs(text: str) -> str:
    """Cut any unbroken run of non-space characters to ``_MAX_RUN``: the PII patterns are not linear on a megabyte-long word."""
    return re.sub(r"\S{%d,}" % (_MAX_RUN + 1), lambda m: m.group(0)[:_MAX_RUN], text)
