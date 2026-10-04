"""E2E: new A2A pairings write require_ibc=true (ADR-2099 P0 Fact 3).

Reuses the real console route sandbox from
``core/console/tests/test_a2a_pair_friendship_security.py`` (same env-var
sandboxed origins/endpoints dirs, same friendship-token forging helper) and
drives the ACTUAL route functions — ``friendship_import`` (console redeem)
and the accept-side reconstruction in ``a2a_friendship.py`` — not a direct
call to ``to_origin_dict`` in isolation. That receiver-side enforcement of
``require_ibc`` (reject when true + no attestation, cache-only CRL) is
already proven end-to-end over real HTTP in
``corvin_operator/bridges/shared/test_a2a_ibc_attach_e2e.py``; this file
proves the narrower, previously-untested half: new pairings actually WRITE
the flag the receiver enforces.

Run: python3 -m pytest tests/e2e/a2a/test_a2a_require_ibc_enforcement_e2e.py -v
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

_CONSOLE_TESTS = Path(__file__).resolve().parents[3] / "core" / "console" / "tests"
if str(_CONSOLE_TESTS) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_TESTS))

from test_a2a_pair_friendship_security import (  # noqa: E402  # type: ignore[import-not-found]
    _RouteSandbox,
    _forge_friendship_token,
    ap,
    ft,
)


class TestRequireIbcWrittenOnNewPairings(_RouteSandbox):
    def test_friendship_import_writes_require_ibc_true(self):
        """Real route call: ap.friendship_import -> origin file on disk."""
        kid = "peer-" + secrets.token_hex(4)
        tok = _forge_friendship_token({
            "kid": kid, "key": secrets.token_hex(32), "v": 1,
            "con": {"personas": ["assistant"]},
        })
        status = self.status_of(self.import_, tok)
        self.assertEqual(status, 200, "friendship_import should accept a well-formed token")

        origin_file = self.od / f"{kid}.json"
        self.assertTrue(origin_file.exists(), f"origin file not written: {origin_file}")
        cfg = json.loads(origin_file.read_text())
        self.assertIs(cfg.get("require_ibc"), True,
                       f"new pairing must set require_ibc=true, got: {cfg.get('require_ibc')!r}")

if __name__ == "__main__":
    import unittest
    unittest.main()
