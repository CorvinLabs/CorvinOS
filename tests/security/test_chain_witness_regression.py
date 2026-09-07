"""ADR-0640 R4: the boot-path prefix witness must never hide a break.

``audit_chain_history_clean`` walks the WHOLE chain on every host boot. On the
maintainer install that is 315 MB / 588 827 records / ~5.7 s per walk, paid twice
per boot and paid again by every test that boots the console app — and the cost
is O(n) in a file that only ever grows.

``security_events.verify_chain_incremental`` memoises the already-verified
PREFIX of the append-only chain. The witness lives beside the anchor key
(``chain_witness/<sha256(realpath)>``, 0600, MAC'd under the anchor key) and is
admitted only after the prefix BYTES are re-hashed in the same call and match the
recorded SHA-256. That byte digest — not a record count, not the tail hash, not
an mtime — is the whole security argument, and the tests below are what pin it:

* a record edited in the middle of the memoised prefix (the ``tampered`` /
  ``mac_tampered`` class, which leaves the hash at the prefix's END untouched)
  is still reported;
* a truncation, a whole-file replacement and a prepend are still reported;
* a fresh process with no witness verifies everything;
* a witness whose MAC does not verify, or that was written under a different
  anchor key, is ignored — never trusted;
* the boot still refuses on a current-state problem with a witness present.

EVERY verification runs in a FRESH SUBPROCESS. In-process the genesis cache and
the tripwire's own per-file cache are warm and would mask exactly what these
tests are for — the same reason the round-3 replacement regressions subprocess.
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
for i in range(int(os.environ.get("N", "40"))):
    se.write_event(p, "test.event", details={"count": i})
print("SEEDED")
'''

#: Runs the BOOT verifier (witness-aware) and prints both its verdict and the
#: verdict of an unconditional full walk, so every assertion can also pin that
#: the two agree — a memoised answer that differs from the full walk is the
#: failure mode this whole file exists to exclude.
_VERIFY = r'''
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["REPO"], "operator", "forge"))
from pathlib import Path
from forge import security_events as se
p = Path(os.environ["VOICE_AUDIT_PATH"])
ok_i, probs_i, total = se.verify_chain_incremental(p)
ok_f, probs_f = se.verify_chain(p)
def norm(probs):
    return sorted((str(x.get("issue")), x.get("line")) for x in probs)
print("RESULT " + json.dumps({
    "ok": ok_i, "total": total,
    "issues": sorted({str(x.get("issue")) for x in probs_i}),
    "problems": norm(probs_i),
    "agrees_with_full_walk": norm(probs_i) == norm(probs_f) and ok_i == ok_f,
}))
'''

_WITNESS = r'''
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["REPO"], "operator", "forge"))
from pathlib import Path
from forge import security_events as se
p = Path(os.environ["VOICE_AUDIT_PATH"])
wp = se._chain_witness_path(p)
body, hasher = se._read_chain_witness(p, initial_prev="")
print("WITNESS " + json.dumps({
    "path": str(wp), "exists": wp.exists(),
    # "admitted" is the proof that the memoised prefix is actually REUSED.
    # Without it every assertion below would also pass on a witness that always
    # falls back to a full walk, i.e. on a mechanism that does nothing.
    "admitted": body is not None,
    "prefix_bytes": int(body.get("bytes", 0)) if body else 0,
}))
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
    print("TRIPWIRE_REFUSED " + str(exc)[:400].replace("\n", " "))
'''


def _env(home: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "REPO": str(_REPO),
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        # Chain AND anchor key both under tmp, so the out-of-tree markers (the
        # witness among them) are really written and the operator's real
        # ~/.config/corvin-voice is never touched.
        "VOICE_AUDIT_PATH": str(home / "global" / "forge" / "audit.jsonl"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "keys" / "audit_anchor.key"),
    })
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def _run(script: str, home: Path) -> str:
    proc = subprocess.run([sys.executable, "-c", script], env=_env(home),
                          cwd=str(_REPO), capture_output=True, text=True, timeout=300)
    return proc.stdout + "\n" + proc.stderr


def _tagged(script: str, home: Path, tag: str) -> dict:
    out = _run(script, home)
    line = next((l for l in out.splitlines() if l.startswith(tag + " ")), None)
    assert line, out[-3000:]
    return json.loads(line[len(tag) + 1:])


def _verify(home: Path) -> dict:
    return _tagged(_VERIFY, home, "RESULT")


def _witness(home: Path) -> dict:
    return _tagged(_WITNESS, home, "WITNESS")


@pytest.fixture
def chain(tmp_path):
    """A seeded 40-record chain plus ONE boot-path verify, so a witness exists.

    Every test below therefore starts from the state that matters: a durable
    witness on disk claiming the whole current file has been verified clean.
    """
    home = tmp_path / "home"
    (home / "keys").mkdir(parents=True)
    assert "SEEDED" in _run(_SEED, home)
    path = home / "global" / "forge" / "audit.jsonl"
    first = _verify(home)
    assert first["ok"] and first["issues"] == [], first
    assert first["agrees_with_full_walk"], first
    w = _witness(home)
    assert w["exists"], "the boot verifier must leave a witness"
    assert w["admitted"], "and that witness must be admitted on the next verify"
    return home, path


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestNoWitness:
    def test_a_fresh_process_with_no_witness_verifies_everything(self, chain):
        """The cold path: delete the witness, corrupt an early record, verify."""
        home, path = chain
        Path(_witness(home)["path"]).unlink()
        lines = _lines(path)
        rec = json.loads(lines[3])
        rec["details"]["count"] = 999999
        lines[3] = json.dumps(rec)
        _rewrite(path, lines)

        result = _verify(home)
        assert not result["ok"], result
        assert "tampered" in result["issues"], result
        assert (("tampered", 4) in [tuple(p) for p in result["problems"]]), result
        assert result["agrees_with_full_walk"], result


class TestBreakInsideTheMemoisedPrefix:
    def test_an_edited_record_in_the_prefix_is_still_reported(self, chain):
        """The attack the byte digest exists for.

        Line 4 is deep inside the memoised prefix, and editing it WITHOUT
        rehashing forward leaves the chain hash at the END of the prefix — the
        obvious thing to witness — completely untouched. Only a digest over the
        prefix BYTES notices, and it must, because ``tampered`` is precisely the
        class ``audit_chain_history_clean`` exists to keep visible.
        """
        home, path = chain
        before = _lines(path)
        rec = json.loads(before[3])
        rec["details"]["count"] = 999999
        after = list(before)
        after[3] = json.dumps(rec)
        _rewrite(path, after)

        assert not _witness(home)["admitted"], (
            "an edited prefix must miss the byte digest and force a full walk")
        result = _verify(home)
        assert not result["ok"], result
        assert "tampered" in result["issues"], result
        assert ("tampered", 4) in [tuple(p) for p in result["problems"]], result
        assert result["agrees_with_full_walk"], result

    def test_a_same_length_edit_is_still_reported(self, chain):
        """Same as above but byte-length preserving, so the file SIZE is
        unchanged too — a size-and-tail witness would sail straight past it."""
        home, path = chain
        lines = _lines(path)
        target = lines[5]
        rec = json.loads(target)
        old = str(rec["details"]["count"])
        rec["details"]["count"] = int("9" * len(old))
        edited = json.dumps(rec)
        assert len(edited) == len(target), (len(edited), len(target))
        lines[5] = edited
        size_before = path.stat().st_size
        _rewrite(path, lines)
        assert path.stat().st_size == size_before
        assert not _witness(home)["admitted"], (
            "a same-LENGTH edit must still miss the byte digest")

        result = _verify(home)
        assert not result["ok"], result
        assert "tampered" in result["issues"], result
        assert result["agrees_with_full_walk"], result

    def test_a_stripped_mac_in_the_prefix_is_still_reported(self, chain):
        """MAC-strip inside the prefix: the record's ``hash`` is computed
        WITHOUT ``mac``, so dropping it keeps the chain arithmetic perfect."""
        home, path = chain
        lines = _lines(path)
        rec = json.loads(lines[6])
        assert rec.pop("mac", None), "seed records must carry a mac"
        lines[6] = json.dumps(rec)
        _rewrite(path, lines)

        result = _verify(home)
        assert not result["ok"], result
        assert "mac_missing" in result["issues"], result
        assert result["agrees_with_full_walk"], result


class TestStructuralAttacksWithAWitnessPresent:
    """The round-3 shapes, re-run with a durable witness already on disk."""

    def test_truncation_is_reported(self, chain):
        home, path = chain
        lines = _lines(path)
        _rewrite(path, lines[:-5])
        result = _verify(home)
        assert not result["ok"], result
        assert "tail_truncated" in result["issues"], result
        assert result["agrees_with_full_walk"], result

    def test_whole_file_replacement_is_reported(self, chain):
        home, path = chain
        prev = ""
        forged = []
        for i in range(60):  # deliberately LONGER than the witnessed prefix
            rec = {"ts": 1.0 + i, "event_type": "consent.granted", "severity": "INFO",
                   "run_id": "", "tool": "", "details": {"granted_by": "attacker"},
                   "prev_hash": prev}
            canon = json.dumps(rec, sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256()
            h.update(prev.encode()); h.update(b"\n"); h.update(canon.encode())
            rec["hash"] = h.hexdigest()[:16]
            prev = rec["hash"]
            forged.append(json.dumps(rec))
        _rewrite(path, forged)

        result = _verify(home)
        assert not result["ok"], result
        assert "chain_replaced" in result["issues"], result
        assert result["agrees_with_full_walk"], result

    def test_prepended_records_are_reported(self, chain):
        home, path = chain
        forged = {"ts": 1.0, "event_type": "consent.granted", "severity": "INFO",
                  "run_id": "", "tool": "", "details": {"granted_by": "attacker"}}
        path.write_text(json.dumps(forged) + "\n" + path.read_text(encoding="utf-8"),
                        encoding="utf-8")
        result = _verify(home)
        assert not result["ok"], result
        assert "records_prepended" in result["issues"], result
        assert result["agrees_with_full_walk"], result


class TestWitnessIsNeverTrustedOnItsOwn:
    def test_a_witness_claiming_a_clean_prefix_over_a_broken_file_is_ignored(self, chain):
        """The witness is a cache, not an authority.

        Forge the strongest possible witness: keep its own MAC valid by
        re-MACing it under the anchor key (an attacker who reaches the key
        directory has the key), but leave ``digest`` naming the OLD bytes.
        The digest re-computed this call is what decides, so the break wins.
        """
        home, path = chain
        lines = _lines(path)
        rec = json.loads(lines[7])
        rec["details"]["count"] = 424242
        lines[7] = json.dumps(rec)
        _rewrite(path, lines)

        forge_witness = r'''
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["REPO"], "operator", "forge"))
from pathlib import Path
from forge import security_events as se
p = Path(os.environ["VOICE_AUDIT_PATH"])
wp = se._chain_witness_path(p)
doc = json.loads(wp.read_text())
body = doc["body"]
body["problems"] = []          # "nothing was ever wrong here"
body["bytes"] = p.stat().st_size
body["lines"] = sum(1 for _ in p.open("rb"))
doc = {"body": body, "mac": se._witness_mac(se._canonical(body))}
wp.write_text(json.dumps(doc))
print("FORGED")
'''
        assert "FORGED" in _run(forge_witness, home)

        result = _verify(home)
        assert not result["ok"], result
        assert "tampered" in result["issues"], result
        assert result["agrees_with_full_walk"], result

    def test_a_witness_with_a_broken_mac_is_ignored(self, chain):
        home, path = chain
        wp = Path(_witness(home)["path"])
        doc = json.loads(wp.read_text())
        doc["mac"] = "0" * 32
        wp.write_text(json.dumps(doc))

        lines = _lines(path)
        rec = json.loads(lines[8])
        rec["details"]["count"] = 777
        lines[8] = json.dumps(rec)
        _rewrite(path, lines)

        result = _verify(home)
        assert not result["ok"], result
        assert "tampered" in result["issues"], result
        assert result["agrees_with_full_walk"], result

    def test_a_witness_from_a_different_anchor_key_is_ignored(self, chain):
        """Every ``mac_*`` verdict in the prefix depends on the key, so a
        rotated key must invalidate the witness rather than freeze stale
        verdicts. With a fresh key the old macs stop verifying — the full walk
        says so, and the witness-aware verify must say exactly the same."""
        home, path = chain
        (home / "keys" / "audit_anchor.key").write_bytes(os.urandom(32))
        os.chmod(home / "keys" / "audit_anchor.key", 0o600)
        result = _verify(home)
        assert not result["ok"], result
        assert "mac_tampered" in result["issues"], result
        assert result["agrees_with_full_walk"], result

    def test_a_witness_is_not_reused_across_paths(self, chain, tmp_path):
        """The witness is keyed on the RESOLVED chain path (R3-A3), so moving a
        forged file to a different path cannot inherit another chain's verdict."""
        home, path = chain
        other = tmp_path / "other"
        (other / "keys").mkdir(parents=True)
        (other / "global" / "forge").mkdir(parents=True)
        import shutil
        shutil.copy2(home / "keys" / "audit_anchor.key", other / "keys" / "audit_anchor.key")
        shutil.copy2(path, other / "global" / "forge" / "audit.jsonl")
        assert not _witness(other)["exists"]


class TestGrowth:
    def test_appending_reuses_the_witness_and_still_reports_the_prefix_break(self, chain):
        """The point of the whole mechanism, and its sharpest edge.

        A break is introduced in the prefix, one boot records it in the witness,
        then the chain GROWS. The next boot resumes from the witness — and the
        historical break must still be reported, at the same line, with the same
        verdict a full walk gives.
        """
        home, path = chain
        lines = _lines(path)
        rec = json.loads(lines[9])
        rec["details"]["count"] = 31337
        lines[9] = json.dumps(rec)
        _rewrite(path, lines)

        first = _verify(home)
        assert ("tampered", 10) in [tuple(p) for p in first["problems"]], first

        env = _env(home)
        env["N"] = "15"
        proc = subprocess.run([sys.executable, "-c", _SEED], env=env, cwd=str(_REPO),
                              capture_output=True, text=True, timeout=300)
        assert "SEEDED" in proc.stdout, proc.stdout + proc.stderr

        grown = _witness(home)
        assert grown["admitted"], "the grown chain must still admit its witness"
        assert grown["prefix_bytes"] < path.stat().st_size, grown

        second = _verify(home)
        assert second["total"] > first["total"], (first, second)
        assert ("tampered", 10) in [tuple(p) for p in second["problems"]], second
        assert second["agrees_with_full_walk"], second

    def test_a_clean_growing_chain_stays_clean(self, chain):
        home, path = chain
        for _ in range(3):
            env = _env(home)
            env["N"] = "10"
            subprocess.run([sys.executable, "-c", _SEED], env=env, cwd=str(_REPO),
                           capture_output=True, text=True, timeout=300)
            result = _verify(home)
            assert result["ok"] and result["issues"] == [], result
            assert result["agrees_with_full_walk"], result


class TestBootStillRefuses:
    def test_a_current_state_problem_refuses_the_boot_with_a_witness_present(self, chain):
        """``anchor_key_insecure_mode`` is line-less — a CURRENT-state fact,
        recomputed after every walk, resumed or not — so the boot must refuse
        exactly as it does without a witness."""
        home, path = chain
        os.chmod(home / "keys" / "audit_anchor.key", 0o644)
        out = _run(_TRIPWIRE, home)
        assert "TRIPWIRE_REFUSED" in out, out[-3000:]
        assert "audit_chain_intact" in out, out[-3000:]

    def test_a_replaced_chain_refuses_the_boot_with_a_witness_present(self, chain):
        home, path = chain
        prev = ""
        forged = []
        for i in range(4):
            rec = {"ts": 1.0 + i, "event_type": "consent.granted", "severity": "INFO",
                   "run_id": "", "tool": "", "details": {"granted_by": "attacker"},
                   "prev_hash": prev}
            canon = json.dumps(rec, sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256()
            h.update(prev.encode()); h.update(b"\n"); h.update(canon.encode())
            rec["hash"] = h.hexdigest()[:16]
            prev = rec["hash"]
            forged.append(json.dumps(rec))
        _rewrite(path, forged)
        out = _run(_TRIPWIRE, home)
        assert "TRIPWIRE_REFUSED" in out, out[-3000:]
        assert "audit_chain_intact" in out, out[-3000:]

    def test_a_clean_chain_still_boots(self, chain):
        home, path = chain
        assert "TRIPWIRE_PASSED" in _run(_TRIPWIRE, home)
        # and again, now that the first boot left a witness behind
        assert "TRIPWIRE_PASSED" in _run(_TRIPWIRE, home)
