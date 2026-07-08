#!/usr/bin/env python3
"""test_completion_e2e.py — END-TO-END proof of notify-on-background-completion.

Chains the ENTIRE path a real background completion travels, with no live
engine, proving the user-visible claim "I'll notify you when the background task
is done" actually reaches the messenger:

    completion_notify.register(origin channel + chat_id)      # at task start
        → mark_done(task_id, result)                          # at completion
        → deliver_ready(shared outbox)                        # adapter/bg_monitor poller
        → signal daemon processOutboxPayload(envelope)        # REAL daemon handler
        → sendSignal(recipient, text)                         # messenger send (faked)

Stage boundaries are real files (the shared outbox) and the REAL signal
handler module — only the final network send is faked, exactly as the bridge's
own daemon test does. If this is green, a completed background task provably
produces a delivered messenger notification.

Run: python3 operator/bridges/shared/test_completion_e2e.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIGNAL_HANDLER = HERE.parent / "signal" / "handler.js"

_NODE_DRIVER = r"""
const path = require('path');
const { makeHandler } = require(process.argv[2]);
const envelope = JSON.parse(process.argv[3]);
const sent = [];
const handler = makeHandler({
  inboxDir: '/tmp', settingsFile: '/tmp/none.json',
  currentSettings: () => ({}), auth: { isAllowed: () => true },
  logger: null,
  sendSignal: async (recipient, message) => { sent.push({ recipient, message }); },
});
(async () => {
  await handler.processOutboxPayload(envelope);
  process.stdout.write(JSON.stringify(sent));
})().catch(e => { process.stderr.write(String(e)); process.exit(2); });
"""


def main() -> int:
    if not SIGNAL_HANDLER.exists():
        print(f"SKIP: signal handler not found at {SIGNAL_HANDLER}")
        return 0
    node = None
    for cand in ("node", "/usr/bin/node", "/usr/local/bin/node"):
        from shutil import which
        if which(cand) or os.path.isfile(cand):
            node = cand
            break
    if node is None:
        print("SKIP: node not available")
        return 0

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        outbox = Path(td) / "outbox"
        os.environ["CORVIN_HOME"] = str(home)
        sys.path.insert(0, str(HERE))
        for m in list(sys.modules):
            if m == "completion_notify":
                del sys.modules[m]
        import completion_notify as cn  # type: ignore

        # Stage 1+2 — a signal-origin background task registers + completes.
        recipient = "+4915112345678"
        tid = cn.register(channel="signal", chat_id=recipient,
                          sender=recipient, label="nightly backtest")
        assert cn.mark_done(tid, text="Sharpe 1.9 — report attached.", ok=True)

        # Stage 3 — the poller delivers it into the shared outbox.
        n = cn.deliver_ready(outbox)
        assert n == 1, f"deliver_ready produced {n} envelopes, expected 1"
        env_file = next(outbox.glob("cn_*.json"))
        envelope = json.loads(env_file.read_text())
        assert envelope["channel"] == "signal"
        assert envelope["chat_id"] == recipient  # signal chat_id stays a string

        # Stage 4+5 — the REAL signal handler sends it (send faked).
        driver = Path(td) / "driver.js"
        driver.write_text(_NODE_DRIVER)
        res = subprocess.run(
            [node, str(driver), str(SIGNAL_HANDLER), json.dumps(envelope)],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            print(f"FAIL: node driver error: {res.stderr}")
            return 1
        sent = json.loads(res.stdout or "[]")
        assert len(sent) == 1, f"expected 1 send, got {sent}"
        assert sent[0]["recipient"] == recipient, sent
        assert "Sharpe 1.9" in sent[0]["message"], sent
        assert "nightly backtest finished" in sent[0]["message"], sent

    print("PASS: E2E — background completion → outbox → signal handler → "
          f"sendSignal({recipient}, ✅ …)")
    print("\nALL E2E CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
