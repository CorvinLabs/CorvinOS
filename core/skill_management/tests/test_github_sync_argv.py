"""D-17 — GitClient: URL scheme allowlist + ``--`` before positionals."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.skill_management.github_sync import GitClient, _validate_repo_url


@pytest.mark.parametrize("url", [
    "https://github.com/org/repo.git",
    "https://github.com/org/repo",
    "git@github.com:org/repo.git",
    "ssh://git@github.com/org/repo.git",
])
def test_accepted_urls(url):
    assert _validate_repo_url(url) == url


@pytest.mark.parametrize("url", [
    "--upload-pack=touch /tmp/pwned",
    "-c core.sshCommand=evil",
    "ext::sh -c 'touch /tmp/pwned'",
    "file:///etc/passwd",
    "/etc/passwd",
    "http://github.com/org/repo",
    "",
])
def test_rejected_urls(url):
    with pytest.raises(ValueError):
        _validate_repo_url(url)


def test_client_rejects_option_shaped_url_and_branch():
    with pytest.raises(ValueError):
        GitClient("--upload-pack=x", tenant_id="t1")
    with pytest.raises(ValueError):
        GitClient("https://github.com/org/repo.git", branch="--exec=x", tenant_id="t1")


def test_clone_argv_has_double_dash_before_positionals(tmp_path):
    client = GitClient("https://github.com/org/repo.git", branch="main", tenant_id="t1")
    client.local_repo_path = tmp_path / "missing"
    with patch("core.skill_management.github_sync.subprocess.run") as run:
        assert client.clone_or_pull() is True
        argv = run.call_args[0][0]
    assert argv[:4] == ["git", "clone", "--branch", "main"]
    assert "--" in argv
    dd = argv.index("--")
    assert argv[dd + 1] == "https://github.com/org/repo.git"
    assert argv[dd + 2] == str(client.local_repo_path)
