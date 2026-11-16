"""Every bridge daemon supplies chat_type the way ADR-2326 item 6 maps it (AC-O18).

Source contract plus a real Node run of the Discord helper. A daemon without the field is denied
upstream (fail-closed), which is the intended state for WhatsApp and email.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

BR = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges"


@pytest.mark.parametrize("path,needle", [
    ("discord/daemon.js", "chat_type: chatTypeOfInteraction(interaction)"),
    ("discord/daemon.js", "chat_type: chatTypeOfChannel(msg.channel)"),
    ("telegram/daemon.js", "msg.chat.type === 'private'"),
    ("slack/daemon.js", "event.channel_type === 'im'"),
    ("teams/handler.js", "convType === 'personal'"),
    ("teams/handler.js", "sender_stable_id: activity.from?.aadObjectId"),
    ("signal/handler.js", "CORVIN_SIGNAL_DEDICATED_NUMBER === '1'"),
])
def test_daemon_supplies_chat_type(path, needle):
    assert needle in (BR / path).read_text(encoding="utf-8")


def test_every_text_envelope_base_carries_chat_type():
    for path in ("discord/daemon.js", "telegram/daemon.js", "slack/daemon.js"):
        src = (BR / path).read_text(encoding="utf-8")
        bases = re.findall(r"const base\s*=\s*\{[^}]*\}", src)
        assert bases and all("chat_type" in b for b in bases), path


@pytest.mark.parametrize("path", ["whatsapp/daemon.js", "email/daemon.js"])
def test_never_private_daemons_do_not_claim_private(path):
    src = (BR / path).read_text(encoding="utf-8")
    assert "chat_type" not in src  # no field at all: the adapter treats the chat as not private


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_discord_helper_maps_channel_types():
    src = (BR / "discord" / "daemon.js").read_text(encoding="utf-8")
    fns = re.search(r"function chatTypeOfChannel[\s\S]*?\n}\nfunction chatTypeOfInteraction[\s\S]*?\n}\n", src).group(0)
    script = fns + """
const out = [chatTypeOfChannel({type: 1}), chatTypeOfChannel({type: 'DM'}), chatTypeOfChannel({type: 3}),
  chatTypeOfChannel({type: 0}), chatTypeOfChannel(null), chatTypeOfInteraction({guildId: '1', channel: {type: 1}}),
  chatTypeOfInteraction({guildId: null, channel: {type: 1}}), chatTypeOfInteraction({guildId: null, channel: null})];
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '["private","private","group","group","unknown","group","private","unknown"]'
