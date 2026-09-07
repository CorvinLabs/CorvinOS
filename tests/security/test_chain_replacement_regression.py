"""R2-A1 / R2-A2: a whole-file rewrite and a prepended prefix must be caught.

Round 1 anchored the chain's tail and its "this chain carries a MAC" marker by
its GENESIS hash. Both therefore answered questions about *the chain that hashes
to G* — and said nothing about *the file that lives at this path*. An attacker
who rewrites ``audit.jsonl`` end to end (fresh records, recomputed hashes, every
``mac`` dropped) mints a NEW genesis, so no genesis-keyed marker exists to
contradict it: the file self-verifies and the boot tripwire accepts it.

Round 2 adds a PATH-keyed identity record beside the anchor key
(``chain_ids/<sha256(abspath)>`` → genesis + tail + legacy-prefix length + mac
flag) and, for the primary live layout, consults the host sentinel
``audit_mac_active``. A file whose genesis is not the anchored one is
``chain_replaced``; hash-less records inserted before the genesis are
``records_prepended``; a Layer 37 rotation stays clean because its
``audit.rotation_link`` binds to the out-of-tree recorded tail.

EVERY verification below runs in a FRESH SUBPROCESS. In-process,
``_GENESIS_CACHE`` is warm from the seeding writes and masks the swap — which is
exactly how the reviewer demonstrated the hole, and why an in-process assertion
here would pass without proving anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

_SEED = r'''
import os, sys
sys.path.insert(0, os.path.join(os.environ["REPO"], "operator", "forge"))
from pathlib import Path
from forge import security_events as se
p = Path(os.environ["VOICE_AUDIT_PATH"])
for i in range(int(os.environ.get("N", "5"))):
    se.write_event(p, "test.event", details={"count": i})
print("SEEDED")
'''

_VERIFY = r'''
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["REPO"], "operator", "forge"))
from pathlib import Path
from forge import security_events as se
p = Path(os.environ["VOICE_AUDIT_PATH"])
ok, problems = se.verify_chain(p)
print("RESULT " + json.dumps({"ok": ok, "issues": sorted({str(x.get("issue")) for x in problems})}))
'''

_TRIPWIRE = r'''
import os, sys
for sub in ("operator/forge", "operator/bridges/shared", "core/compliance"):
    sys.path.append(os.path.join(os.environ["REPO"], sub))
from corvin_compliance_reports import tripwire
try:
    tripwire.assert_all()
    print("TRIPWIRE_PASSED")
except tripwire.TripwireError as exc:
    print("TRIPWIRE_REFUSED " + str(exc)[:300])
'''


def _env(home: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "REPO": str(_REPO),
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        # The chain AND the anchor key both live under tmp, so the out-of-tree
        # markers are really written (they are skipped only when a tmp chain
        # would litter the operator's real key directory) and the operator's
        # own ~/.config/corvin-voice is never touched.
        "VOICE_AUDIT_PATH": str(home / "global" / "forge" / "audit.jsonl"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "keys" / "audit_anchor.key"),
    })
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def _run(script: str, home: Path) -> str:
    proc = subprocess.run([sys.executable, "-c", script], env=_env(home),
                          cwd=str(_REPO), capture_output=True, text=True, timeout=300)
    return proc.stdout + "\n" + proc.stderr


def _verify(home: Path) -> dict:
    out = _run(_VERIFY, home)
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    assert line, out[-3000:]
    return json.loads(line[len("RESULT "):])


@pytest.fixture
def chain(tmp_path):
    """A seeded 5-record chain, written by a real writer in its own process."""
    home = tmp_path / "home"
    (home / "keys").mkdir(parents=True)
    out = _run(_SEED, home)
    assert "SEEDED" in out, out[-3000:]
    path = home / "global" / "forge" / "audit.jsonl"
    assert path.exists()
    assert _verify(home) == {"ok": True, "issues": []}
    return home, path


def _rewrite_whole_file(path: Path, n: int = 4) -> None:
    """The reviewer's attack: a brand-new, internally consistent chain.

    Fresh records, hashes recomputed from an empty prev, no ``mac`` anywhere —
    the file is self-consistent, so nothing INSIDE it can expose the swap.
    """
    prev = ""
    lines = []
    for i in range(n):
        rec = {"ts": 1.0 + i, "event_type": "consent.granted", "severity": "INFO",
               "run_id": "", "tool": "", "details": {"granted_by": "attacker"},
               "prev_hash": prev}
        canon = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256()
        h.update(prev.encode()); h.update(b"\n"); h.update(canon.encode())
        rec["hash"] = h.hexdigest()[:16]
        prev = rec["hash"]
        lines.append(json.dumps(rec))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestWholeFileReplacement:
    def test_rewritten_chain_is_reported_as_chain_replaced(self, chain):
        home, path = chain
        _rewrite_whole_file(path)
        result = _verify(home)
        assert not result["ok"], result
        assert "chain_replaced" in result["issues"], result

    def test_rewritten_chain_refuses_the_boot(self, chain):
        """The tripwire must REFUSE, not merely report (R2-A1)."""
        home, path = chain
        _rewrite_whole_file(path)
        out = _run(_TRIPWIRE, home)
        assert "TRIPWIRE_REFUSED" in out, out[-3000:]
        assert "audit_chain_intact" in out, out[-3000:]

    def test_a_self_consistent_rewrite_would_pass_without_the_path_record(self, chain):
        """Pins WHY the path record is needed: with it deleted the forged file
        verifies clean, which is the round-1 behaviour this finding reports."""
        home, path = chain
        _rewrite_whole_file(path)
        removed = 0
        for marker in (home / "keys").rglob("*"):
            if marker.is_file() and marker.parent.name in ("chain_ids", "chain_tails",
                                                           "mac_active_chains"):
                marker.unlink(); removed += 1
        sentinel = home / "keys" / "audit_mac_active"
        if sentinel.exists():
            sentinel.unlink(); removed += 1
        assert removed, "expected out-of-tree markers beside the anchor key"
        assert _verify(home) == {"ok": True, "issues": []}


class TestPrependedRecords:
    def test_hashless_records_prepended_before_the_genesis_are_flagged(self, chain):
        """R2-A2: the genesis' ``prev_hash`` is "" and binds to nothing, so a
        hash-less prefix cannot be caught by the walk — only by the recorded
        prefix LENGTH and by the fact that the genesis carries a mac."""
        home, path = chain
        forged = {"ts": 1.0, "event_type": "consent.granted", "severity": "INFO",
                  "run_id": "", "tool": "", "details": {"granted_by": "attacker"}}
        path.write_text(json.dumps(forged) + "\n" + path.read_text(encoding="utf-8"),
                        encoding="utf-8")
        result = _verify(home)
        assert not result["ok"], result
        assert "records_prepended" in result["issues"], result

    def test_a_genuine_legacy_prefix_is_still_tolerated(self, tmp_path):
        """The counter-case that bounds the rule, and it is not hypothetical.

        A pre-hash-chain install really does carry hash-less records, and its
        FIRST chained record is written today — so that genesis carries a mac.
        "Genesis has a mac ⇒ there can be no prefix" would therefore condemn
        every migrated legacy chain (the shape
        ``operator/forge/tests/test_tenant_migration_roundtrip.py`` R5 builds).
        The discriminator is the recorded prefix LENGTH, frozen at anchoring —
        so a prefix that was there from the start verifies clean.
        """
        home = tmp_path / "home"
        (home / "keys").mkdir(parents=True)
        chain_path = home / "global" / "forge" / "audit.jsonl"
        chain_path.parent.mkdir(parents=True)
        chain_path.write_text('{"event":"pre-seed-legacy"}\n', encoding="utf-8")
        assert "SEEDED" in _run(_SEED, home)
        assert _verify(home) == {"ok": True, "issues": []}


class TestLegitimateRotationStaysClean:
    def test_layer37_rotation_does_not_read_as_a_replacement(self, chain):
        """A rotation legitimately changes the genesis. It is told apart from a
        replacement by the ``audit.rotation_link`` binding to the tail recorded
        OUT of tree — which a rewriter who can only edit audit.jsonl cannot read.
        Mirrors ``audit_sealer.rotate()``'s link record exactly."""
        home, path = chain
        tail = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                tail = json.loads(line)["hash"]
        path.rename(path.with_name("audit.20260907T000000Z.jsonl"))
        link = {"ts": 2.0, "event_type": "audit.rotation_link", "severity": "INFO",
                "run_id": "", "tool": "audit_sealer",
                "details": {"rotated_segment": "audit.20260907T000000Z.jsonl"},
                "prev_hash": tail}
        canon = json.dumps(link, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256()
        h.update(tail.encode()); h.update(b"\n"); h.update(canon.encode())
        link["hash"] = h.hexdigest()[:16]
        path.write_text(json.dumps(link) + "\n", encoding="utf-8")

        result = _verify(home)
        # A bare verify of a freshly rotated LIVE segment reports ``broken_chain``
        # for its first record by design — the segment's ``prev_hash`` points at
        # the rotated segment's tail, and the cross-segment check passes that in
        # as ``initial_prev`` (ADR-0044 / Layer 37, what ``voice-audit
        # --include-sealed`` does). That is unrelated to this finding. What must
        # NOT appear is a replacement, a truncation or a strip verdict.
        assert "chain_replaced" not in result["issues"], result
        assert "tail_truncated" not in result["issues"], result
        assert "mac_stripped_chain" not in result["issues"], result
        # …and a rewrite that merely CLAIMS to be a rotation, without the
        # recorded tail, is still caught.
        bogus = dict(link, prev_hash="0" * 16)
        canon = json.dumps({k: v for k, v in bogus.items() if k != "hash"},
                           sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256()
        h.update(b"0" * 16); h.update(b"\n"); h.update(canon.encode())
        bogus["hash"] = h.hexdigest()[:16]
        path.write_text(json.dumps(bogus) + "\n", encoding="utf-8")
        result = _verify(home)
        assert not result["ok"], result
        assert "chain_replaced" in result["issues"], result


class TestTailTruncationSurvivesAGenesisSwap:
    def test_truncation_is_caught_by_the_path_keyed_tail(self, chain):
        """The genesis-keyed tail anchor is unreachable once the genesis moves;
        the path-keyed one still names the record that is gone."""
        home, path = chain
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
        result = _verify(home)
        assert not result["ok"], result
        assert "tail_truncated" in result["issues"], result
