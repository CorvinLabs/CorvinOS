"""``corvin-maintainer`` — operator CLI for the ADR-0178 Tier-CONTRIBUTOR loop.

Turns the "production-remaining" steps into commands:

  corvin-maintainer keygen [--out-priv FILE]      # one-time: Ed25519 keypair
  corvin-maintainer issue  --priv-file F --instance ID [--subject S] [--ttl-days N]
  corvin-maintainer verify                         # checks the env config
  corvin-maintainer run --repo DIR --patch P.json [--diagnosis D.json]
                        [--test-cmd "..."] [--direct-main] [--push]

The PRIVATE key is YOUR secret — keygen writes it to a 0600 file (or stdout) and
it never leaves your machine. ``issue`` mints a capability bound to one instance.
``run`` drives the L6 loop with a built-in pytest gate_runner and a file-based
patch_source (the maintainer/agent authors the patch JSON). Deny-by-default
everywhere — without a valid pinned key + token, ``run`` refuses.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from . import maintainer_capability as MC
from . import maintenance_loop as ML


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _read_priv(args) -> bytes:
    if getattr(args, "priv_file", None):
        raw = Path(args.priv_file).read_text(encoding="utf-8").strip()
    elif os.environ.get("CORVIN_MAINTAINER_PRIV"):
        raw = os.environ["CORVIN_MAINTAINER_PRIV"].strip()
    else:
        raise SystemExit("error: provide --priv-file or set CORVIN_MAINTAINER_PRIV")
    return base64.b64decode(raw)


def cmd_keygen(args) -> int:
    from cryptography.hazmat.primitives import serialization as S
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())
    pub = sk.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
    pub_b64 = _b64(pub)
    if args.out_priv:
        p = Path(args.out_priv)
        # 0o600 in os.open only applies when the file is CREATED; a pre-existing
        # (possibly world-readable) file would keep its old mode under O_TRUNC.
        # os.fchmod forces 0600 UNCONDITIONALLY before the secret is written
        # (review 2026-06-29, HIGH).
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
        except BaseException:
            os.close(fd)
            raise
        with os.fdopen(fd, "w") as fh:   # fdopen now owns + closes fd
            fh.write(_b64(priv) + "\n")
        print(f"private key → {p} (mode 0600, KEEP SECRET, never commit)")
    else:
        print("PRIVATE KEY (store offline + secret, never commit):")
        print("  " + _b64(priv))
    print("\nPUBLIC KEY — pin it on the maintainer install:")
    print(f"  export CORVIN_MAINTAINER_PUBKEY={pub_b64}")
    return 0


def cmd_issue(args) -> int:
    priv = _read_priv(args)
    iid = args.instance or MC.current_instance_id()
    if not iid:
        raise SystemExit("error: no --instance and current_instance_id() is empty "
                         "(set CORVIN_INSTANCE_ID or pass --instance)")
    tok = MC.issue(priv, instance_id=iid, subject=args.subject,
                   ttl_seconds=int(args.ttl_days) * 86400)
    print("capability token (set as CORVIN_MAINTAINER_CAP on instance "
          f"'{iid}'):\n")
    print(tok)
    return 0


def cmd_verify(args) -> int:
    v = MC.is_contributor()
    print(json.dumps({
        "allowed": v.allowed, "reason": v.reason, "subject": v.subject,
        "instance_id": MC.current_instance_id() or "(empty)",
        "pubkey_pinned": bool(os.environ.get("CORVIN_MAINTAINER_PUBKEY")),
        "token_present": bool(os.environ.get("CORVIN_MAINTAINER_CAP")),
    }, indent=2))
    return 0 if v.allowed else 1


def _file_patch_source(patch_path: str):
    def src(diag: dict):
        spec = json.loads(Path(patch_path).read_text(encoding="utf-8"))
        edits = [ML.PatchEdit(e["path"], e["new_content"]) for e in spec.get("edits", [])]
        if not edits:
            return None
        return ML.Patch(
            diagnosis_id=str(spec.get("diagnosis_id") or diag.get("id") or "diag"),
            summary=spec.get("summary", "maintenance fix"),
            risk_class=spec.get("risk_class", "refactor"),
            edits=edits,
        )
    return src


def _pytest_gate(test_cmd: list[str], repo: str):
    def g() -> tuple[bool, str]:
        p = subprocess.run(test_cmd, cwd=repo, capture_output=True, text=True, timeout=1800)
        return (p.returncode == 0, (p.stdout + p.stderr)[-1200:])
    return g


def cmd_run(args) -> int:
    diag = {"id": "diag"}
    if args.diagnosis:
        diag = json.loads(Path(args.diagnosis).read_text(encoding="utf-8"))
    test_cmd = (args.test_cmd.split() if args.test_cmd
                else [sys.executable, "-m", "pytest", "core/console/tests", "-q"])
    if args.auto:
        # Fully-automatic: the engine generates the patch from the diagnosis.
        # Engine-generated patches are ALWAYS ack-gated (never auto-merged) — so
        # --direct-main has no effect here by design.
        from .patch_generator import engine_patch_source, default_llm
        llm = default_llm(model=args.model, working_dir=args.repo)
        if llm is None:
            print(json.dumps({"status": "no_patch",
                              "detail": "no engine available for --auto"}, indent=2))
            return 1
        patch_source = engine_patch_source(repo_dir=args.repo, llm=llm)
    elif args.patch:
        patch_source = _file_patch_source(args.patch)
    else:
        raise SystemExit("error: provide --patch FILE or --auto")
    r = ML.run_maintenance_loop(
        diagnosis=diag,
        repo_dir=args.repo,
        patch_source=patch_source,
        capability_token=None,                      # from CORVIN_MAINTAINER_CAP env
        gate_runner=_pytest_gate(test_cmd, args.repo),
        enable_direct_main=bool(args.direct_main),
        enable_push=bool(args.push),
    )
    print(json.dumps({
        "status": r.status, "branch": r.branch, "commit": r.commit,
        "requires_ack": r.requires_ack, "detail": r.detail,
        "gate_reasons": r.gate_reasons, "telemetry": r.telemetry,
    }, indent=2))
    # distinct exit codes so automation can tell the three states apart:
    #   0 = terminally resolved (merged/pushed)
    #   2 = a PR awaits human ack (NOT done — never auto-proceed on this)
    #   1 = denied / gate-failed / error
    if r.status in ("merged", "pushed"):
        return 0
    if r.status == "pr_ready":
        return 2
    return 1


def cmd_remotes(args) -> int:
    """List or add remote-instance log sources (used by `nightly --pull-remotes`)."""
    from forge import paths as _paths  # type: ignore
    from . import remote_logs as RL
    home = _paths.corvin_home()
    remotes = RL.load_remotes(home)
    if args.action == "add":
        if not (args.name and args.ssh):
            raise SystemExit("error: add needs --name and --ssh USER@HOST")
        remotes = [r for r in remotes if r.get("name") != args.name]
        remotes.append({"name": args.name, "ssh": args.ssh,
                        "remote_home": args.remote_home})
        RL.save_remotes(home, remotes)
    print(json.dumps({"remotes": remotes,
                      "config": str(RL.remotes_config_path(home))}, indent=2))
    return 0


def cmd_bundle(args) -> int:
    """Collect all healing/logging artifacts into one zippable support folder.
    No capability needed — any user runs this to send their errors to the
    maintainer. Secret-safe (logs + metadata only)."""
    from . import support_bundle as SB
    out = SB.create_default(out_dir=args.out)
    print(json.dumps({"status": "ok", "bundle": str(out),
                      "hint": "send this .zip to the maintainer — it contains logs"
                              " + metadata only, no secrets"}, indent=2))
    return 0


def _git_q(repo, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=180)
    return p.returncode, (p.stdout + p.stderr).strip()


def _remote_branch_exists(repo, branch: str) -> bool:
    rc, out = _git_q(repo, "ls-remote", "--heads", "origin", branch)
    return rc == 0 and bool(out.strip())


def _pr_body(diag: dict, result) -> str:
    return (
        "**Autonomous nightly L6 maintenance PR — ADR-0178 Tier CONTRIBUTOR.**\n\n"
        f"- Diagnosis `{diag.get('id')}`: {diag.get('root_cause', '')}\n"
        "- Patch generated by the engine running **tool-less** (mode=restricted).\n"
        "- Gates: signed maintainer.commit capability + tests green + paths in-scope.\n"
        "- Engine-generated → **always ack-gated**: review + merge here, never auto-merged.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )


def _pypi_token(repo: Path) -> str:
    t = os.environ.get("PYPI_TOKEN") or os.environ.get("TWINE_PASSWORD") or ""
    if t:
        return t.strip()
    envf = repo / ".env"
    try:
        for line in envf.read_text(encoding="utf-8").splitlines():
            if line.startswith("PYPI_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _bump_patch(version: str) -> str:
    parts = version.strip().split(".")
    if not parts[-1].isdigit():
        return version
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def _release(repo: Path, *, dry_run: bool = False) -> dict:
    """Auto-publish the NEXT PyPI version — but ONLY for the maintainer instance
    AND only when main advanced since the last release tag. Double deny-by-default
    gate: the signed maintainer.commit capability + the PYPI_TOKEN secret. A repo
    clone / other user has neither, so they can NEVER publish."""
    import re as _re
    cap = MC.is_contributor()
    if not cap.allowed:
        return {"released": False, "reason": f"not a contributor: {cap.reason}"}
    token = _pypi_token(repo)
    if not token:
        return {"released": False, "reason": "no PYPI_TOKEN (not the maintainer instance)"}
    # something new since the last vX.Y.Z tag?
    rc, last = _git_q(repo, "describe", "--tags", "--abbrev=0", "--match", "v*")
    last = last.strip()
    if rc == 0 and last:
        rc2, cnt = _git_q(repo, "rev-list", f"{last}..HEAD", "--count")
        if rc2 == 0 and cnt.strip() == "0":
            return {"released": False, "reason": f"nothing new since {last}"}
    pyproj = repo / "pyproject.toml"
    text = pyproj.read_text(encoding="utf-8")
    m = _re.search(r'^version = "([^"]+)"', text, _re.M)
    if not m:
        return {"released": False, "reason": "version not found"}
    cur, new = m.group(1), _bump_patch(m.group(1))
    if dry_run:
        return {"released": False, "reason": "dry_run", "would_bump": f"{cur} -> {new}",
                "since_tag": last or "(none)"}
    pyproj.write_text(_re.sub(r'^version = "[^"]+"', f'version = "{new}"', text, count=1,
                              flags=_re.M), encoding="utf-8")
    spa = repo / "core" / "console" / "corvin_console" / "web-next"
    subprocess.run(["npm", "run", "build"], cwd=spa, capture_output=True, text=True, timeout=900)
    import shutil as _sh
    for d in ("dist", "build"):
        _sh.rmtree(repo / d, ignore_errors=True)
    b = subprocess.run([sys.executable, "-m", "build"], cwd=repo,
                       capture_output=True, text=True, timeout=900)
    if b.returncode != 0:
        _git_q(repo, "checkout", "--", "pyproject.toml")  # revert bump on failure
        return {"released": False, "reason": "build failed", "detail": b.stderr[-300:]}
    arts = [str(p) for p in (repo / "dist").glob(f"corvinos-{new}*")]
    up = subprocess.run([sys.executable, "-m", "twine", "upload", "--non-interactive",
                         "-u", "__token__", "-p", token, *arts],
                        cwd=repo, capture_output=True, text=True, timeout=900)
    if up.returncode != 0:
        _git_q(repo, "checkout", "--", "pyproject.toml")
        return {"released": False, "reason": "upload failed", "detail": up.stderr[-300:]}
    _git_q(repo, "add", "pyproject.toml")
    _git_q(repo, "commit", "-m",
           f"chore(release): {new} — nightly auto-release (ADR-0178 maintainer-only)")
    _git_q(repo, "tag", f"v{new}")
    _git_q(repo, "push", "origin", "main", "--tags")
    return {"released": True, "version": new, "previous": cur}


def cmd_release(args) -> int:
    res = _release(Path(args.repo).resolve(), dry_run=bool(args.dry_run))
    print(json.dumps(res, indent=2))
    return 0 if (res.get("released") or res.get("reason") in
                 ("dry_run",) or "nothing new" in res.get("reason", "")) else 1


def cmd_nightly(args) -> int:
    """Process the diagnosis queue → one PR per actionable diagnosis. No-op (exit 0)
    when no capability or the queue is empty — never spams. Idempotent: skips a
    diagnosis whose branch already exists on origin."""
    from forge import paths as _paths  # type: ignore
    qroot = _paths.corvin_home() / "aco" / "diagnoses"
    pend, done, failed = qroot / "pending", qroot / "done", qroot / "failed"
    for d in (pend, done, failed):
        d.mkdir(parents=True, exist_ok=True)

    cap = MC.is_contributor()
    if not cap.allowed:
        print(json.dumps({"status": "noop", "reason": f"not a contributor: {cap.reason}"}))
        return 0  # not provisioned → quietly do nothing

    # Optionally mirror remote instances' logs (e.g. Hetzner) first, so the nerve
    # scan + any support bundle below cover them too. Runs even on an empty queue.
    remote_pull = None
    if getattr(args, "pull_remotes", False):
        try:
            from . import remote_logs as RL
            home = _paths.corvin_home()
            remote_pull = RL.pull_all(home)
            # surface remote health as a fresh nerve scan + bundle if anything is hot
            from .nerve import NerveRegistry
            sigs = [s for s in NerveRegistry.scan_all()
                    if getattr(s, "fiber_id", "") == "remote.log_health"]
            if sigs:
                from . import support_bundle as SB
                bz = SB.create_bundle(home, out_dir=qroot / "failed")
                remote_pull = {"pulled": remote_pull,
                               "remote_signals": [s.to_dict() for s in sigs],
                               "support_bundle": str(bz)}
        except Exception as exc:  # noqa: BLE001 — remote analysis is best-effort
            remote_pull = {"error": str(exc)[:160]}

    repo = Path(args.repo).resolve()
    items = sorted(pend.glob("*.json"))[: int(args.max)]
    if not items:
        out = {"status": "noop", "reason": "queue empty", "remote": remote_pull}
        if getattr(args, "release", False):
            out["release"] = _release(repo, dry_run=bool(args.dry_run))
        print(json.dumps(out))
        return 0

    test_cmd = (args.test_cmd.split() if args.test_cmd else
                [sys.executable, "-m", "pytest",
                 "core/console/tests/test_aco_repair_actions.py", "-q"])
    from .patch_generator import engine_patch_source, default_llm
    results = []
    for dj in items:
        try:
            diag = json.loads(dj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dj.rename(failed / dj.name)
            results.append({"file": dj.name, "status": "bad_json"})
            continue
        did = str(diag.get("id") or dj.stem)
        branch = f"aco/l6/{did}"
        if _remote_branch_exists(repo, branch):
            dj.rename(done / dj.name)
            results.append({"id": did, "status": "skip_exists"})
            continue
        import tempfile
        wt = Path(tempfile.mkdtemp(prefix="aco-nightly-")) / "wt"
        _git_q(repo, "worktree", "add", "--detach", str(wt), "HEAD")
        try:
            llm = default_llm(model=args.model)
            r = ML.run_maintenance_loop(
                diagnosis=diag, repo_dir=wt,
                patch_source=engine_patch_source(repo_dir=wt, llm=llm),
                capability_token=None,
                gate_runner=_pytest_gate(test_cmd, str(wt)))
            if r.status == "pr_ready" and not args.dry_run:
                _git_q(wt, "push", "-u", "origin", branch)
                pr = subprocess.run(
                    ["gh", "pr", "create", "--repo", args.gh_repo, "--base", "main",
                     "--head", branch, "--title", f"fix(aco-l6): {diag.get('summary', did)}"[:120],
                     "--body", _pr_body(diag, r)], capture_output=True, text=True)
                dj.rename(done / dj.name)
                results.append({"id": did, "status": "pr_opened",
                                "pr": pr.stdout.strip() or pr.stderr.strip()[:200]})
            elif r.status == "pr_ready":
                results.append({"id": did, "status": "pr_ready_dryrun",
                                "branch": branch, "commit": r.commit})
            else:
                dj.rename(failed / dj.name)
                results.append({"id": did, "status": r.status, "detail": r.detail[:200]})
        finally:
            _git_q(repo, "worktree", "remove", "--force", str(wt))
            _git_q(repo, "worktree", "prune")

    out = {"status": "done", "processed": len(results), "results": results,
           "remote": remote_pull}
    # On any non-success outcome, attach a fresh support bundle so the maintainer
    # has the full debug material for the failed run without asking the user.
    _ok = {"pr_opened", "pr_ready_dryrun", "skip_exists"}
    if any(r.get("status") not in _ok for r in results):
        try:
            from . import support_bundle as SB
            out["support_bundle"] = str(SB.create_bundle(_paths.corvin_home(),
                                                         out_dir=qroot / "failed"))
        except Exception as exc:  # noqa: BLE001 — bundle is a convenience, never fatal
            out["support_bundle_error"] = str(exc)[:120]
    # Auto-publish the next PyPI version IFF main advanced since the last tag
    # (maintainer-only: capability + PYPI_TOKEN double-gate).
    if getattr(args, "release", False):
        out["release"] = _release(repo, dry_run=bool(args.dry_run))
    print(json.dumps(out, indent=2))
    try:
        with (qroot / "nightly.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(out) + "\n")
    except OSError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="corvin-maintainer",
                                description="ADR-0178 Tier-CONTRIBUTOR maintainer CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("keygen", help="generate an Ed25519 maintainer keypair")
    g.add_argument("--out-priv", help="write the private key to this 0600 file")
    g.set_defaults(fn=cmd_keygen)

    i = sub.add_parser("issue", help="mint a maintainer.commit capability token")
    i.add_argument("--priv-file", help="file holding the base64 private key")
    i.add_argument("--instance", help="instance_id to bind (default: current)")
    i.add_argument("--subject", default="shumway")
    i.add_argument("--ttl-days", default="90")
    i.set_defaults(fn=cmd_issue)

    v = sub.add_parser("verify", help="check the pinned key + token in the env")
    v.set_defaults(fn=cmd_verify)

    b = sub.add_parser("bundle", help="zip all logs/healing into one sendable folder")
    b.add_argument("--out", help="output dir (default: <corvin_home>/aco/support-bundles)")
    b.set_defaults(fn=cmd_bundle)

    r = sub.add_parser("run", help="drive one L6 maintenance-loop iteration")
    r.add_argument("--repo", required=True, help="repo working dir to operate on")
    r.add_argument("--patch", help="patch spec JSON (edits) — file-based source")
    r.add_argument("--auto", action="store_true",
                   help="engine generates the patch from the diagnosis (always ack-gated)")
    r.add_argument("--model", help="engine model for --auto")
    r.add_argument("--diagnosis", help="diagnosis JSON (required for --auto)")
    r.add_argument("--test-cmd", help="override the gate test command")
    r.add_argument("--direct-main", action="store_true",
                   help="allow ff-merge to main for low-risk+green (default: PR only)")
    r.add_argument("--push", action="store_true", help="push origin main (implies risk)")
    r.set_defaults(fn=cmd_run)

    n = sub.add_parser("nightly", help="process the diagnosis queue → one PR each")
    n.add_argument("--repo", required=True, help="maintainer repo working dir")
    n.add_argument("--gh-repo", default="CorvinLabs/CorvinOS", help="owner/name for gh pr")
    n.add_argument("--max", default="3", help="max diagnoses to process per run")
    n.add_argument("--model", help="engine model")
    n.add_argument("--test-cmd", help="override the gate test command")
    n.add_argument("--dry-run", action="store_true", help="run loop but don't push/PR")
    n.add_argument("--release", action="store_true",
                   help="auto-publish next PyPI version if main advanced (maintainer-only)")
    n.add_argument("--pull-remotes", action="store_true",
                   help="mirror + analyse configured remote instances' logs first")
    n.set_defaults(fn=cmd_nightly)

    rel = sub.add_parser("release", help="publish next PyPI version if main advanced (maintainer-only)")
    rel.add_argument("--repo", required=True, help="repo working dir")
    rel.add_argument("--dry-run", action="store_true", help="report would-bump, don't publish")
    rel.set_defaults(fn=cmd_release)

    rr = sub.add_parser("remotes", help="manage remote-instance log sources")
    rr.add_argument("action", choices=["list", "add"])
    rr.add_argument("--name"); rr.add_argument("--ssh")
    rr.add_argument("--remote-home", default=".corvin")
    rr.set_defaults(fn=cmd_remotes)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
