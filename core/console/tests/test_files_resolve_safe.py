#!/usr/bin/env python3
"""Regression: _resolve_safe() must turn EVERY traversal-guard failure into a
clean HTTPException(400), never an unhandled ValueError.

``candidate = (root / rel).resolve()`` can itself raise ``ValueError`` (e.g.
for a relative path containing an embedded null byte, such as
``"a\\x00../../etc"``) — a failure mode distinct from the ``relative_to()``
escape check the try/except was written to guard. Before the fix, that
resolve()-raised ValueError happened outside the try/except block and
propagated unhandled out of every route calling ``_resolve_safe`` (mkdir,
delete, download, tree, content, upload), turning a designed-for 400 into a
raw 500 on any of the six file routes for a trivially crafted request.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

import corvin_console.routes.files as files_routes  # noqa: E402
from corvin_console.routes.files import _resolve_safe, _MkdirBody  # type: ignore


def _rec(tenant_id: str = "_default"):
    r = MagicMock()
    r.tenant_id = tenant_id
    r.sid_fingerprint = "fp-test"
    return r


class ResolveSafeUnitTests(unittest.TestCase):
    """Direct unit coverage of _resolve_safe()."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-resolve-safe-"))

    def test_null_byte_raises_http_400_not_value_error(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _resolve_safe(self.tmp, "a\x00b")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("escapes tenant root", ctx.exception.detail)

    def test_null_byte_in_nested_traversal_raises_http_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _resolve_safe(self.tmp, "a\x00../../etc")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_ordinary_traversal_still_raises_http_400(self) -> None:
        # Sanity: the fix must not weaken the original relative_to() guard.
        with self.assertRaises(HTTPException) as ctx:
            _resolve_safe(self.tmp, "../../etc/passwd")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_ordinary_path_resolves_normally(self) -> None:
        (self.tmp / "sub").mkdir()
        result = _resolve_safe(self.tmp, "sub/file.txt")
        self.assertEqual(result, (self.tmp / "sub" / "file.txt").resolve())

    def test_empty_rel_returns_root(self) -> None:
        self.assertEqual(_resolve_safe(self.tmp, ""), self.tmp)


class FilesRoutesNullByteTests(unittest.TestCase):
    """Route-level coverage: a null byte in the path/body must surface as a
    clean 400 from every route that calls _resolve_safe, not a bare 500."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-files-routes-"))
        self.rec = _rec()
        self._patcher = patch.object(
            files_routes._forge_paths, "tenant_home", return_value=self.tmp
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_files_tree_null_byte_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            files_routes.files_tree(rec=self.rec, path="a\x00b", depth=2)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_files_download_null_byte_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            files_routes.files_download(rec=self.rec, path="a\x00b")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_files_delete_null_byte_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            files_routes.files_delete(rec=self.rec, path="a\x00b")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_files_mkdir_null_byte_400(self) -> None:
        body = _MkdirBody(path="foo\x00bar")
        with self.assertRaises(HTTPException) as ctx:
            files_routes.files_mkdir(rec=self.rec, body=body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_files_content_null_byte_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            files_routes.files_content(rec=self.rec, path="a\x00b")
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
