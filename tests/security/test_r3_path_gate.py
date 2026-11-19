"""Review 2026-11-19 round 3 - path_gate: R3-SEC-1, R3-SEC-9, R3-SEC-10.

Every vector runs the REAL hook as a subprocess, the way Claude Code runs it:
payload on stdin, exit status 2 is a deny, 0 an allow. Nothing is imported from
the gate, so a vector cannot pass because of in-process state.

R3-SEC-1: ``_is_reader_invocation`` classified an invocation as a read and the
strict-store check was then skipped. It knew exact spellings only, so GNU
option abbreviations (``sed --in-pl``, ``sort --outp``, ``tar --rem``), sed
``w`` / ``e`` commands behind an address, tar checkpoint actions and a path
inside a script token all reached the session ledger or the audit chain.

R3-SEC-9: the same classification refused plain reads (``uniq CHAIN /tmp/out``,
a sed / perl pattern that merely contains the word ``w`` / ``open``).

R3-SEC-10: ``spawn_gates.py`` and ``house_rules.yaml`` are gate sources.
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
    home = tmp_path / "rt" / ".corvin"
    forge = home / "tenants" / "_default" / "global" / "forge"
    forge.mkdir(parents=True)
    chain = forge / "audit.jsonl"
    chain.write_text("", encoding="utf-8")
    ledger_dir = home / "tenants" / "_default" / "session_ledger" / "discord" / "c1"
    ledger_dir.mkdir(parents=True)
    ledger = ledger_dir / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    user = tmp_path / "user"
    (user / ".claude").mkdir(parents=True)
    keydir = tmp_path / "keys"
    keydir.mkdir()
    key = keydir / "audit_anchor.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("b\nc\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()

    class Box:
        pass

    b = Box()
    b.home, b.chain, b.forge, b.ledger, b.ledger_dir = home, chain, forge, ledger, ledger_dir
    b.out, b.work, b.tmp = out, work, tmp_path
    b.env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(user),
        "USERPROFILE": str(user),
        "CORVIN_HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(key),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return b


def _run(box, tool: str, tool_input: dict) -> int:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        capture_output=True, text=True, env=box.env, cwd=str(box.work), timeout=60,
    )
    assert proc.returncode in (0, 2), (proc.returncode, proc.stderr[-2000:])
    return proc.returncode


def _fmt(box, template: str, store: str) -> str:
    target = box.ledger if store == "ledger" else box.chain
    return template.format(T=target, D=target.parent, N=target.name, X=box.out / "x", O=box.out)


# {T} the protected file, {D} its directory, {N} its name, {X} an ordinary
# input file, {O} an ordinary output directory.
_WRITERS = (
    # GNU long-option abbreviations
    "sed --in-pl 's/a/b/' {T}",
    "sed --i 's/.*//' {T}",
    "sed --in-place=.bak 's/a/b/' {T}",
    "sort --out={T} {X}",
    "sort {X} --outp {T}",
    "sort --o {T} {X}",
    "tar cf {O}/o.tar {T} --remove-fi",
    "tar -c -f {O}/o.tar {T} --rem",
    "tar -c --file {O}/o.tar --remove-files {T}",
    "tar cf {O}/o.tar {T} --checkpoint=1 --checkpoint-ac=exec=id",
    "tar cf {O}/o.tar {X} --checkpoint=1 --checkpoint-action='exec=rm {T}'",
    "tar cf {O}/o.tar {X} --to-comm='tee {T}'",
    "tar cfg {O}/o.tar {T} {X}",
    "tar --del -f {T} x",
    "cp --t={D} {O}/{N}",
    "cp --targ {D} {O}/{N}",
    # sed w / W / e behind an address, s///w
    "sed -n '$w {T}' {X}",
    "sed -n '1,$w {T}' {X}",
    "sed -n '0~1w {T}' {X}",
    "sed -n '$W {T}' {X}",
    "sed 's/a/b/gw {T}' {X}",
    "sed 's/a/b/w {T}' {X}",
    "sed '1e unlink {T}' {X}",
    "sed -n 'w {T}' {X}",
    "sed -n -e p -e 'w {T}' {X}",
    "sed -n --expression='w {T}' {X}",
    "sed -n '/b/w {T}' {X}",
    "sed -n '/b/!w {T}' {X}",
    "sed -n '1{{w {T}\n}}' {X}",
    # the same after a `cd` and inside a loop
    "cd {D} && sed -n '$w {N}' {X}",
    "cd {D} && sed -n 'w {N}' {X}",
    "cd {D} && sort --out={N} {X}",
    "cd {D} && sort --outp {N} {X}",
    "cd {D} && sed --in-pl 's/a/b/' {N}",
    "cd {D} && tar cf {O}/o.tar {N} --remove-fi",
    "cd {D} && tar cf {O}/o.tar {N} --checkpoint=1 --checkpoint-ac=exec=id",
    "for f in {T}; do sed --in-pl 's/a/b/' \"$f\"; done",
    "for f in {T}; do sort --out=\"$f\" {X}; done",
    "for f in {T}; do sed -n \"\\$w $f\" {X}; done",
    "for f in {T}; do tar cf {O}/o.tar \"$f\" --rem; done",
    # other writers hidden behind a read classification
    "perl -e'unlink(\"{T}\")'",
    "perl -e '1' -e 'unlink(\"{T}\")'",
    "perl -ne '1 / unlink / 1' {T}",
    "awk 'BEGIN{{\"rm {T}\" | getline}}'",
    "awk 'BEGIN{{f=\"{T}\"; print \"x\" \\\n > f}}'",
    "zstd -t {T} --rm",
    "zstd -l {X} -o {T}",
    "uniq {X} {T}",
    "xxd {X} {T}",
    "xxd -r {X} {T}",
    "tree -o {T} {O}",
    "rg --pre rm . {T}",
)

_READERS = (
    # the readers round 2 opened must stay open
    "cat {T}",
    "wc -l {T}",
    "grep -c denied {T}",
    "sort {T}",
    "sort -u -k1,1 {T}",
    "sort --unique --reverse {T}",
    "sort -o {O}/sorted.txt {T}",
    "uniq -c {T}",
    "xxd {T}",
    "xxd -l 64 {T}",
    "awk '{{print $1}}' {T}",
    "awk -F: '/foo/ {{n++}} END {{print n}}' {T}",
    "sed -n '1,5p' {T}",
    "sed -n '$p' {T}",
    "sed -n -e 1p -e '$p' {T}",
    "sed 's/a/b/g' {T}",
    "sed -E 's,a,b,2' {T}",
    "sed -e 's/x/y/I' -e 's/ w f/z/2' {T}",
    "sed --quiet --expression=1p {T}",
    "sed '/^$/d' {T}",
    "sed -n '/denied/{{p}}' {T}",
    "perl -ne 'print if $. < 5' {T}",
    "gzip -c {T}",
    "zstd -c {T}",
    "tar czf {O}/c.tgz {T}",
    "tar -czf {O}/c.tgz {T}",
    "tar cf {O}/c.tar --exclude=x {T}",
    "tar tf {O}/c.tar",
    "cp {T} {O}/copy.jsonl",
    "python3 -m json.tool {T}",
    "for f in {T}; do wc -l \"$f\"; done",
    "cd {D} && sort {N}",
    "cd {D} && sed -n 1p {N}",
    # R3-SEC-9: false denials of round 2
    "uniq {T} {O}/out.txt",
    "xxd {T} {O}/out.hex",
    "sed -n '/write w something/p' {T}",
    "sed -n -e '/e w x/p' -e '/1e/p' {T}",
    "perl -ne 'print if /open/' {T}",
    "perl -ne 'print if /unlink/i' {T}",
    "perl -ne '/system call/ and print' {T}",
)


@pytest.mark.parametrize("store", ("ledger", "chain"))
@pytest.mark.parametrize("template", _WRITERS)
def test_r3_sec1_writers_are_denied(box, template, store):
    cmd = _fmt(box, template, store)
    assert _run(box, "Bash", {"command": cmd}) == 2, cmd


# The session ledger is not a copy source (only the chain and gate sources are).
_CHAIN_ONLY_READERS = ("cp {T} {O}/copy.jsonl",)


@pytest.mark.parametrize("store", ("ledger", "chain"))
@pytest.mark.parametrize("template", _READERS)
def test_r3_readers_stay_allowed(box, template, store):
    if store == "ledger" and template in _CHAIN_ONLY_READERS:
        pytest.skip("cp of a ledger file is not a read by design")
    cmd = _fmt(box, template, store)
    assert _run(box, "Bash", {"command": cmd}) == 0, cmd


def test_r3_unrelated_work_is_not_refused(box):
    """The stricter classification must not reach commands that name no store."""
    for cmd in (
        f"sed --in-place 's/a/b/' {box.out}/x",
        f"sed -i 's/a/b/' {box.out}/x",
        f"sed -n 'w {box.out}/copy.txt' {box.out}/x",
        f"sort --output={box.out}/s.txt {box.out}/x",
        f"tar cf {box.out}/o.tar {box.out}/x --remove-files",
        f"uniq {box.out}/x {box.out}/u.txt",
        f"perl -pi -e 's/a/b/' {box.out}/x",
        f"cp --target-directory={box.out} {box.work}/a.txt",
        'git commit -m "the session ledger and the audit chain stay append-only"',
    ):
        assert _run(box, "Bash", {"command": cmd}) == 0, cmd


def test_r3_abbreviated_options_on_a_protected_non_store_file(box):
    """The write-TARGET extraction knows the abbreviations too: a protected file
    that is not a strict store (a Forge policy file) is still refused."""
    policy = box.forge / "policy.json"
    policy.write_text("{}", encoding="utf-8")
    for cmd in (
        f"sed --in-pl 's/a/b/' {policy}",
        f"sort --outp {policy} {box.out}/x",
        f"sort --out={policy} {box.out}/x",
        f"sed -n 'w {policy}' {box.out}/x",
        f"tar cf {box.out}/o.tar {policy} --rem",
        f"cp --t={box.forge} {box.out}/policy.json",
    ):
        assert _run(box, "Bash", {"command": cmd}) == 2, cmd


# ---- R3-SEC-10 -------------------------------------------------------------

_GATE_SOURCES = (
    ("corvin_operator", "bridges", "shared", "spawn_gates.py"),
    ("corvin_operator", "policy", "house_rules.yaml"),
)


@pytest.mark.parametrize("rel", _GATE_SOURCES, ids=lambda r: r[-1])
@pytest.mark.parametrize("tool", ("Write", "Edit"))
def test_r3_sec10_write_tools_refused_on_spawn_gates_and_policy(box, rel, tool):
    target = str(REPO.joinpath(*rel))
    assert Path(target).is_file(), target
    payload = {"file_path": target, "content": "x"}
    if tool == "Edit":
        payload = {"file_path": target, "old_string": "a", "new_string": "b"}
    assert _run(box, tool, payload) == 2, target


@pytest.mark.parametrize("rel", _GATE_SOURCES, ids=lambda r: r[-1])
def test_r3_sec10_bash_writes_refused_and_reads_allowed(box, rel):
    target = str(REPO.joinpath(*rel))
    for cmd in (
        f"cp {box.out}/x {target}",
        f"echo x >> {target}",
        f"sed -i 1d {target}",
        f"sed --in-pl 1d {target}",
        f"rm {target}",
    ):
        assert _run(box, "Bash", {"command": cmd}) == 2, cmd
    for cmd in (
        f"cat {target}",
        f"grep -n gate {target}",
        f"cp {target} {box.out}/copy",
    ):
        assert _run(box, "Bash", {"command": cmd}) == 0, cmd
