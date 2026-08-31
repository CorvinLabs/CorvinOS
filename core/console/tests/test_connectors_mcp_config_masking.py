"""Regression: GET /connectors/mcp-config must NOT leak resolved plaintext secrets.

Finding (MEDIUM): ``get_mcp_config`` (require_session only) previously resolved
``${ENV}`` placeholders from the vault AND ``os.environ`` and returned the
concrete token values in the ``{"mcpServers": …}`` response body. Any
authenticated session (or an XSS reading the response) could thereby harvest
resolved secrets, including the console's own process env like
``${OPENAI_API_KEY}``.

Fix: the client-facing endpoint returns the config with placeholders left
UNEXPANDED (masked). Secret resolution stays server-side in
``build_mcp_config_for_node`` (used for spawning), which this test confirms
still resolves.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

import corvin_console.routes.connectors as connectors  # noqa: E402


def _rec(tenant_id: str = "_default"):
    r = MagicMock()
    r.tenant_id = tenant_id
    return r


_SENTINEL = "sk-SENTINEL-super-secret-value-DO-NOT-LEAK"


class GetMcpConfigMaskingTests(unittest.TestCase):

    def setUp(self) -> None:
        # Put a real secret into the console process env — the connector
        # 'notion' declares ${OPENAI_API_KEY} in its mcp_config env.
        self._orig = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = _SENTINEL

    def tearDown(self) -> None:
        if self._orig is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._orig

    def test_response_body_contains_no_resolved_secret(self) -> None:
        out = connectors.get_mcp_config("notion", rec=_rec())
        # The sentinel must appear nowhere in the serialized response.
        import json
        blob = json.dumps(out)
        self.assertNotIn(_SENTINEL, blob,
                         "resolved plaintext secret leaked into client response")
        # The placeholder must be preserved (masked, not resolved).
        env = out["mcpServers"]["notion"]["env"]
        self.assertEqual(env.get("OPENAI_API_KEY"), "${OPENAI_API_KEY}")
        self.assertEqual(env.get("notion_api_key"), "${NOTION_TOKEN}")

    def test_args_placeholder_not_resolved(self) -> None:
        # 'filesystem' has ${FILESYSTEM_ROOT} in args — must stay a placeholder.
        out = connectors.get_mcp_config("filesystem", rec=_rec())
        args = out["mcpServers"]["filesystem"]["args"]
        self.assertIn("${FILESYSTEM_ROOT}", args)

    def test_server_side_builder_still_resolves(self) -> None:
        # The spawn-path builder (server-side only) MUST still resolve the
        # secret — masking is client-facing only.
        built = connectors.build_mcp_config_for_node("_default", ["notion"])
        self.assertEqual(
            built["mcpServers"]["notion"]["env"]["OPENAI_API_KEY"], _SENTINEL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
