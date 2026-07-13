#!/usr/bin/env python3
"""test_path_gate.py — unit tests for path_gate.py.

Plain-python PASS/FAIL counters in the same style as
forge/tests/test_namespace_gate.py and skill-forge/tests/. Runs the
in-process check() function — fast, deterministic, no subprocess. The
adapter live-E2E lives in operator/bridges/shared/test_adapter_path_gate.py
(iteration 4) and exercises the hook through a real Claude subprocess.

Run: python3 operator/voice/hooks/test_path_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOK_DIR))

# Redirect CORVIN_HOME to a sandboxed root BEFORE importing path_gate, so
# the protected-path detection runs against this fixture, not the real
# user's home.
SCOPE = tempfile.mkdtemp(prefix="path-gate-test-")
os.environ["CORVIN_HOME"] = SCOPE

import path_gate  # noqa: E402

REPO = path_gate._repo_root()
HOME = str(Path(SCOPE))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def expect(label: str, payload: dict, *, blocked: bool) -> None:
    allow, reason = path_gate.check(payload)
    if blocked:
        ok = (allow is False)
        detail = "" if ok else f"expected deny, got allow"
    else:
        ok = (allow is True)
        detail = "" if ok else f"expected allow, got deny: {reason[:120]}"
    t(label, ok, detail=detail)


# ---------------------------------------------------------------------------
# Cases — keep them surgical: each case verifies one vector.
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"path_gate tests — CORVIN_HOME={HOME}, REPO={REPO}")

    # 1. Write into <corvin_home>/<scope>/forge/ → DENY
    expect(
        "Write to forge tools dir",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/sessions/x/forge/tools/x.py",
                        "content": "..."}},
        blocked=True,
    )

    # 2. Write into <corvin_home>/.../skill-forge/skills/ → DENY
    expect(
        "Write to skill-forge skill SKILL.md",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/sessions/x/skill-forge/skills/y/SKILL.md",
                        "content": "..."}},
        blocked=True,
    )

    # 3. Write to audit.jsonl → DENY
    expect(
        "Write to audit.jsonl",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/sessions/x/audit.jsonl",
                        "content": "..."}},
        blocked=True,
    )

    # 4. Write to slot-mirror under repo's operator/skill-forge/skills/dyn/ → DENY
    if REPO is not None:
        expect(
            "Write to slot-mirror SKILL.md",
            {"tool_name": "Write",
             "tool_input": {
                 "file_path": str(REPO / "operator/skill-forge/skills/dyn/x/SKILL.md"),
                 "content": "..."}},
            blocked=True,
        )
    else:
        t("Write to slot-mirror SKILL.md", True, detail="skipped: no repo root")

    # 4b. operator/license/ trust tree — code + trust anchors → DENY (SEAL-SHADOW-01).
    if REPO is not None:
        expect(
            "Write to operator/license validator.py",
            {"tool_name": "Write",
             "tool_input": {"file_path": str(REPO / "operator/license/validator.py"),
                            "content": "..."}},
            blocked=True,
        )
        expect(
            "Drop native seal binary operator/license/_corvin_seal.so",
            {"tool_name": "Write",
             "tool_input": {"file_path": str(REPO / "operator/license/_corvin_seal.so"),
                            "content": "..."}},
            blocked=True,
        )
        expect(
            "Overwrite attestation anchor operator/license/a2a_network_pubkey.pem",
            {"tool_name": "Write",
             "tool_input": {"file_path": str(REPO / "operator/license/a2a_network_pubkey.pem"),
                            "content": "..."}},
            blocked=True,
        )
    else:
        t("operator/license trust-tree protection", True, detail="skipped: no repo root")

    # 4c. R4: authoritative license-token files read by validator._find_token
    # are operator-only — global/license.key and corvin-voice/session.key were
    # NOT covered despite their protected siblings (license.jwt / secrets.json).
    expect(
        "Write to global/license.key",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/global/license.key", "content": "x"}},
        blocked=True,
    )
    expect(
        "Truncate corvin-voice/session.key via Bash",
        {"tool_name": "Bash",
         "tool_input": {"command": ": > ~/.config/corvin-voice/session.key"}},
        blocked=True,
    )

    # 5. Write to a benign user dir (~/cowork/coder/notes.md) → ALLOW
    expect(
        "Write to benign cowork notes",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/cowork/coder/notes.md",
                        "content": "..."}},
        blocked=False,
    )

    # 6. Edit on forge policy.json → DENY
    expect(
        "Edit forge policy.json",
        {"tool_name": "Edit",
         "tool_input": {"file_path": f"{HOME}/global/forge/policy.json",
                        "old_string": "x", "new_string": "y"}},
        blocked=True,
    )

    # 7. Bash redirect into forge tools → DENY
    expect(
        "Bash echo > forge/tools/x.py",
        {"tool_name": "Bash",
         "tool_input": {"command": f"echo x > {HOME}/sessions/x/forge/tools/x.py"}},
        blocked=True,
    )

    # 8. Bash tee into forge → DENY
    expect(
        "Bash tee forge/y.py",
        {"tool_name": "Bash",
         "tool_input": {"command": f"tee {HOME}/sessions/x/forge/y.py < /tmp/x"}},
        blocked=True,
    )

    # 9. Bash redirect into /tmp/bar (not protected) → ALLOW
    expect(
        "Bash benign redirect",
        {"tool_name": "Bash",
         "tool_input": {"command": "cat foo > /tmp/bar"}},
        blocked=False,
    )

    # 9a. R6 #4 — clobber-override `>|` into audit.jsonl → DENY.
    # Old _REDIRECT_RE alternation lacked `>|`, so this truncating redirect
    # slipped through undetected.
    expect(
        "Bash clobber-override >| audit.jsonl",
        {"tool_name": "Bash",
         "tool_input": {"command": f"echo poison >| {HOME}/sessions/x/audit.jsonl"}},
        blocked=True,
    )

    # 9b. R6 #4 — numbered-fd clobber-override `1>|` into forge tool → DENY.
    expect(
        "Bash numbered-fd clobber-override 1>| forge/tools/x.py",
        {"tool_name": "Bash",
         "tool_input": {"command": f"echo poison 1>| {HOME}/sessions/x/forge/tools/x.py"}},
        blocked=True,
    )

    # 9c. R6 #5 — space-less redirect `data>audit.jsonl` (no whitespace before
    # the operator; bash allows it) → DENY. Old lead-anchor required a separator.
    expect(
        "Bash space-less redirect data>audit.jsonl",
        {"tool_name": "Bash",
         "tool_input": {"command": f"echo data>{HOME}/sessions/x/audit.jsonl"}},
        blocked=True,
    )

    # 9d. R6 #5 — space-less `word>protected` form (cmd>policy.json) → DENY.
    expect(
        "Bash space-less word>policy.json",
        {"tool_name": "Bash",
         "tool_input": {"command": f"cmd>{HOME}/global/forge/policy.json"}},
        blocked=True,
    )

    # 9e. Regression guard — the new `>|` operator and space-less form must NOT
    # over-block legitimate writes to a non-protected path. Both must ALLOW.
    expect(
        "Bash clobber-override to non-protected /tmp → ALLOW",
        {"tool_name": "Bash",
         "tool_input": {"command": "echo x >| /tmp/safe.txt"}},
        blocked=False,
    )
    expect(
        "Bash space-less redirect to non-protected /tmp → ALLOW",
        {"tool_name": "Bash",
         "tool_input": {"command": "echo data>/tmp/out.log"}},
        blocked=False,
    )

    # 9f. path-audit 2026-07-06 — `find` mutating actions were a live audit-log
    # deletion bypass (empirically confirmed): `find` matched no target-extraction
    # branch, so a destructive find under a protected path was ALLOWED. Same class
    # as the truncate/rm/ln bypasses. All of these must now DENY.
    expect(
        "Bash find -delete audit.jsonl → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find {HOME} -name audit.jsonl -delete"}},
        blocked=True,
    )
    expect(
        "Bash find -exec rm on forge tools → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find {HOME}/global/forge -name '*.py' -exec rm {{}} +"}},
        blocked=True,
    )
    expect(
        "Bash find -exec truncate audit.jsonl → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find {HOME} -name audit.jsonl -exec truncate -s0 {{}} +"}},
        blocked=True,
    )
    expect(
        "Bash find -fprintf into protected forge → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find /etc -type f -fprintf {HOME}/global/forge/x 'y'"}},
        blocked=True,
    )
    # 9g. round-2 — glob/pattern bypass: a mutating find whose root is an
    # ANCESTOR of the corvin home recurses down into it, so `-name '*.jsonl'`
    # deletes audit.jsonl without ever naming it literally. Must DENY regardless
    # of the pattern text. Also the gfind/bfind aliases must not skip the branch.
    _ANC = str(Path(HOME).parent)  # ancestor of CORVIN_HOME
    expect(
        "Bash find glob '*.jsonl' -delete over ancestor → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find {_ANC} -name '*.jsonl' -delete"}},
        blocked=True,
    )
    expect(
        "Bash find glob '*.jsonl' -exec rm over ancestor → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find {_ANC} -name '*.jsonl' -exec rm {{}} +"}},
        blocked=True,
    )
    expect(
        "Bash gfind alias -delete over protected home → DENY",
        {"tool_name": "Bash",
         "tool_input": {"command": f"gfind {HOME} -name audit.jsonl -delete"}},
        blocked=True,
    )
    # 9h. Regression guard — read-only find and mutating find on a genuinely
    # unrelated root (neither under nor an ancestor of corvin_home) must ALLOW.
    expect(
        "Bash read-only find under protected home → ALLOW",
        {"tool_name": "Bash",
         "tool_input": {"command": f"find {HOME} -name '*.py' -print"}},
        blocked=False,
    )
    expect(
        "Bash find -delete on unrelated dir → ALLOW",
        {"tool_name": "Bash",
         "tool_input": {"command": "find /var/spool/unrelated-xyz -name foo -delete"}},
        blocked=False,
    )

    # 9i. round-3 — recursive / glob / root destructive ops over the corvin tree.
    # is_protected_path only flags specific leaf names/subdirs, so `rm -rf ~/.corvin`,
    # a glob, or a move of a protected tree named no protected leaf and passed the
    # gate — wiping hash-chained audit logs. All must DENY.
    for _label, _cmd in [
        ("rm -rf corvin home root",        f"rm -rf {HOME}"),
        ("rm -rf tenants tree",            f"rm -rf {HOME}/tenants"),
        ("rm glob *.jsonl under home",     f"rm {HOME}/*.jsonl"),
        ("chmod -R over tenants",          f"chmod -R 000 {HOME}/tenants"),
        ("mv protected tree away",         f"mv {HOME}/tenants /tmp/x"),
        ("find tree -delete under home",   f"find {HOME}/tenants -name x -delete"),
        ("find glob | xargs rm",           f"find {str(Path(HOME).parent)} -name '*.jsonl' | xargs rm"),
        ("find -print0 | xargs -0 rm",     f"find {str(Path(HOME).parent)} -name '*.jsonl' -print0 | xargs -0 rm"),
    ]:
        expect(f"Bash {_label} → DENY", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=True)
    # 9j. Regression — destructive ops on UNRELATED paths and reads must ALLOW.
    for _label, _cmd, _blk in [
        ("rm -rf unrelated /tmp",  "rm -rf /var/spool/unrelated-xyz", False),
        ("mv between /tmp paths",  "mv /tmp/a /tmp/b", False),
        ("echo | xargs echo",      "echo hi | xargs echo", False),
    ]:
        expect(f"Bash {_label} → ALLOW", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=_blk)

    # 9k. round-4 — exec-wrapper prefix must not defeat the gate. `env rm -rf
    # ~/.corvin` etc. put the wrapper in cmd_name and slipped past every check.
    for _label, _cmd in [
        ("env rm",        f"env rm -rf {HOME}"),
        ("command rm",    f"command rm -rf {HOME}"),
        ("busybox rm",    f"busybox rm -rf {HOME}"),
        ("nice -n rm",    f"nice -n 10 rm -rf {HOME}"),
        ("timeout rm",    f"timeout 5 rm -rf {HOME}"),
        ("sudo rm",       f"sudo rm -rf {HOME}"),
        ("setsid rm",     f"setsid rm -rf {HOME}"),
        ("env FOO=1 rm",  f"env FOO=1 rm -rf {HOME}"),
        ("env leaf",      f"env rm {HOME}/audit.jsonl"),
        ("busybox trunc", f"busybox truncate -s0 {HOME}/audit.jsonl"),
    ]:
        expect(f"Bash wrapper {_label} → DENY", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=True)
    # 9l. round-4 — archive extraction into the corvin tree overwrites audit.jsonl.
    for _label, _cmd in [
        ("tar -C home",   f"tar -C {HOME} -xf evil.tar"),
        ("tar dir= home", f"tar --directory={HOME} -xf evil.tar"),
        ("unzip -d home", f"unzip -o evil.zip -d {HOME}"),
        ("cpio -D home",  f"cpio -idmv -D {HOME}"),
        ("7z -o home",    f"7z x evil.7z -o{HOME}"),
    ]:
        expect(f"Bash archive {_label} → DENY", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=True)
    # 9m. round-4 — `cd <tree> && <destructive>` (hook can't see the cd).
    for _label, _cmd in [
        ("cd&&rm -rf",    f"cd {HOME} && rm -rf tenants"),
        ("cd;rm leaf",    f"cd {HOME}; rm audit.jsonl"),
        ("cd&&truncate",  f"cd {HOME} && truncate -s0 audit.jsonl"),
    ]:
        expect(f"Bash {_label} → DENY", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=True)
    # 9n. round-4 regression — wrappers/archive/cd on UNRELATED paths still ALLOW.
    for _label, _cmd in [
        ("env rm /tmp",        "env rm -rf /tmp/junk"),
        ("sudo rm /var/tmp",   "sudo rm -rf /var/tmp/y"),
        # NOTE: the test harness roots CORVIN_HOME under /tmp, so /tmp is an
        # ancestor of the home here — use a genuinely-unrelated dir to assert
        # non-over-block (matches the round-2 /var/spool convention).
        ("tar -C unrelated",   "tar -C /var/spool/unrelated-xyz -xf e.tar"),
        ("cd unrelated && rm", "cd /var/spool/unrelated-xyz && rm -rf x"),
        ("cd tree && cat",     f"cd {HOME} && cat audit.jsonl"),
        ("tar read tree",      f"tar czf /tmp/b.tar {HOME}"),
    ]:
        expect(f"Bash {_label} → ALLOW", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=False)

    # 9o. round-5 — chdir-via-wrapper (env -C / sudo -D / doas / pushd) reaches the
    # tree with a relative target; must DENY like the literal `cd` form.
    for _label, _cmd in [
        ("env -C",       f"env -C {HOME} rm -rf tenants"),
        ("env --chdir=",  f"env --chdir={HOME} rm -rf tenants"),
        ("sudo -D",      f"sudo -D {HOME} rm -rf tenants"),
        ("sudo --chdir",  f"sudo --chdir {HOME} rm -rf tenants"),
        ("doas -C",      f"doas -C {HOME} rm -rf tenants"),
        ("pushd",        f"pushd {HOME} && rm -rf tenants"),
    ]:
        expect(f"Bash chdir {_label} → DENY", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=True)
    # 9p. round-5 OVER-BLOCK regression — `cd <ANCESTOR-of-home> && rm -rf <rel>`
    # descends AWAY from the tree and MUST ALLOW (the ancestor arm of
    # _touches_corvin_tree must not fire in the chdir context). These broke normal
    # shell use before the fix.
    _parent = str(Path(HOME).parent)          # ancestor of the corvin home
    _gparent = str(Path(HOME).parent.parent)  # deeper ancestor
    for _label, _cmd in [
        ("cd parent && rm build",  f"cd {_parent} && rm -rf build"),
        ("cd gparent && rm x",     f"cd {_gparent} && rm -rf some-unrelated-dir"),
        ("env -C /tmp make",       "env -C /tmp/build make"),
        ("sudo -D /var rm",        "sudo -D /var/www rm -rf cache"),
    ]:
        expect(f"Bash overblock {_label} → ALLOW", {"tool_name": "Bash",
                "tool_input": {"command": _cmd}}, blocked=False)

    # 9q. cd-context fail-direction symmetry — _dir_at_or_under_corvin_home
    # must fail CLOSED (like its sibling _touches_corvin_tree) when the cd
    # target is unresolvable, e.g. poisoned with an embedded NUL byte. Before
    # this fix _dir_at_or_under_corvin_home returned False (fail-OPEN) on an
    # _abs()-raising target, silently reporting "not chdir'd into the tree"
    # and letting `cd <poisoned-home> && rm -rf tenants` sail through as an
    # ordinary unprotected relative rm.
    _poisoned_cd = f"{HOME}\x00/x"
    t("_dir_at_or_under_corvin_home fails CLOSED on a NUL-poisoned target",
      path_gate._dir_at_or_under_corvin_home(_poisoned_cd) is True,
      detail=f"expected True (fail-closed), got "
             f"{path_gate._dir_at_or_under_corvin_home(_poisoned_cd)!r}")
    t("_touches_corvin_tree also fails CLOSED on the same target "
      "(symmetry check)",
      path_gate._touches_corvin_tree(_poisoned_cd) is True,
      detail=f"expected True, got "
             f"{path_gate._touches_corvin_tree(_poisoned_cd)!r}")
    expect(
        "Bash cd(NUL-poisoned)-target && rm -rf tenants → DENY "
        "(cd-context fail-closed backstop)",
        {"tool_name": "Bash",
         "tool_input": {"command": f"cd {_poisoned_cd} && rm -rf tenants"}},
        blocked=True,
    )

    # 10. sed -i on protected policy → DENY
    expect(
        "sed -i on policy.json",
        {"tool_name": "Bash",
         "tool_input": {"command": f"sed -i 's/a/b/' {HOME}/global/forge/policy.json"}},
        blocked=True,
    )

    # 11. python -c open(..., 'w') on protected → DENY
    expect(
        "python -c open(...,'w') on forge",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"python -c \"open('{HOME}/sessions/x/forge/x.py','w').write('')\""}},
        blocked=True,
    )

    # 12. eval with protected reference → fail-closed DENY
    expect(
        "eval $X with forge in command",
        {"tool_name": "Bash",
         "tool_input": {"command": f"eval \"echo x > {HOME}/sessions/x/forge/y.py\""}},
        blocked=True,
    )

    # 13. WebFetch file:// on protected → DENY
    expect(
        "WebFetch file:// on forge tool",
        {"tool_name": "WebFetch",
         "tool_input": {"url": f"file://{HOME}/sessions/x/forge/tools/x.py"}},
        blocked=True,
    )

    # 14. Read on protected → ALLOW (read is fine; only writes are gated)
    expect(
        "Read on forge tool",
        {"tool_name": "Read",
         "tool_input": {"file_path": f"{HOME}/sessions/x/forge/tools/x.py"}},
        blocked=False,
    )

    # Bonus checks — extra vectors beyond the original 14, kept here so future
    # additions to the gate don't silently break them.

    # 15. mv into protected → DENY (last-nonflag-arg vector)
    expect(
        "mv into forge dir",
        {"tool_name": "Bash",
         "tool_input": {"command": f"mv /tmp/foo {HOME}/sessions/x/forge/x.py"}},
        blocked=True,
    )

    # 16. cp into slot-mirror → DENY
    if REPO is not None:
        expect(
            "cp into slot-mirror",
            {"tool_name": "Bash",
             "tool_input": {"command":
                f"cp /tmp/foo {REPO}/operator/skill-forge/skills/dyn/y/SKILL.md"}},
            blocked=True,
        )

    # 17. NotebookEdit on protected → DENY
    expect(
        "NotebookEdit on forge file",
        {"tool_name": "NotebookEdit",
         "tool_input": {"notebook_path": f"{HOME}/sessions/x/forge/x.ipynb",
                        "new_source": "..."}},
        blocked=True,
    )

    # 18. dd of=<protected> → DENY
    expect(
        "dd of=forge/...",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"dd if=/tmp/in of={HOME}/sessions/x/forge/x.py bs=1"}},
        blocked=True,
    )

    # ---------------------------------------------------------------------
    # Layer-16 v2 vectors (Phase 2 hardening)
    # ---------------------------------------------------------------------

    # 19. Process substitution >(...) into protected → fail-closed DENY
    expect(
        "process subst >(cat > forge/...)",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"echo x | tee >(cat > {HOME}/sessions/x/forge/y.py)"}},
        blocked=True,
    )

    # 20. bash -c '...' wrapping a hidden redirect to forge → fail-closed DENY
    expect(
        "bash -c with forge hint",
        {"tool_name": "Bash",
         "tool_input": {"command": "bash -c 'echo x > /tmp/x; cat forge/y'"}},
        blocked=True,
    )

    # 20b. sh -c with forge hint → fail-closed DENY
    expect(
        "sh -c with skill-forge hint",
        {"tool_name": "Bash",
         "tool_input": {"command": "sh -c 'tee skill-forge/x'"}},
        blocked=True,
    )

    # 21. env -i bash -c '...' (the env-strip wrapper) with hint → DENY
    expect(
        "env -i bash -c with audit.jsonl hint",
        {"tool_name": "Bash",
         "tool_input": {"command": "env -i bash -c 'tee audit.jsonl < /tmp/x'"}},
        blocked=True,
    )

    # 22. xargs with forge hint → fail-closed DENY (target is opaque)
    expect(
        "xargs -I{} with forge hint",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"echo {HOME}/sessions/x/forge/y.py | xargs -I{{}} cp /tmp/x {{}}"}},
        blocked=True,
    )

    # 23. awk -i inplace on forge file → fail-closed DENY
    expect(
        "awk -i inplace with forge hint",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"awk -i inplace '{{print}}' {HOME}/sessions/x/forge/x.py"}},
        blocked=True,
    )

    # 24. Heredoc with forge hint inside → fail-closed DENY
    expect(
        "heredoc with skill-forge hint",
        {"tool_name": "Bash",
         "tool_input": {"command":
            "cat > /tmp/y << 'EOF'\nthis writes to skill-forge later\nEOF"}},
        blocked=True,
    )

    # V-007: exec file descriptor redirect into audit.jsonl → DENY
    expect(
        "bash-exec-fd into audit.jsonl",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"exec 3> {HOME}/global/forge/audit.jsonl; echo x >&3"}},
        blocked=True,
    )

    # V-007: mkfifo piped into forge path → DENY (mkfifo + protected hint)
    expect(
        "bash-mkfifo into forge dir",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"mkfifo /tmp/p; echo x > /tmp/p & cat > /tmp/p > {HOME}/sessions/x/forge/tool.py"}},
        blocked=True,
    )

    # V-007: process substitution write side >(…) with forge hint → DENY
    expect(
        "bash-proc-subst >(cat > forge/...)",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"echo x | tee >(cat > {HOME}/sessions/x/forge/tool.py)"}},
        blocked=True,
    )

    # V-013: command substitution in pipe position with forge hint → DENY
    expect(
        "bash-cmd-subst-pipe into forge",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"$(echo 'writing to forge') | tee {HOME}/sessions/x/forge/out.py"}},
        blocked=True,
    )

    # 25. printf > /tmp/x (no hint) → ALLOW (regression check that printf
    #     redirects keep working in normal cases)
    expect(
        "printf > benign /tmp",
        {"tool_name": "Bash",
         "tool_input": {"command": "printf 'data' > /tmp/notes.txt"}},
        blocked=False,
    )

    # 26. bash -c '...' WITHOUT any hint → ALLOW (don't over-block)
    expect(
        "bash -c benign",
        {"tool_name": "Bash",
         "tool_input": {"command": "bash -c 'echo hello > /tmp/out'"}},
        blocked=False,
    )

    # 27. xargs WITHOUT hint → ALLOW
    expect(
        "xargs benign",
        {"tool_name": "Bash",
         "tool_input": {"command": "ls /tmp | xargs -I{} echo found {}"}},
        blocked=False,
    )

    # 28. Direct write to absolute corvinos path → DENY (corvinos hint)
    expect(
        "Bash redirect to .corvinOS path",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"echo x > {HOME}/some/forge/file.py"}},
        blocked=True,
    )

    # ---------------------------------------------------------------------
    # ADR-0012 — data_policy.yaml protection (Phase 12.8 hardening)
    # ---------------------------------------------------------------------

    # 29. Write to data_policy.yaml → DENY (operator-only file; LLM
    # overwriting it could disable every PII strategy and let the
    # snapshot leak unredacted values).
    expect(
        "Write to data_policy.yaml",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/global/data_policy.yaml",
                        "content": "spec: {default_strategy: drop}"}},
        blocked=True,
    )

    # 30. Edit data_policy.json variant → DENY
    expect(
        "Edit data_policy.json",
        {"tool_name": "Edit",
         "tool_input": {"file_path": f"{HOME}/global/data_policy.json",
                        "old_string": "redact", "new_string": "drop"}},
        blocked=True,
    )

    # 31. Bash redirect into data_policy.yaml → DENY
    expect(
        "Bash redirect to data_policy.yaml",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"echo 'kind: DataPolicy' > {HOME}/global/data_policy.yaml"}},
        blocked=True,
    )

    # 32. sed -i on data_policy.yaml → DENY
    expect(
        "sed -i on data_policy.yaml",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"sed -i 's/redact/drop/' {HOME}/global/data_policy.yaml"}},
        blocked=True,
    )

    # 33. eval-wrapping with data_policy hint → fail-closed DENY
    expect(
        "eval with data_policy hint",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"eval \"cat > {HOME}/global/data_policy.yaml\""}},
        blocked=True,
    )

    # 34. heredoc with data_policy hint → fail-closed DENY
    expect(
        "heredoc with data_policy hint",
        {"tool_name": "Bash",
         "tool_input": {"command":
            "cat > /tmp/x << 'EOF'\ndata_policy.yaml is in here\nEOF"}},
        blocked=True,
    )

    # 35. Read on data_policy.yaml → ALLOW (read is fine, only writes
    # are gated; an operator can still grep the policy through Bash
    # for diagnostic purposes)
    expect(
        "Read on data_policy.yaml",
        {"tool_name": "Read",
         "tool_input": {"file_path": f"{HOME}/global/data_policy.yaml"}},
        blocked=False,
    )

    # 36. Write to data_policy.yml (alt extension) → DENY
    expect(
        "Write to data_policy.yml variant",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/global/data_policy.yml",
                        "content": "..."}},
        blocked=True,
    )

    # 37. Write to a benign foo_policy.yaml elsewhere → ALLOW (the
    # gate is name-anchored, not substring-anchored)
    expect(
        "Write to unrelated policy.yaml",
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{HOME}/cowork/x/foo_policy.yaml",
                        "content": "..."}},
        blocked=False,
    )

    # ---------------------------------------------------------------------
    # ADR-0014 — admin-UI trust artefacts (Phase 14.1)
    # ---------------------------------------------------------------------

    # 38. Write to operator_tokens.json → DENY (LLM-side overwrite
    # could mint a fake operator token).
    expect(
        "Write to operator_tokens.json",
        {"tool_name": "Write",
         "tool_input": {"file_path":
            f"{HOME}/global/admin/operator_tokens.json",
                        "content": "{}"}},
        blocked=True,
    )

    # 39. Write to a session record → DENY (impersonation vector).
    expect(
        "Write to admin/sessions/<sid>.json",
        {"tool_name": "Write",
         "tool_input": {"file_path":
            f"{HOME}/global/admin/sessions/abc.json",
                        "content": "{}"}},
        blocked=True,
    )

    # 40. Bash redirect into the admin tree → DENY.
    expect(
        "Bash redirect into admin tree",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"echo x > {HOME}/global/admin/operator_tokens.json"}},
        blocked=True,
    )

    # 41. eval-wrapping with operator_tokens hint → fail-closed DENY.
    expect(
        "eval with operator_tokens hint",
        {"tool_name": "Bash",
         "tool_input": {"command":
            f"eval \"cat > {HOME}/global/admin/operator_tokens.json\""}},
        blocked=True,
    )

    # ---------------------------------------------------------------------
    # Subprocess + audit-chain E2E (iteration 3)
    # ---------------------------------------------------------------------
    import json
    import subprocess

    # Fresh CORVIN_HOME for the subprocess case so the audit path is
    # isolated from the in-process check tests above.
    audit_home = tempfile.mkdtemp(prefix="path-gate-audit-")
    audit_jsonl = Path(audit_home) / "global" / "forge" / "audit.jsonl"
    deny_payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": f"{audit_home}/sessions/x/forge/tools/x.py",
                       "content": "..."},
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK_DIR / "path_gate.py")],
        input=json.dumps(deny_payload),
        capture_output=True,
        text=True,
        env={**os.environ,
             "CORVIN_HOME": audit_home,
             "CORVIN_CALLER_PERSONA": "coder",
             "CORVIN_CHANNEL_ID": "test-chat-42"},
    )
    t("subprocess returns exit-2 on deny",
      proc.returncode == 2,
      detail=f"got {proc.returncode}; stderr={proc.stderr[:80]!r}")
    t("subprocess writes path_gate.denied to audit chain",
      audit_jsonl.exists(),
      detail=f"audit path: {audit_jsonl}")
    if audit_jsonl.exists():
        # The path-gate now also emits a dialectic.decision event on
        # every deny (Layer 11), so we filter to the path_gate.denied
        # record specifically rather than reading the last line.
        all_events = []
        for line in audit_jsonl.read_text().splitlines():
            if line.strip():
                try:
                    all_events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        denied = [e for e in all_events
                  if e.get("event_type") == "path_gate.denied"]
        rec = denied[-1] if denied else {}
        t("audit event_type is path_gate.denied",
          rec.get("event_type") == "path_gate.denied",
          detail=f"all events: {[e.get('event_type') for e in all_events]}")
        t("audit details carry persona+channel+target",
          (rec.get("details", {}).get("persona") == "coder"
           and rec.get("details", {}).get("channel_id") == "test-chat-42"
           and "forge/tools/x.py" in rec.get("details", {}).get("target", "")))
        t("audit record carries hash-chain hash",
          isinstance(rec.get("hash"), str) and len(rec["hash"]) == 16)

        # verify_chain across the file
        try:
            sys.path.insert(0, str(REPO / "operator/forge"))
            from forge.security_events import verify_chain  # type: ignore
            ok, problems = verify_chain(audit_jsonl)
            t("verify_chain reports (ok, []) over the deny event",
              ok and not problems,
              detail=f"problems={problems!r}")
        except Exception as e:  # pragma: no cover
            t("verify_chain reports (ok, [])", False, detail=str(e))

    # Subprocess: ALLOW path → exit 0, no audit file
    audit_home2 = tempfile.mkdtemp(prefix="path-gate-allow-")
    allow_payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": f"{audit_home2}/cowork/coder/notes.md",
                       "content": "..."},
    }
    proc2 = subprocess.run(
        [sys.executable, str(HOOK_DIR / "path_gate.py")],
        input=json.dumps(allow_payload),
        capture_output=True, text=True,
        env={**os.environ, "CORVIN_HOME": audit_home2},
    )
    t("subprocess returns exit-0 on allow",
      proc2.returncode == 0,
      detail=f"got {proc2.returncode}; stderr={proc2.stderr[:80]!r}")
    t("subprocess does NOT write audit on allow",
      not (Path(audit_home2) / "global" / "forge" / "audit.jsonl").exists())

    # ------------------------------------------------------------------
    # KNOWN BUG (documented, NOT fixed here — see WRITE_RESULT.bugsDiscovered):
    # main() (path_gate.py) calls `allow, reason = check(payload)` with NO
    # enclosing try/except, and check() itself has no top-level guard either.
    # A payload engineered to make a reachable helper raise (e.g. a NUL byte
    # in file_path, which makes Path.resolve() inside _abs() raise
    # ValueError, uncaught by is_protected_path/check) crashes the whole
    # process with Python's default uncaught-exception exit code — NOT the
    # module's own documented "exit 0 -> allow, exit 2 -> deny, fail closed"
    # contract, and none of the deny-side effects (_emit_audit,
    # _emit_dialectic, stderr deny message) ever run. This pins the CURRENT
    # (unsafe) behavior so the gap is visible in the suite; when main() gets
    # a top-level fail-closed backstop, this test's assertions must be
    # updated to expect returncode == 2 and a written audit event.
    # ------------------------------------------------------------------
    crash_home = tempfile.mkdtemp(prefix="path-gate-crash-")
    crash_audit_jsonl = Path(crash_home) / "global" / "forge" / "audit.jsonl"
    # NUL byte in file_path makes Path.resolve() (inside _abs(), called from
    # is_protected_path <- check) raise ValueError, uncaught anywhere on the
    # path from main() down to _abs().
    crash_payload = {"tool_name": "Write",
                      "tool_input": {"file_path": "/tmp/evil\x00.py",
                                     "content": "..."}}
    proc3 = subprocess.run(
        [sys.executable, str(HOOK_DIR / "path_gate.py")],
        input=json.dumps(crash_payload),
        capture_output=True, text=True,
        env={**os.environ, "CORVIN_HOME": crash_home},
    )
    t("BUG-PIN: NUL-byte file_path crashes main() with an exit code that "
      "is NEITHER the documented allow (0) NOR deny (2) contract",
      proc3.returncode not in (0, 2),
      detail=f"got {proc3.returncode}; expected an undocumented/uncaught-"
             f"exception exit code (currently {proc3.returncode}), proving "
             f"there is no top-level fail-closed backstop in main()")
    t("BUG-PIN: the crash is an uncaught Python traceback, not the "
      "module's own deny message",
      "Traceback" in proc3.stderr and "ValueError" in proc3.stderr,
      detail=f"stderr[:200]={proc3.stderr[:200]!r}")
    t("BUG-PIN: no audit event is written when check() crashes (the "
      "_emit_audit deny-side-effect never runs on an uncaught exception)",
      not crash_audit_jsonl.exists(),
      detail=f"audit path: {crash_audit_jsonl}")

    # ----- Roadmap F13 — boot-time self-test --------------------------------
    print("\n[F13] path-gate self-test on boot")
    # Happy path — every curated vector must be denied.
    self_home = tempfile.mkdtemp(prefix="path-gate-self-test-")
    saved = os.environ.get("CORVIN_HOME")
    os.environ["CORVIN_HOME"] = self_home
    try:
        ok, fails = path_gate.path_gate_self_test()
        t("self_test: clean install passes (no failures)",
          ok and not fails,
          detail=f"ok={ok} fails={fails!r}")

        # No audit event written when self-test passes.
        audit_jsonl_self = Path(self_home) / "global" / "forge" / "audit.jsonl"
        t("self_test: clean install does NOT write self_test_failed event",
          not audit_jsonl_self.exists()
          or "path_gate.self_test_failed" not in audit_jsonl_self.read_text())

        # Failure injection — monkey-patch _self_test_vectors to include a
        # vector that targets a NON-protected path so check() will allow it.
        # The self-test should report failure and emit a CRITICAL audit event.
        unrelated = Path(tempfile.mkdtemp(prefix="path-gate-unrelated-")) / "x.md"
        original_vectors = path_gate._self_test_vectors

        def _injected():
            return original_vectors() + [
                ("synthetic-allow",
                 {"tool_name": "Write",
                  "tool_input": {"file_path": str(unrelated)}}),
            ]
        path_gate._self_test_vectors = _injected
        try:
            ok2, fails2 = path_gate.path_gate_self_test()
            t("self_test: injected unprotected path triggers failure",
              not ok2 and "synthetic-allow" in fails2,
              detail=f"ok={ok2} fails={fails2!r}")

            # CRITICAL audit event must land in the chain.
            if audit_jsonl_self.exists():
                events = []
                for line in audit_jsonl_self.read_text().splitlines():
                    if line.strip():
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                st_events = [e for e in events
                             if e.get("event_type") == "path_gate.self_test_failed"]
                t("self_test: CRITICAL self_test_failed event written",
                  len(st_events) >= 1,
                  detail=f"got {len(st_events)} of "
                         f"{[e.get('event_type') for e in events]}")
                if st_events:
                    e = st_events[-1]
                    t("self_test: event severity = CRITICAL",
                      e.get("severity") == "CRITICAL",
                      detail=f"got {e.get('severity')!r}")
                    failures_in_event = e.get("details", {}).get("first_failures") or []
                    t("self_test: event details list synthetic-allow",
                      "synthetic-allow" in failures_in_event,
                      detail=f"first_failures={failures_in_event!r}")
            else:
                t("self_test: audit file written after failure", False,
                  detail=f"missing {audit_jsonl_self}")
        finally:
            path_gate._self_test_vectors = original_vectors
    finally:
        if saved is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = saved

    print(f"\nResult: PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
