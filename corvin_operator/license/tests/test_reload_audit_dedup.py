"""A licence reload that changes nothing writes nothing to the audit chain.

``reload_from_disk()`` runs on every authenticated console request
(``auth.py::_compute_lic_proof``); the 5 s throttle lets one through per window.
Every one of those re-seeded the chain DNA and wrote ``license.chain_dna_seeded``
+ ``audit.cit_issued`` for the SAME token and tier — measured 2026-09-26: ~1 350
records/hour on a host with one open console tab, 97 % of everything that chain
received. Those two records mark a licence TAKING EFFECT; they are written when
the tier or the DNA seed actually changes, and not otherwise.

Drives the real reload (crypto and licence I/O stubbed as in
test_reload_root_key_atomicity.py) against a temporary chain file and CORVIN_HOME.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "corvin_operator")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from license import feature_lattice as FL  # noqa: E402
from license import validator as V  # noqa: E402

TOKEN_A = "header.payloadA.signature"
TOKEN_B = "header.payloadB.signature"
CLAIMS = {"tier": "member", "jti": "test-jti-dedup", "iss": "corvinlabs.io", "type": "license"}


class ReloadAuditDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.chain = self.tmp / "audit.jsonl"
        self._env = mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.tmp / "home")})
        self._env.start()
        names = ("_LICENSE_INITIALIZED", "_CORVIN_HOME_SNAPSHOT", "_AUDIT_PATH_SNAPSHOT",
                 "_LAST_RELOAD_AT", "_LAST_LOADED_TOKEN_HASH", "_LICENSE_LOADED_AT",
                 "_LAST_SEEDED_STATE")
        self._prev = {n: getattr(V, n, None) for n in names}
        V._LICENSE_INITIALIZED = True
        V._CORVIN_HOME_SNAPSHOT = self.tmp / "home"
        V._AUDIT_PATH_SNAPSHOT = self.chain
        V._LAST_SEEDED_STATE = None

    def tearDown(self) -> None:
        for n, v in self._prev.items():
            setattr(V, n, v)
        FL.set_feature_root_key(None)
        self._env.stop()

    def _reload(self, token: str) -> None:
        V._LAST_RELOAD_AT = 0.0  # every call past the 5 s throttle, like one per window
        with mock.patch.object(V, "_find_token_disk_only", lambda: token), \
             mock.patch.object(V, "_verify_ed25519", lambda _t: dict(CLAIMS)), \
             mock.patch.object(V, "_is_token_fp_revoked", lambda _t: False), \
             mock.patch.object(V, "_validate_claims", lambda c: dict(c)), \
             mock.patch.object(V, "_check_instance_id_bound", lambda _c: True), \
             mock.patch.object(V, "_check_device_fp", lambda _c: True), \
             mock.patch.object(V, "_set_active_license", lambda _c: None), \
             mock.patch.object(V, "_init_instance_seed", lambda: None), \
             mock.patch.object(V, "_audit", lambda *a, **k: None):
            V.reload_from_disk()

    def _count(self, event_type: str) -> int:
        if not self.chain.exists():
            return 0
        return sum(1 for line in self.chain.read_text().splitlines()
                   if line.strip() and json.loads(line).get("event_type") == event_type)

    def test_unchanged_licence_is_seeded_once(self) -> None:
        for _ in range(5):
            self._reload(TOKEN_A)
        self.assertEqual(self._count("license.chain_dna_seeded"), 1)

    def test_a_new_token_is_seeded_again(self) -> None:
        self._reload(TOKEN_A)
        self._reload(TOKEN_A)
        self._reload(TOKEN_B)
        self.assertEqual(self._count("license.chain_dna_seeded"), 2)

    def test_without_redirect_the_record_lands_in_the_tenant_chain(self) -> None:
        # No VOICE_AUDIT_PATH: the record must go to THE tenant chain
        # (tenant_audit_chain), never to the legacy host path
        # <corvin_home>/global/forge/audit.jsonl the code used to compose by hand.
        V._AUDIT_PATH_SNAPSHOT = None
        home = self.tmp / "home"
        with mock.patch.dict(os.environ, {"CORVIN_TENANT_ID": "_default"}):
            os.environ.pop("VOICE_AUDIT_PATH", None)
            self._reload(TOKEN_A)
        canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        legacy = home / "global" / "forge" / "audit.jsonl"
        self.chain = legacy
        self.assertEqual(self._count("license.chain_dna_seeded"), 0)
        self.chain = canonical
        self.assertEqual(self._count("license.chain_dna_seeded"), 1)


if __name__ == "__main__":
    unittest.main()
