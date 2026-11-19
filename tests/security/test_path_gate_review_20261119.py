"""Review 2026-11-19, SEC-2 / SEC-3 - path_gate regression vectors.

Every vector runs the REAL hook as a subprocess (the way Claude Code runs it):
the payload goes in on stdin, exit status 2 is a deny, 0 an allow. Nothing is
imported from the gate, so a vector cannot pass because of in-process state.

SEC-2: the in-place writers the write-target extraction did not know
(`sed --in-place`, `sed -Ei`, `perl -pi`, `gzip`, `gawk -i inplace`, `patch`,
`shuf -o`, `sponge`, `vim -c`, `git -C <dir> clean`, `sqlite3`), and a `cd`
into a protected runtime tree followed by anything but a read.

SEC-3: the gate's own sources, the audit writer, the three path mirrors, the
boot tripwire, the anchor key and its out-of-tree marker trees, the hook
registration (`~/.claude/settings*.json`) and the house-rules policy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "corvin_operator" / "voice" / "hooks" / "path_gate.py"


@pytest.fixture()
def box(tmp_path):
    """A sandbox install: runtime root, user home, key dir - all under tmp."""
    home = tmp_path / "rt" / ".corvin"
    forge = home / "tenants" / "_default" / "global" / "forge"
    forge.mkdir(parents=True)
    chain = forge / "audit.jsonl"
    chain.write_text("", encoding="utf-8")
    (home / "tenants" / "_default" / "memory").mkdir(parents=True)
    user = tmp_path / "user"
    (user / ".claude").mkdir(parents=True)
    keydir = tmp_path / "keys"
    keydir.mkdir()
    key = keydir / "audit_anchor.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(user),
        "USERPROFILE": str(user),
        "CORVIN_HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(key),
    }
    work = tmp_path / "work"
    work.mkdir()

    class Box:
        pass

    b = Box()
    b.home, b.chain, b.forge, b.user, b.keydir, b.key = home, chain, forge, user, keydir, key
    b.env, b.work, b.tmp = env, work, tmp_path
    return b


def _run(box, tool: str, tool_input: dict) -> int:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        capture_output=True, text=True, env=box.env, cwd=str(box.work), timeout=60,
    )
    assert proc.returncode in (0, 2), (proc.returncode, proc.stderr[-2000:])
    return proc.returncode


def _bash(box, cmd: str) -> int:
    return _run(box, "Bash", {"command": cmd})


def _src(*rel: str) -> str:
    return str(REPO.joinpath(*rel))


# --------------------------------------------------------------------- SEC-2

def _sec2_deny_vectors(b) -> dict[str, str]:
    c = str(b.chain)
    policy = str(b.forge / "policy.json")
    recall = str(b.home / "tenants" / "_default" / "memory" / "recall.db")
    gate = _src("corvin_operator", "voice", "hooks", "path_gate.py")
    events = _src("corvin_operator", "forge", "forge", "security_events.py")
    return {
        "sed-long-in-place": f"sed --in-place 's/a/b/' {c}",
        "sed-long-in-place-suffix": f"sed --in-place=.bak 's/a/b/' {policy}",
        "sed-cluster-Ei": f"sed -Ei 's/a/b/' {c}",
        "sed-cluster-ni-policy": f"sed -ni '1p' {policy}",
        "sed-two-files": f"sed -i 's/a/b/' {policy} /tmp/other.txt",
        "perl-pi": f"perl -pi -e 's/a/b/' {c}",
        "perl-i-bak": f"perl -i.bak -pe 's/a/b/' {policy}",
        "ruby-i": f"ruby -i -pe 'gsub(/a/, \"b\")' {policy}",
        "gzip": f"gzip {c}",
        "xz-policy": f"xz {policy}",
        "gawk-inplace": f"gawk -i inplace '{{print}}' {c}",
        "gawk-inplace-policy": f"gawk -i inplace '{{print}}' {policy}",
        "patch": f"patch {c} /tmp/x.diff",
        "patch-policy": f"patch {policy} /tmp/x.diff",
        "shuf-o": f"shuf -o {c} /tmp/in.txt",
        "shuf-o-policy": f"shuf /tmp/in.txt -o {policy}",
        "sponge": f"cat /tmp/in.txt | sponge {c}",
        "sponge-policy": f"cat /tmp/in.txt | sponge {policy}",
        "vim-c": f"vim -c '%d' -c 'wq' {c}",
        "vim-c-policy": f"vim -c '%d' -c 'wq' {policy}",
        "git-C-clean": f"git -C {b.home.parent} clean -fdx",
        "git-C-clean-glued": f"git -C{b.home.parent} clean -xfd",
        "git-clean-inside-home": f"git -C {b.home} clean -fd",
        "sqlite3-delete": f"sqlite3 {recall} 'delete from turns'",
        "opaque-program-on-chain": f"python3 /tmp/tool.py {c}",
        "cd-forge-then-run": f"cd {b.forge} && python3 /tmp/tool.py",
        "cd-forge-then-sed": f"cd {b.forge} && sed -i 's/a/b/' audit.jsonl",
        "cd-keydir-then-sed-key": f"cd {b.keydir} && sed -i 's/a/b/' audit_anchor.key",
        "cd-keydir-then-rm-marker": f"cd {b.keydir} && sed -i 's/a/b/' chain_ids/abc",
        "sed-in-place-gate-source": f"sed --in-place 's/return 2/return 0/' {gate}",
        "perl-pi-audit-writer": f"perl -pi -e 's/CRITICAL/INFO/' {events}",
        "git-checkout-gate-source": f"git -C {REPO} checkout -- corvin_operator/voice/hooks/path_gate.py",
        "git-rm-gate-source": f"git -C {REPO} rm corvin_operator/voice/hooks/hooks.json",
        "redirect-gate-source": f"echo x > {gate}",
        "rm-chain-id": f"rm {b.keydir}/chain_ids/abc",
        "rm-anchor-key": f"rm -f {b.key}",
        "truncate-tail-anchor": f"truncate -s0 {b.keydir}/chain_tails/g-abc",
    }


def test_sec2_in_place_writers_are_denied(box):
    allowed = [label for label, cmd in _sec2_deny_vectors(box).items() if _bash(box, cmd) != 2]
    assert not allowed, f"allowed although they write a protected path: {allowed}"


def _sec2_allow_vectors(b) -> dict[str, str]:
    c = str(b.chain)
    gate = _src("corvin_operator", "voice", "hooks", "path_gate.py")
    events = _src("corvin_operator", "forge", "forge", "security_events.py")
    hooks_dir = _src("corvin_operator", "voice", "hooks")
    other = b.tmp / "elsewhere"
    return {
        "cat-chain": f"cat {c}",
        "grep-chain": f"grep -c event {c}",
        "tail-jq-chain": f"tail -n 5 {c} | jq -c .event",
        "sha256-chain": f"sha256sum {c}",
        "sed-print-gate-source": f"sed -n '1,5p' {gate}",
        "sed-in-place-ordinary-file": f"sed -i 's/a/b/' {b.work}/notes.txt",
        "perl-pi-ordinary-file": f"perl -pi -e 's/a/b/' {b.work}/notes.txt",
        "gzip-ordinary-file": f"gzip {b.work}/notes.txt",
        "pytest-on-gate-tests": f"python3 -m pytest {hooks_dir}/test_path_gate.py -q",
        "cd-hooks-dir-pytest": f"cd {hooks_dir} && python3 -m pytest test_path_gate.py -q",
        "git-add-gate-source": f"git -C {REPO} add corvin_operator/voice/hooks/path_gate.py",
        "git-diff-audit-writer": f"git -C {REPO} diff -- {events}",
        "git-clean-dry-run": f"git -C {b.home.parent} clean -ndx",
        "git-clean-unrelated-repo": f"git -C {other} clean -fdx",
        "git-checkout-branch": f"git -C {REPO} checkout main",
        "sqlite3-ordinary-db": f"sqlite3 {b.work}/x.db 'select 1'",
        "vim-ordinary-file": f"vim -c 'wq' {b.work}/notes.txt",
    }


def test_sec2_reads_and_unrelated_writes_stay_allowed(box):
    denied = [label for label, cmd in _sec2_allow_vectors(box).items() if _bash(box, cmd) != 0]
    assert not denied, f"over-blocked: {denied}"


# --------------------------------------------------------------------- SEC-3

def _sec3_protected_files(b) -> dict[str, str]:
    return {
        "gate-itself": _src("corvin_operator", "voice", "hooks", "path_gate.py"),
        "hooks-json": _src("corvin_operator", "voice", "hooks", "hooks.json"),
        "hook-launcher": _src("corvin_operator", "voice", "hooks", "with_python.sh"),
        "sibling-gate": _src("corvin_operator", "voice", "hooks", "auth_elevation_gate.py"),
        "new-file-in-hooks": _src("corvin_operator", "voice", "hooks", "sitecustomize.py"),
        "audit-writer": _src("corvin_operator", "forge", "forge", "security_events.py"),
        "paths-mirror-forge": _src("corvin_operator", "forge", "forge", "paths.py"),
        "paths-mirror-bridges": _src("corvin_operator", "bridges", "shared", "paths.py"),
        "paths-mirror-core": _src("core", "paths", "tenant.py"),
        "tripwire": _src("core", "compliance", "corvin_compliance_reports", "tripwire.py"),
        "house-rules-policy": _src("corvin_operator", "policy", "house_rules.yaml"),
        "house-rules-gate": _src("corvin_operator", "bridges", "shared", "house_rules.py"),
        "anchor-key": str(b.key),
        "mac-sentinel": str(b.keydir / "audit_mac_active"),
        "chain-id-record": str(b.keydir / "chain_ids" / "0123abcd"),
        "chain-tail-anchor": str(b.keydir / "chain_tails" / "g-0123abcd"),
        "mac-marker": str(b.keydir / "mac_active_chains" / "g-0123abcd"),
        "claude-settings": str(b.user / ".claude" / "settings.json"),
        "claude-settings-local": str(b.user / ".claude" / "settings.local.json"),
        "chain": str(b.chain),
        "chain-rotation-sibling": str(b.chain) + ".1",
    }


@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "file_path"),
                                      ("NotebookEdit", "notebook_path")])
def test_sec3_write_tools_denied_on_gate_sources_and_anchors(box, tool, key):
    allowed = [label for label, path in _sec3_protected_files(box).items()
               if _run(box, tool, {key: path, "content": "x", "old_string": "a", "new_string": "b"}) != 2]
    assert not allowed, f"{tool} allowed on: {allowed}"


def test_sec3_bash_redirect_and_delete_denied(box):
    allowed = []
    for label, path in _sec3_protected_files(box).items():
        for verb, cmd in (("redirect", f"echo x >> {path}"), ("rm", f"rm -f {path}"),
                          ("sed", f"sed -i 's/a/b/' {path}"), ("cp-over", f"cp /tmp/evil {path}")):
            if _bash(box, cmd) != 2:
                allowed.append(f"{label}:{verb}")
    assert not allowed, f"allowed: {allowed}"


def test_sec3_default_key_dir_is_protected_without_the_env_override(box):
    """With no CORVIN_AUDIT_ANCHOR_KEY the key lives in ~/.config/corvin-voice."""
    env = dict(box.env)
    del env["CORVIN_AUDIT_ANCHOR_KEY"]
    del env["XDG_CONFIG_HOME"]
    box.env = env
    base = box.user / ".config" / "corvin-voice"
    for rel in ("audit_anchor.key", "audit_mac_active", "chain_ids/ab", "chain_tails/g-ab",
                "mac_active_chains/g-ab"):
        assert _run(box, "Write", {"file_path": str(base / rel), "content": "x"}) == 2, rel
        assert _bash(box, f"rm -f {base / rel}") == 2, rel
    # A file in the same directory that is not part of the chain's integrity.
    assert _run(box, "Write", {"file_path": str(base / "notes.txt"), "content": "x"}) == 0


def test_sec3_ordinary_files_stay_writable(box):
    for label, path in {
        "repo-doc": _src("docs", "claude-ref", "some-note.md"),
        "repo-test": _src("tests", "security", "test_new_thing.py"),
        "repo-module": _src("corvin_operator", "bridges", "shared", "adapter_helpers_new.py"),
        "claude-other-json": str(box.user / ".claude" / "keybindings.json"),
        # Round 2 (R2-SEC-3): project settings are NOT ordinary any more - see
        # test_r2_sec3_project_settings_plugins_and_ancestors below.
        "project-claude-note": str(box.work / ".claude" / "notes.md"),
        "claude-project-memory": str(box.user / ".claude" / "projects" / "p" / "memory.md"),
        "workdir-file": str(box.work / "audit_anchor.key"),
        "tmp-chain-dir-name": str(box.work / "chain_ids" / "x"),
    }.items():
        assert _run(box, "Write", {"file_path": path, "content": "x"}) == 0, label


def test_denied_call_is_recorded_in_the_tenant_chain(box):
    """The deny lands in the ONE chain file of the tenant (positive control for
    the vectors above: the hook really ran against this sandbox)."""
    assert _bash(box, f"gzip {box.chain}") == 2
    records = [json.loads(line) for line in
               box.chain.read_text(encoding="utf-8").splitlines() if line.strip()]
    names = {r.get("event_type") or r.get("event") or r.get("type") for r in records}
    assert "path_gate.denied" in names, records


# ── Round 2 ──────────────────────────────────────────────────────────────────

def test_r2_sec3_project_settings_plugins_and_ancestors(box):
    """R2-SEC-3: only ``~/.claude/settings*.json`` was protected. Project and local
    settings are merged over it, the plugin cache holds the hook copy that runs,
    and ``rm -rf ~/.claude`` / ``mv ~/.claude`` named no protected leaf."""
    sub = box.work / "sub" / ".claude"
    sub.mkdir(parents=True)
    (box.work / ".claude").mkdir()
    plugins = box.user / ".claude" / "plugins"
    for label, path in {
        "cwd-settings": str(box.work / ".claude" / "settings.json"),
        "cwd-settings-local": str(box.work / ".claude" / "settings.local.json"),
        "below-cwd": str(sub / "settings.local.json"),
        "above-cwd": str(box.tmp / ".claude" / "settings.json"),
        "repo-root": _src(".claude", "settings.local.json"),
        "below-repo-root": _src("core", ".claude", "settings.json"),
        "plugin-cache-hook": str(plugins / "cache" / "voice" / "hooks" / "path_gate.py"),
        "plugin-registry": str(plugins / "installed_plugins.json"),
    }.items():
        assert _run(box, "Write", {"file_path": path, "content": "{}"}) == 2, label
        assert _run(box, "Edit", {"file_path": path, "old_string": "a", "new_string": "b"}) == 2, label
    for cmd in (
        "echo '{}' > .claude/settings.local.json",
        "sed -i s/a/b/ .claude/settings.json",
        "rm .claude/settings.json",
        "rm -rf .claude",
        "mv .claude /tmp/elsewhere",
        f"rm -rf {box.user}/.claude",
        f"mv {box.user}/.claude {box.tmp}/gone",
        f"mv {box.user} {box.tmp}/gone",
        f"rm -rf {plugins}",
        f"echo x > {plugins}/known_marketplaces.json",
    ):
        assert _bash(box, cmd) == 2, cmd
    # Reads, and files that are not settings, stay free.
    for cmd in (
        f"cat {box.user}/.claude/settings.json",
        "cat .claude/settings.local.json",
        f"ls {plugins}",
        f"rm {box.user}/.claude/projects/p/scratch.txt",
        "echo x > .claude/notes.md",
        f"echo '{{}}' > {box.tmp}/elsewhere/.claude/settings.json",
    ):
        assert _bash(box, cmd) == 0, cmd


def test_r2_sec4_key_directory_and_witness_guarded_wherever_the_key_is(box):
    """R2-SEC-4: the anchor key here is NOT in the default location. Its directory,
    an ancestor of it, and ``chain_witness/`` (retired generations included) must
    not be removable or movable."""
    wit = box.keydir / "chain_witness"
    wit.mkdir()
    (wit / "abc_retired").write_text("x", encoding="utf-8")
    for retired in ("chain_witness_retired", "chain_ids_retired"):
        (box.keydir / retired).mkdir()
        assert _bash(box, f"rm -rf {box.keydir}/{retired}") == 2, retired
        assert _bash(box, f"mv {box.keydir}/{retired}/x {box.tmp}/y") == 2, retired
        assert _bash(box, f"echo x > {box.keydir}/{retired}/x") == 2, retired
    for cmd in (
        f"rm -rf {box.keydir}",
        f"mv {box.keydir} {box.tmp}/gone",
        f"rm -rf {box.tmp}",
        f"rm -rf {wit}",
        f"rm {wit}/abc_retired",
        f"mv {wit}/abc_retired {box.tmp}/x",
        f"find {box.keydir} -name '*_retired' -delete",
        f"echo x > {wit}/abc",
    ):
        assert _bash(box, cmd) == 2, cmd
    assert _run(box, "Write", {"file_path": str(wit / "abc"), "content": "x"}) == 2
    # An unrelated file beside a non-default key is not swallowed.
    assert _bash(box, f"echo x > {box.keydir}/notes.txt") == 0
    assert _bash(box, f"ls -la {wit}") == 0


_R2_READERS = (
    "awk -F, '{{print $1}}' {c}",
    "awk '$3 == \"x\" {{n++}} END {{print n}}' {c}",
    "sort {c}",
    "sort -u {c} | head",
    "uniq -c {c}",
    "xxd {c} | head",
    "tac {c} | head -3",
    "sed -n 1,5p {c}",
    "perl -ne 'print if /denied/' {c}",
    "tar czf {out}/chain.tgz {c}",
    "tar cf - {c} | wc -c",
    "gzip -c {c} > {out}/c.gz",
    "python3 -m json.tool {c}",
    'for f in {c}; do wc -l "$f"; done',
    "cd {d} && sha256sum audit.jsonl",
    "cd {d} && awk '{{print $1}}' audit.jsonl | sort | uniq -c",
    "cd {d} && tar cf {out}/x.tar audit.jsonl",
    "cp {c} {out}/chain-copy.jsonl",
    "cp {se} {out}/security_events.py",
    "sha256sum {c} {se}",
)
_R2_WRITERS = (
    "sed -i 1d {c}",
    "sed --in-place 1d {c}",
    "sed -n 'w {out}/o' {c}",
    "sort -o {c} {c}",
    "sort -uo {c} {out}/x",
    "sort {c} > {c}",
    "awk -i inplace '{{print}}' {c}",
    "awk '{{print > \"{c}\"}}' {out}/x",
    "awk '{{system(\"rm x\")}}' {c}",
    "awk -f {out}/prog.awk {c}",
    "perl -pi -e 's/a/b/' {c}",
    "perl -ne 'unlink $ARGV' {c}",
    "perl {out}/script.pl {c}",
    "gzip {c}",
    "gzip -k {c}",
    "xxd -r {out}/x {c}",
    "xxd {c} {d}/out.hex",
    # round 3 (R3-SEC-9): `uniq CHAIN /tmp/out` reads the chain - moved to
    # tests/security/test_r3_path_gate.py as an ALLOW vector. The write
    # direction stays denied:
    "uniq {out}/x {c}",
    "python3 -m json.tool {c} {c}",
    "tar xzf {out}/x.tgz -C {d}",
    "tar czf {d}/x.tgz {c}",
    "tar czf {out}/a.tgz --remove-files {c}",
    "cp {out}/x {c}",
    "cp {out}/x.py {se}",
    "cp {c} {d}/audit.jsonl.bak",
    "cp {k} {out}/key",
    "cp -r {home} {out}/copy",
    "mv {c} {out}/gone",
    "cd {d} && cp {out}/x audit.jsonl",
    "cd {d} && tar cf x.tar audit.jsonl",
    "cd {d} && sort -o audit.jsonl audit.jsonl",
    'for f in {c}; do rm "$f"; done',
    'for f in {d}/*.jsonl; do : > "$f"; done',
    "tee -a {c} < {out}/x",
    "truncate -s 0 {c}",
)


def _fmt(box, template: str) -> str:
    out = box.tmp / "out"
    out.mkdir(exist_ok=True)
    return template.format(c=box.chain, d=box.forge, out=out, home=box.home, k=box.key,
                           se=_src("corvin_operator", "forge", "forge", "security_events.py"))


@pytest.mark.parametrize("template", _R2_READERS)
def test_r2_sec5_readers_of_the_chain_are_allowed(box, template):
    """R2-SEC-5: round 1 refused every analysis read of the chain that was not on a
    list of command names. A read is decided by the invocation's arguments."""
    cmd = _fmt(box, template)
    assert _bash(box, cmd) == 0, cmd


@pytest.mark.parametrize("template", _R2_WRITERS)
def test_r2_sec5_writers_of_the_chain_stay_denied(box, template):
    cmd = _fmt(box, template)
    assert _bash(box, cmd) == 2, cmd


def test_r2_sec5_deny_message_names_the_real_reason(box):
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": f"perl {box.tmp}/s.pl {box.chain}"}}),
        capture_output=True, text=True, env=box.env, cwd=str(box.work), timeout=60,
    )
    assert proc.returncode == 2
    assert "unparseable" not in proc.stderr
    assert "not a proven read" in proc.stderr and "Reading is allowed" in proc.stderr


def test_r2_shell_keywords_do_not_hide_the_command(box):
    """Found while fixing R2-SEC-5: the segment after ``do`` / ``then`` / ``else``
    was keyed on the keyword, so ``for x in 1; do rm -rf <home>; done`` and
    ``if true; then rm <chain>; fi`` extracted no target and were ALLOWED."""
    for cmd in (
        f"for x in 1; do rm -rf {box.home}; done",
        f"if true; then rm {box.chain}; fi",
        f"if false; then :; else truncate -s 0 {box.chain}; fi",
        f"while true; do truncate -s 0 {box.chain}; done",
        f"until false; do echo x >> {box.chain}; done",
        f"for f in {box.forge}/*.jsonl; do sed -i 1d \"$f\"; done",
    ):
        assert _bash(box, cmd) == 2, cmd
    for cmd in (
        f"if grep -q denied {box.chain}; then echo yes; fi",
        f'for f in {box.forge}/*.jsonl; do wc -l "$f"; sha256sum "$f"; done',
        'for n in 1 2 3; do echo "$n" > "note-$n.txt"; done',
    ):
        assert _bash(box, cmd) == 0, cmd

