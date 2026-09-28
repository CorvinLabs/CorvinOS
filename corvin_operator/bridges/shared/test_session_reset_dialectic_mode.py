"""A session reset never spawns a `claude -p` dialectic judge.

``session_reset._dialectic_session_reset`` records an audit-trail entry for a
high-value session about to be reset. Its Decision is ignored — the reset runs
regardless — so the site's bundle default (``cli``: a synchronous ``claude -p``
subprocess, up to 15 s) was pure latency on every reset, including the
``corvin-session-timeout`` timer, once the tenant_id fix let the heat score
cross the threshold. The call site pins ``mode="fast"``: the decision is still
audited, no LLM runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dialectic  # noqa: E402
import session_reset  # noqa: E402


def test_high_heat_reset_runs_no_llm_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    forge_chan_id = session_reset.forge_channel_id("discord", "c1")
    root = session_reset._sessions_root("_default") / forge_chan_id
    # 5 skills: heat = 0.4*0.8 + 0.3*0.4 + 0.3*(3/5) = 0.62 > 0.5 on a timeout
    for i in range(5):
        (root / "skill-forge" / "skills" / f"s{i}").mkdir(parents=True)
    assert str(root).startswith(str(tmp_path)), root  # positive control: sandboxed

    spawned, decisions = [], []
    monkeypatch.setattr(dialectic, "_run_cli_judge",
                        lambda **kw: spawned.append(kw) or "A | x | y")
    monkeypatch.setattr(dialectic.subprocess, "run",
                        lambda *a, **kw: spawned.append(a) or None)
    real_decide = dialectic.decide

    def _spy(**kw):
        d = real_decide(**kw)
        decisions.append(d)
        return d
    monkeypatch.setattr(dialectic, "decide", _spy)

    session_reset._dialectic_session_reset(
        channel="discord", chat_id="c1", forge_chan_id=forge_chan_id,
        reason="timeout", tenant_id="_default")

    [d] = decisions
    # positive control: the probe saw the skills and the gate was crossed —
    # otherwise "no spawn" would hold vacuously below the threshold
    assert d.heat >= dialectic.resolve_threshold(site="session_reset"), d
    assert d.thesis["n_skills"] == 5, d.thesis
    assert not spawned, f"a reset spawned an LLM judge: {spawned!r}"
    assert d.mode == "fast", d
