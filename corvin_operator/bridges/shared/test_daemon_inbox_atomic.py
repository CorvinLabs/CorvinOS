"""Every bridge daemon writes inbox envelopes through shared/js/inbox_write.js
(tmp + rename, mode 0o600). Review SRF-9: Slack wrote them in place at umask, so the
adapter's 1 Hz poll could read a half-written envelope and quarantine the message."""
from __future__ import annotations

import re
from pathlib import Path

_BRIDGES = Path(__file__).resolve().parents[1]


def test_every_daemon_writes_its_inbox_atomically():
    daemons = sorted(_BRIDGES.glob("*/daemon.js"))
    assert len(daemons) >= 5, "positive control: the scan must reach the daemons"
    offenders = [d.parent.name for d in daemons
                 if re.search(r"writeFileSync\(\s*path\.join\(\s*INBOX", d.read_text(encoding="utf-8"))]
    assert offenders == [], f"non-atomic, umask-mode inbox writes in: {offenders}"
    slack = (_BRIDGES / "slack" / "daemon.js").read_text(encoding="utf-8")
    assert "writeInboxAtomic(INBOX" in slack
