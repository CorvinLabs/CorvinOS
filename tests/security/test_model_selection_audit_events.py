"""The automatic OS-model tiers' audit records keep their fields (review DLG-16).

``model_selector`` emits ``bridge.workload_model_selection`` (Tier 2.7) and
``bridge.ato_model_selection`` (Tier 2.8) through the bridge's ``audit_event``.
Until 2026-11-05 neither name had an ``EVENT_SEVERITY`` or ``_EVENT_ALLOWLIST``
entry, so the floor dropped ``selected_model`` / ``tier`` / ``confidence`` — the
record said a routing decision happened and not which one.

Driven through the real chain writer in a fresh interpreter with an isolated
``CORVIN_HOME`` (never the live install).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(code: str, home: Path) -> dict:
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(home / "anchor.key")
    env["XDG_CONFIG_HOME"] = str(home / "xdg")
    for k in ("VOICE_AUDIT_PATH", "FORGE_ROOT", "CORVIN_TENANT_ID",
              "CORVIN_OS_MODEL_AUTOSELECT", "CORVIN_OS_MODEL_OVERRIDE"):
        env.pop(k, None)
    pre = textwrap.dedent(f"""
        import sys, json
        from pathlib import Path
        R = Path({str(REPO)!r})
        sys.path.insert(0, str(R))
        sys.path.insert(0, str(R / "corvin_operator" / "forge"))
        sys.path.insert(0, str(R / "corvin_operator" / "bridges" / "shared"))
        OUT = {{}}
    """)
    proc = subprocess.run(
        [sys.executable, "-c", pre + textwrap.dedent(code) + "\nprint('@@' + json.dumps(OUT))"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    return json.loads([l for l in proc.stdout.splitlines() if l.startswith("@@")][-1][2:])


def test_ato_and_workload_selection_records_keep_their_fields(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run("""
        import audit as bridge_audit
        import model_selector as ms
        from forge.security_events import EVENT_SEVERITY, _EVENT_ALLOWLIST
        OUT["registered"] = {n: (n in EVENT_SEVERITY, n in _EVENT_ALLOWLIST)
                             for n in ("bridge.workload_model_selection",
                                       "bridge.ato_model_selection")}
        model, source = ms.resolve_os_model_with_source(
            {"name": "coder"}, chat_key="c1", audit_fn=bridge_audit.audit_event,
            ato_plan_hint={"recommended_model": "haiku", "confidence": 0.8,
                           "task_type": "chat"})
        OUT["source"] = source
        recs = [json.loads(l) for l in Path(bridge_audit.audit_path()).read_text().splitlines()]
        OUT["ato"] = [r["details"] for r in recs
                      if r.get("event_type") == "bridge.ato_model_selection"]
        # Tier 2.7 emits only with fast_chat_mode on; drive its emitter call
        # shape through the same writer.
        bridge_audit.audit_event(
            "bridge.workload_model_selection", chat_key="c1",
            details={"workload_type": "chat", "confidence": 0.9,
                     "selected_model": model, "engine": "claude_code",
                     "tier": "2.7_workload"})
        recs = [json.loads(l) for l in Path(bridge_audit.audit_path()).read_text().splitlines()]
        OUT["workload"] = [r["details"] for r in recs
                           if r.get("event_type") == "bridge.workload_model_selection"]
    """, home)
    assert out["registered"] == {
        "bridge.workload_model_selection": [True, True],
        "bridge.ato_model_selection": [True, True],
    }, out
    assert out["source"] == "ato", out
    (ato,) = out["ato"]
    assert "_dropped_fields" not in ato, ato
    assert ato["tier"] == "2.8_ato" and ato["recommended_model"] == "haiku", ato
    assert ato["selected_model"] and ato["confidence"] == 0.8, ato
    (wl,) = out["workload"]
    assert "_dropped_fields" not in wl, wl
    assert wl["tier"] == "2.7_workload" and wl["workload_type"] == "chat", wl
