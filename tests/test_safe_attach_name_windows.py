"""Windows device names / trailing dots in attachment names (platform-gated: POSIX unchanged)."""
from corvin_console.attachments_common import safe_attach_name


def test_windows_reserved_and_trailing():
    for raw in ("nul", "NUL.txt", "con", "COM1.log", "lpt9", "aux.tar.gz", "CON .txt"):
        out = safe_attach_name(raw, windows=True)
        assert out.startswith("_"), (raw, out)
    assert safe_attach_name("report.pdf.", windows=True) == "report.pdf"
    assert safe_attach_name("...", windows=True) == "file"
    assert safe_attach_name("console.txt", windows=True) == "console.txt"


def test_posix_unchanged():
    assert safe_attach_name("nul.txt", windows=False) == "nul.txt"
    assert safe_attach_name("a b.", windows=False) == "a b."


def test_windows_long_name_truncated_keeps_extension_and_is_idempotent():
    raw = ("Sehr langer Dokumenttitel " * 12) + ".pdf"
    out = safe_attach_name(raw, windows=True)
    assert len(out) <= 100 and out.endswith(".pdf")
    assert safe_attach_name(out, windows=True) == out
    assert safe_attach_name(raw, windows=False) == raw.strip()
