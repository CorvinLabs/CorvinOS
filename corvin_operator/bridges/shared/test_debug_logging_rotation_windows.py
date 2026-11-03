"""A rotation the OS refuses (file open in another process on Windows) must not break logging."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import debug_logging as dl  # noqa: E402


def test_rotation_failure_keeps_logging(tmp_path, monkeypatch):
    log = tmp_path / "corvin.log"
    h = dl._TolerantRotatingFileHandler(log, maxBytes=50, backupCount=2, encoding="utf-8")
    lg = logging.getLogger("rot_test")
    lg.propagate = False
    lg.addHandler(h)
    lg.setLevel(logging.INFO)

    def boom(src, dst):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(h, "rotate", boom)
    errors = []
    h.handleError = lambda record: errors.append(record)
    for i in range(10):
        lg.info("line %d xxxxxxxxxxxxxxxxxxxx", i)
    h.close()
    lg.removeHandler(h)
    assert errors == []
    assert log.read_text(encoding="utf-8").count("line") == 10
