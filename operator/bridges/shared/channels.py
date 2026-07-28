"""The shipped messenger channels — one list, read by everything that enumerates them.

Why this module exists
----------------------
The channel list was copy-pasted into at least six places and three of them had
gone stale in the same direction: ``signal`` and ``teams`` ship complete daemons
(``operator/bridges/{signal,teams}/daemon.js``), are startable through
``bridge_manager``, and are configurable from the Console — yet

* ``session_reset.VALID_CHANNELS`` listed five, so ``/new`` and ``/reset``
  died with ``argparse: invalid choice: 'signal'`` on both bridges. The user saw
  "session reset failed" and the session was never actually reset (reproduced
  2026-07-28);
* ``settings_view._BRIDGE_CHANNELS`` listed five, so ``/settings`` never showed
  a Signal or Teams operator whether their own bridge was configured — and its
  comment said "four-channel set", stale twice over;
* ``bridges_migrate._CHANNELS`` listed five, so neither channel's legacy state
  was ever migrated.

``bridge_manager._CHANNELS`` already carried the correct seven plus a comment
recording the *previous* incarnation of exactly this bug ("signal + teams were
long console-configurable but absent here — the console saved their settings and
then NOTHING could ever start the daemons"). That comment is the reason this
module exists rather than a seventh corrected copy: the list is load-bearing in
enough places that the next channel added would miss some of them again.

Adding a channel
----------------
Append it here. ``core/plugins/corvin_plugins/bridges/supervisor.py`` keeps its
own ``BRIDGE_CHANNELS`` because it lives in a different distribution package
(``corvin_plugins``) that must import cleanly without ``operator/`` on the path;
``operator/bridges/tests/test_channel_list_ssot.py`` pins the two together, so a
divergence is a red test rather than a silent half-wiring.
"""
from __future__ import annotations

#: Every channel with a shipped daemon under ``operator/bridges/<channel>/``.
#: Order is stable and is the order surfaces render them in.
BRIDGE_CHANNELS: tuple[str, ...] = (
    "whatsapp",
    "telegram",
    "discord",
    "slack",
    "email",
    "signal",
    "teams",
)

#: Short labels for compact one-line renderings (``/settings``, ``/status``).
CHANNEL_LABELS: dict[str, str] = {
    "whatsapp": "WA",
    "telegram": "TG",
    "discord": "Discord",
    "slack": "Slack",
    "email": "Mail",
    "signal": "Signal",
    "teams": "Teams",
}

__all__ = ["BRIDGE_CHANNELS", "CHANNEL_LABELS"]
