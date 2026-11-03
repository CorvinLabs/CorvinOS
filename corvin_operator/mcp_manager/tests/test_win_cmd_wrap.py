"""npx-based MCP servers must be launched via ``cmd /c`` on native Windows."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_manager import activate


def test_npx_wrapped_on_windows():
    cfg = {"command": "npx", "args": ["-y", "pkg"], "env": {"A": "b"}}
    with mock.patch("sys.platform", "win32"):
        out = activate._win_cmd_wrap(cfg)
    assert out["command"] == "cmd"
    assert out["args"] == ["/c", "npx", "-y", "pkg"]
    assert out["env"] == {"A": "b"}
    assert cfg["command"] == "npx"  # input untouched


def test_other_commands_and_platforms_untouched():
    cfg = {"command": "node", "args": ["x.js"]}
    with mock.patch("sys.platform", "win32"):
        assert activate._win_cmd_wrap(cfg) is cfg
    cfg2 = {"command": "npx", "args": []}
    with mock.patch("sys.platform", "linux"):
        assert activate._win_cmd_wrap(cfg2) is cfg2
