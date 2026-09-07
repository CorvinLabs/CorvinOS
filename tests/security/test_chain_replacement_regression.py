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


_ROTATE = r'''
import os, sys, json
for sub in ("operator/forge", "operator/bridges/shared"):
    sys.path.insert(0, os.path.join(os.environ["REPO"], sub))
from pathlib import Path
import audit_sealer as S
p = Path(os.environ["VOICE_AUDIT_PATH"])
pol = S.AuditPolicy(rotation=S.RotationPolicy(), encryption=S.EncryptionConfig(),
                    retention=S.RetentionPolicy())
r = S.rotate_and_seal(p, pol)
print("ROTATED " + json.dumps({"rotated": r.rotated, "tail": r.last_hash}))
'''


def _rotate(home: Path) -> dict:
    out = _run(_ROTATE, home)
    line = next((l for l in out.splitlines() if l.startswith("ROTATED ")), None)
    assert line, out[-3000:]
    return json.loads(line[len("ROTATED "):])


def _forge_rotation_link(path: Path, prev: str, *, mac: str | None = None) -> dict:
    """A rotation_link an in-tree attacker can build: correct shape, correct
    hash, binding to ``prev`` — and no anchor-key MAC, because they have no key."""
    link = {"ts": 2.0, "event_type": "audit.rotation_link", "severity": "INFO",
            "run_id": "", "tool": "audit_sealer",
            "details": {"rotated_segment": "audit.20260907T000000Z.jsonl"},
            "prev_hash": prev}
    canon = json.dumps(link, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(prev.encode()); h.update(b"\n"); h.update(canon.encode())
    link["hash"] = h.hexdigest()[:16]
    if mac is not None:
        link["mac"] = mac
    path.write_text(json.dumps(link) + "\n", encoding="utf-8")
    return link


class TestLegitimateRotationStaysClean:
    def test_a_real_sealer_rotation_is_recognised(self, chain):
        """Driven through the REAL ``audit_sealer.rotate_and_seal()`` — the only
        thing that may re-anchor a chain identity. It calls ``note_chain_rotation``
        under the rotation lock and MACs the link under the anchor key, and those
        two out-of-tree facts are what the verifier consults (R3-A1)."""
        home, path = chain
        assert _rotate(home)["rotated"] is True
        link = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert link["event_type"] == "audit.rotation_link"
        assert link.get("mac"), "the sealer must MAC the rotation link"
        result = _verify(home)
        # A bare verify of a freshly rotated LIVE segment reports ``broken_chain``
        # for its first record by design — the segment's ``prev_hash`` points at
        # the rotated segment's tail, and the cross-segment check passes that in
        # as ``initial_prev`` (ADR-0044 / Layer 37, what ``voice-audit
        # --include-sealed`` does). That is unrelated to this finding.
        for issue in ("chain_replaced", "tail_truncated", "mac_stripped_chain",
                      "unanchored_genesis"):
            assert issue not in result["issues"], (issue, result)

    def test_the_boot_survives_a_real_rotation(self, chain):
        home, _ = chain
        assert _rotate(home)["rotated"] is True
        out = _run(_TRIPWIRE, home)
        assert "TRIPWIRE_PASSED" in out, out[-3000:]


class TestForgedRotationLink:
    """R3-A1. Round 2 recognised a rotation by the SHAPE of record 1: a
    ``audit.rotation_link`` whose ``prev_hash`` equalled the tail in the
    out-of-tree path record. The tail in that record is the hash of the last
    chained record — byte-identical to the last ``hash`` IN THE FILE the attacker
    is rewriting. So the "a rewriter cannot read the path record" argument was
    false: the attacker reads the tail straight out of audit.jsonl and mints a
    genesis that binds to it, suppressing ``chain_replaced`` + ``tail_truncated``
    + ``mac_stripped_chain`` at once."""

    @staticmethod
    def _forge_whole_chain(path: Path, n: int) -> str:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        tail = [json.loads(l)["hash"] for l in lines if json.loads(l).get("hash")][-1]
        link = {"ts": 2.0, "event_type": "audit.rotation_link", "severity": "INFO",
                "run_id": "", "tool": "audit_sealer",
                "details": {"rotated_segment": "audit.20260907T000000Z.jsonl"},
                "prev_hash": tail}

        def _chained(rec: dict, prev: str) -> dict:
            rec = dict(rec, prev_hash=prev)
            canon = json.dumps({k: v for k, v in rec.items() if k != "hash"},
                               sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256()
            h.update(prev.encode()); h.update(b"\n"); h.update(canon.encode())
            rec["hash"] = h.hexdigest()[:16]
            return rec

        out = [_chained(link, tail)]
        prev = out[0]["hash"]
        for i in range(n):
            rec = _chained({"ts": 3.0 + i, "event_type": "consent.granted",
                            "severity": "INFO", "run_id": "", "tool": "",
                            "details": {"granted_by": "attacker"}}, prev)
            out.append(rec)
            prev = rec["hash"]
        path.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
        return tail

    def test_forged_rotation_link_is_not_mistaken_for_a_rotation(self, chain):
        home, path = chain
        self._forge_whole_chain(path, 4)
        result = _verify(home)
        assert not result["ok"], result
        assert "chain_replaced" in result["issues"], result
        assert "unanchored_genesis" in result["issues"], result

    def test_padding_past_the_tail_window_does_not_launder_it(self, chain):
        """The attacker used to control the ONLY residual signal's classification:
        a line-1 ``broken_chain`` is "historical" once the file exceeds
        ``TAIL_RECORDS`` (200), and the attacker chooses the file's length. The
        genesis verdict is now LINE-LESS, so length is irrelevant."""
        home, path = chain
        self._forge_whole_chain(path, 250)
        assert len(path.read_text(encoding="utf-8").splitlines()) > 200
        result = _verify(home)
        assert not result["ok"], result
        assert "unanchored_genesis" in result["issues"], result
        out = _run(_TRIPWIRE, home)
        assert "TRIPWIRE_REFUSED" in out, out[-3000:]
        assert "unanchored_genesis" in out, out[-3000:]

    def test_a_link_carrying_a_bogus_mac_is_still_refused(self, chain):
        """The MAC is verified, not merely required."""
        home, path = chain
        lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        tail = [r["hash"] for r in lines if r.get("hash")][-1]
        _forge_rotation_link(path, tail, mac="f" * 16)
        result = _verify(home)
        assert not result["ok"], result
        assert "unanchored_genesis" in result["issues"], result

    def test_a_forged_link_after_a_real_rotation_is_still_refused(self, chain):
        """``rotation_genesis`` names ONE genesis. A second, self-minted "rotation"
        on top of a real one does not inherit it."""
        home, path = chain
        assert _rotate(home)["rotated"] is True
        real = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        _forge_rotation_link(path, real["hash"])
        result = _verify(home)
        assert not result["ok"], result
        assert "unanchored_genesis" in result["issues"], result


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


class TestChainIdentityFollowsSymlinks:
    """R3-A3. ``_chain_path_record_path`` keyed the identity record on
    ``os.path.abspath``, which does NOT follow symlinks, while
    ``tripwire._current_chain_key`` keys on ``.resolve()``, which does. One
    physical chain reached through the documented compat symlink
    (``<corvin_home>/global`` → ``tenants/_default/global``) therefore hashed to
    a different key, and the aliasing reader saw NO identity record at all: no
    ``chain_replaced``, no ``records_prepended``, no path-keyed tail. Both
    readers must land on the same record."""

    @pytest.fixture
    def aliased(self, tmp_path):
        home = tmp_path / "home"
        (home / "keys").mkdir(parents=True)
        real = home / "tenants" / "_default" / "global" / "forge"
        real.mkdir(parents=True)
        (home / "global").symlink_to(Path("tenants") / "_default" / "global")
        env_path = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        return home, env_path, home / "global" / "forge" / "audit.jsonl"

    def _seed(self, home: Path, path: Path) -> None:
        env = _env(home)
        env["VOICE_AUDIT_PATH"] = str(path)
        proc = subprocess.run([sys.executable, "-c", _SEED], env=env, cwd=str(_REPO),
                              capture_output=True, text=True, timeout=300)
        assert "SEEDED" in proc.stdout + proc.stderr, (proc.stdout + proc.stderr)[-3000:]

    def _verify_as(self, home: Path, path: Path) -> dict:
        env = _env(home)
        env["VOICE_AUDIT_PATH"] = str(path)
        proc = subprocess.run([sys.executable, "-c", _VERIFY], env=env, cwd=str(_REPO),
                              capture_output=True, text=True, timeout=300)
        out = proc.stdout + proc.stderr
        line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
        assert line, out[-3000:]
        return json.loads(line[len("RESULT "):])

    def test_a_rewrite_seen_through_the_symlink_is_still_caught(self, aliased):
        home, real, link = aliased
        self._seed(home, real)
        assert self._verify_as(home, real) == {"ok": True, "issues": []}
        # Same physical file, reached through the compat symlink.
        assert self._verify_as(home, link) == {"ok": True, "issues": []}
        _rewrite_whole_file(real)
        assert "chain_replaced" in self._verify_as(home, link)["issues"]

    def test_the_record_is_written_once_for_both_names(self, aliased):
        home, real, link = aliased
        self._seed(home, real)
        self._seed(home, link)
        records = sorted(x.name for x in (home / "keys" / "chain_ids").iterdir())
        assert len(records) == 1, records
