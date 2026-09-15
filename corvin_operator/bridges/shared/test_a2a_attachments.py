"""Tests for a2a_attachments.py — Layer 38 v3 attachment validation."""
from __future__ import annotations

import base64
import hashlib
import sys
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_attachments as att  # noqa: E402


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _att(name: str, content: bytes, mime: str = "text/plain") -> dict:
    return {
        "name": name, "mime": mime,
        "sha256": _digest(content),
        "content_b64": _b64(content),
    }


# ── Name validation ──────────────────────────────────────────────────────

class TestNameValidation(unittest.TestCase):

    def test_simple_name_ok(self):
        att.validate_attachment_name("foo.csv")
        att.validate_attachment_name("histogram.png")
        att.validate_attachment_name("a")
        att.validate_attachment_name("file_1.json")
        att.validate_attachment_name("data-2024.csv")

    def test_path_traversal_rejected(self):
        for bad in ("..", "../foo", "foo/bar", "..\\evil",
                    "/abs/path", "a/b"):
            with self.assertRaises(att.AttachmentError) as ctx:
                att.validate_attachment_name(bad)
            self.assertIn("name_", ctx.exception.reason)

    def test_hidden_file_rejected(self):
        for bad in (".ssh", ".env", ".gitignore"):
            with self.assertRaises(att.AttachmentError):
                att.validate_attachment_name(bad)

    def test_empty_rejected(self):
        with self.assertRaises(att.AttachmentError):
            att.validate_attachment_name("")

    def test_oversize_rejected(self):
        with self.assertRaises(att.AttachmentError):
            att.validate_attachment_name("x" * 129)

    def test_max_length_ok(self):
        att.validate_attachment_name("x" * 128)

    def test_non_string_rejected(self):
        with self.assertRaises(att.AttachmentError):
            att.validate_attachment_name(123)  # type: ignore[arg-type]

    def test_dotdot_anywhere_rejected(self):
        with self.assertRaises(att.AttachmentError):
            att.validate_attachment_name("..hidden")


# ── Attachment dataclass ──────────────────────────────────────────────────

class TestAttachmentDataclass(unittest.TestCase):

    def test_from_bytes_round_trip(self):
        a = att.Attachment.from_bytes(
            name="hi.txt", mime="text/plain", content=b"hello",
        )
        self.assertEqual(a.decode(), b"hello")

    def test_from_dict_required_fields(self):
        with self.assertRaises(att.AttachmentError) as ctx:
            att.Attachment.from_dict({"name": "x"})
        self.assertIn("missing_fields", ctx.exception.reason)

    def test_digest_mismatch_rejected(self):
        a = att.Attachment(
            name="x.txt", mime="text/plain",
            sha256=_digest(b"fake"),  # wrong digest
            content_b64=_b64(b"real"),
        )
        with self.assertRaises(att.AttachmentError) as ctx:
            a.decode()
        self.assertEqual(ctx.exception.reason, "attachment_digest_mismatch")

    def test_malformed_b64_rejected(self):
        a = att.Attachment(
            name="x.txt", mime="text/plain",
            sha256=_digest(b"x"),
            content_b64="!!!not-base64!!!",
        )
        with self.assertRaises(att.AttachmentError):
            a.decode()


# ── List validation + caps ────────────────────────────────────────────────

class TestListValidation(unittest.TestCase):

    def test_empty_list_ok(self):
        out = att.validate_attachments([])
        self.assertEqual(out, [])

    def test_valid_list_ok(self):
        items = [_att("a.txt", b"hello"), _att("b.csv", b"x,y\n1,2")]
        out = att.validate_attachments(items)
        self.assertEqual(len(out), 2)

    def test_non_list_rejected(self):
        with self.assertRaises(att.AttachmentError):
            att.validate_attachments("not-a-list")  # type: ignore[arg-type]

    def test_count_cap_enforced(self):
        items = [_att(f"file{i}.txt", b"x") for i in range(att.MAX_ATTACHMENTS_COUNT + 1)]
        with self.assertRaises(att.AttachmentError) as ctx:
            att.validate_attachments(items)
        self.assertEqual(ctx.exception.reason, "attachments_too_many")

    def test_total_byte_cap_enforced(self):
        big = b"x" * (att.MAX_ATTACHMENTS_TOTAL_BYTES // 2 + 1)
        items = [_att("a.bin", big), _att("b.bin", big)]
        with self.assertRaises(att.AttachmentError) as ctx:
            att.validate_attachments(items)
        self.assertEqual(ctx.exception.reason, "attachments_total_too_large")

    def test_duplicate_names_rejected(self):
        items = [_att("dup.txt", b"a"), _att("dup.txt", b"b")]
        with self.assertRaises(att.AttachmentError) as ctx:
            att.validate_attachments(items)
        self.assertEqual(ctx.exception.reason, "attachment_duplicate_name")

    def test_bad_name_in_list_rejected(self):
        items = [_att("../escape", b"x")]
        with self.assertRaises(att.AttachmentError):
            att.validate_attachments(items)

    def test_digest_mismatch_in_list_rejected(self):
        items = [{"name": "x.txt", "mime": "text/plain",
                  "sha256": _digest(b"WRONG"),
                  "content_b64": _b64(b"actual")}]
        with self.assertRaises(att.AttachmentError) as ctx:
            att.validate_attachments(items)
        self.assertEqual(ctx.exception.reason, "attachment_digest_mismatch")


# ── Audit projection ──────────────────────────────────────────────────────

class TestAuditProjection(unittest.TestCase):

    def test_audit_contains_safe_metadata(self):
        items = att.validate_attachments([
            _att("a.txt", b"hello"),
            _att("b.png", b"\x89PNG\x00...", mime="image/png"),
        ])
        proj = att.attachments_audit_details(items)
        self.assertEqual(proj["attachments_count"], 2)
        self.assertEqual(proj["attachment_names"], ["a.txt", "b.png"])
        self.assertEqual(len(proj["attachment_sha_prefixes"][0]), 16)
        # Full sha not in projection
        full = items[0].sha256
        self.assertNotIn(full, proj["attachment_sha_prefixes"])

    def test_audit_does_not_include_content(self):
        items = att.validate_attachments([
            _att("secret.txt", b"PASSWORD-12345"),
        ])
        proj = att.attachments_audit_details(items)
        serialised = str(proj)
        self.assertNotIn("PASSWORD-12345", serialised)
        self.assertNotIn("UEFTU1dPUkQ", serialised)  # b64 form


# ── from_file helper ──────────────────────────────────────────────────────

class TestFromFile(unittest.TestCase):

    def test_csv_file_round_trip(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"a,b\n1,2\n")
            path = Path(f.name)
        try:
            a = att.Attachment.from_file(path)
            self.assertEqual(a.mime, "text/csv")
            self.assertEqual(a.decode(), b"a,b\n1,2\n")
        finally:
            path.unlink()


# ── CI lint ──────────────────────────────────────────────────────────────

class TestCILint(unittest.TestCase):
    def test_no_anthropic_import(self):
        import ast
        src = (_here / "a2a_attachments.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


# ── C-6: Data classification (ADR-0077) ─────────────────────────────────

class TestClassification(unittest.TestCase):

    def _make_att(self, classification=None):
        return att.Attachment.from_bytes(
            name="data.csv", mime="text/csv",
            content=b"a,b\n1,2\n",
            classification=classification,
        )

    def test_no_classification_defaults_to_internal(self):
        a = self._make_att()
        level = att.classification_level(a.classification)
        self.assertEqual(level, att.classification_level("INTERNAL"))

    def test_public_level_lowest(self):
        self.assertLess(
            att.classification_level("PUBLIC"),
            att.classification_level("INTERNAL"),
        )

    def test_secret_level_highest(self):
        self.assertEqual(att.classification_level("SECRET"), 3)

    def test_bad_classification_rejected(self):
        d = self._make_att().to_dict()
        d["classification"] = "TOPSECRET"
        with self.assertRaises(att.AttachmentError) as ctx:
            att.Attachment.from_dict(d)
        self.assertIn("bad_classification", ctx.exception.reason)

    def test_effective_classification_empty_list(self):
        self.assertEqual(att.effective_classification([]), "PUBLIC")

    def test_effective_classification_max(self):
        a1 = self._make_att("PUBLIC")
        a2 = self._make_att("CONFIDENTIAL")
        a3 = self._make_att("INTERNAL")
        result = att.effective_classification([a1, a2, a3])
        self.assertEqual(result, "CONFIDENTIAL")

    def test_effective_classification_all_none_is_internal(self):
        a1 = self._make_att(None)
        a2 = self._make_att(None)
        result = att.effective_classification([a1, a2])
        self.assertEqual(result, "INTERNAL")

    def test_classification_round_trip(self):
        a = self._make_att("SECRET")
        d = a.to_dict()
        self.assertEqual(d.get("classification"), "SECRET")
        a2 = att.Attachment.from_dict(d)
        self.assertEqual(a2.classification, "SECRET")

    def test_none_classification_omitted_from_wire(self):
        a = self._make_att(None)
        d = a.to_dict()
        self.assertNotIn("classification", d)

    def test_case_normalisation(self):
        d = self._make_att().to_dict()
        d["classification"] = "confidential"
        a = att.Attachment.from_dict(d)
        self.assertEqual(a.classification, "CONFIDENTIAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
