"""Standard-plugin list (corvin_console/default_plugins.yaml): shape and refusal rules.

The behaviour on a real boot (install, consent, once-per-tenant ledger, restart) is proven
end-to-end by web-next/tests/e2e-fresh/default-plugins.spec.ts; this file pins the list itself.
"""
from pathlib import Path

import pytest

from corvin_console import default_plugins as dp


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "list.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_shipped_list_is_valid_and_starts_with_video_producer():
    entries = dp.load_list()
    assert entries, "the shipped list must not be empty"
    first = entries[0]
    assert first["index_id"] == "plugin:contributor-media-video_producer"
    assert first["registry_id"] == "video_producer"
    assert first["min_version"] == "1.2.0"


def test_no_duplicates_in_shipped_list():
    ids = [e["index_id"] for e in dp.load_list()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("body", [
    "plugins: []\n",                                                   # no schema
    "schema: 2\nplugins: []\n",                                        # wrong schema
    "schema: 1\nplugins:\n  - index_id: ../../etc/passwd\n",           # not an index id
    "schema: 1\nplugins:\n  - index_id: plugin:contributor-media\n",  # truncated id
    "schema: 1\nplugins:\n  - index_id: plugin:contributor-media-a_b\n  - index_id: plugin:contributor-media-a_b\n",
    "schema: 1\nplugins:\n  - just-a-string\n",
])
def test_malformed_list_is_refused_not_partially_installed(tmp_path, body):
    with pytest.raises(dp.DefaultPluginsError):
        dp.load_list(_write(tmp_path, body))


def test_start_is_inert_under_pytest():
    assert dp.start("_default") is False
