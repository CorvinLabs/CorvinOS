"""REVIEW-0090 N42: the import check accepted a project.yaml the manifest reader refuses (an alias, a size that only the rewritten
form exceeds); the import was left half done after extraction. The check now applies the reader's own rules."""
import pytest

from core.projects import bundle, manifest

BASE = "schema: 1\nid: alpha\ntitle: Alpha\ntemplate: blank@1\nfeatures:\n  core: true\n"


def test_a_plain_manifest_passes():
    assert bundle._check_manifest("alpha", BASE.encode())["id"] == "alpha"


def test_an_alias_is_refused_at_the_check():
    doc = "schema: 1\nid: alpha\ntitle: Alpha\ntemplate: blank@1\nfeatures: &f {core: true}\nextra: *f\n"
    with pytest.raises(Exception):
        bundle._check_manifest("alpha", doc.encode())


def test_a_manifest_whose_rewritten_form_is_too_large_is_refused_at_the_check():
    pad = "pad: [" + ",".join("1" for _ in range(30000)) + "]\n"       # flow style is small; block style on rewrite is not
    data = (BASE + pad).encode()
    assert len(data) < 64 * 1024
    with pytest.raises(Exception):
        bundle._check_manifest("alpha", data)


def test_write_refuses_what_load_would_refuse(tmp_path):
    with pytest.raises(manifest.ManifestError):
        manifest.write(tmp_path, {"schema": 1, "id": "alpha", "title": "A", "tenant": "_default", "template": "blank@1",
                                  "features": {"core": True}, "pad": list(range(30000))})
    assert not (tmp_path / manifest.FILE).exists()
