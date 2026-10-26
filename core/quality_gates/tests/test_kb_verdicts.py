"""kb_verdicts: severity comes from the checkout's own RULES table, read without executing it."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gates.kb_verdicts import KbCheckOutputError, findings_by_file, rule_severities, verdict_for
from core.quality_gates.models import VerdictType

KB_PY = 'import os\nRULES = {\n    "E07_status_invalid": ("error", True),\n    "G1_no_concept": ("warning", False),\n}\n'


def _kb(tmp_path: Path, src: str = KB_PY) -> Path:
    p = tmp_path / "kb.py"
    p.write_text(src)
    return p


def test_the_table_is_read_as_a_literal(tmp_path):
    assert rule_severities(_kb(tmp_path)) == {"E07_status_invalid": "error", "G1_no_concept": "warning"}


def test_reading_the_table_never_executes_the_file(tmp_path):
    marker = tmp_path / "ran"
    rule_severities(_kb(tmp_path, f"open({str(marker)!r}, 'w').write('x')\n" + KB_PY))
    assert not marker.exists()


@pytest.mark.parametrize("src", ["RULES = dict(a=('error', True))\n", "X = 1\n",
                                 'RULES = {"E1": ("fatal", True)}\n'])
def test_a_table_that_cannot_be_trusted_refuses(tmp_path, src):
    with pytest.raises(KbCheckOutputError):
        rule_severities(_kb(tmp_path, src))


def test_a_reported_rule_the_table_does_not_define_refuses(tmp_path):
    sev = rule_severities(_kb(tmp_path))
    with pytest.raises(KbCheckOutputError, match="does not define"):
        findings_by_file({"issues": {"E99_new_rule": [{"file": str(tmp_path / "a.md"), "detail": ""}]}}, sev)


def test_error_fails_warning_warns_nothing_passes(tmp_path):
    sev = rule_severities(_kb(tmp_path))
    a, b, c = (tmp_path / n for n in ("a.md", "b.md", "c.md"))
    check = {"issues": {"E07_status_invalid": [{"file": str(a), "detail": "x"}],
                        "G1_no_concept": [{"file": str(a), "detail": "y"}, {"file": str(b), "detail": "z"}]}}
    by_file = findings_by_file(check, sev)
    v = {f.name: verdict_for("ADRGate", f.stem, f, by_file, "t") for f in (a, b, c)}
    assert v["a.md"].verdict is VerdictType.FAIL and v["a.md"].reason == "kb check: 1 error(s), 1 warning(s)"
    assert v["b.md"].verdict is VerdictType.WARN and v["b.md"].findings == ["G1_no_concept: z"]
    assert v["c.md"].verdict is VerdictType.PASS and v["c.md"].reason == "kb check: no finding"
