"""Seed one chat through the REAL turn writer (chat_runtime._append_turn) into the CORVIN_HOME of the
environment, then print its sid. Used by chat-efficiency.spec.ts (ADR-2324)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[7]
sys.path[:0] = [str(_REPO), str(_REPO / "core/console"),
                str(_REPO / "corvin_operator/bridges/shared"), str(_REPO / "corvin_operator/forge")]
from corvin_console import chat_runtime as cr

s = cr.create_session("_default", title="Efficiency demo")


def u(i, o, cr_, cw):
    return {"input_tokens": i, "output_tokens": o,
            "cache_read_input_tokens": cr_, "cache_creation_input_tokens": cw}


def diff(n_add, n_rm):
    return "@@ -1,%d +1,%d @@\n%s%s" % (n_rm, n_add, "".join("-old%d\n" % i for i in range(n_rm)),
                                       "\n".join("+new%d" % i for i in range(n_add)))


cr._append_turn(s, "user", [{"kind": "text", "text": "Refactor the parser"}])
cr._append_turn(s, "assistant", [{"kind": "text", "text": "Looking at the code."}], cli_spawned=True,
                turn_usage={"model": "claude-sonnet-5", "routed_model": "claude-sonnet-5", "usage": u(1200, 900, 0, 18000)})
cr._append_turn(s, "user", [{"kind": "text", "text": "go"}])
cr._append_turn(s, "assistant", [{"kind": "text", "text": "Done."},
                {"kind": "tool", "name": "Edit", "id": "a", "diff": diff(42, 7), "diff_truncated": False}],
                cli_spawned=True,
                turn_usage={"model": "claude-sonnet-5", "routed_model": "claude-sonnet-5", "usage": u(300, 4200, 86000, 2000)})
cr._append_turn(s, "user", [{"kind": "text", "text": "more"}])
cr._append_turn(s, "assistant", [{"kind": "text", "text": "Also done."},
                {"kind": "tool", "name": "Write", "id": "b", "diff": diff(120, 0), "diff_truncated": True}],
                cli_spawned=True,
                turn_usage={"model": "claude-opus-5", "routed_model": "claude-opus-5", "usage": u(400, 9000, 120000, 1500)})
cr._append_turn(s, "user", [{"kind": "text", "text": "and"}])
cr._append_turn(s, "assistant", [{"kind": "text", "text": "Unknown model turn."}], cli_spawned=True,
                turn_usage={"model": "mystery-model-9", "routed_model": "mystery-model-9", "usage": u(10, 100, 0, 0)})
cr._append_turn(s, "assistant", [{"kind": "text", "text": "No usage reported."}], cli_spawned=True)
print(s.sid)
