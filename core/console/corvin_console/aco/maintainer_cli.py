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
    items = sorted(pend.glob("*.json"))[: int(args.max)]
    if not items:
        print(json.dumps({"status": "noop", "reason": "queue empty"}))
        return 0

    repo = Path(args.repo).resolve()
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

    out = {"status": "done", "processed": len(results), "results": results}
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
    n.set_defaults(fn=cmd_nightly)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
