"""E2E tests for the ADR-2221 image research worker.

Deterministic gate tests (host allowlist, license allowlist) run always.
The live-network tests hit the real Wikimedia Commons API — they
are the actual reachability + functional proof (ADR-2221's whole point is
that a license gate and a host gate hold against real API responses, not
canned fixtures) and are skipped only if the network is unavailable.
"""

import os
import socket

import pytest

from core.skills.video_producer.workers.research_worker import (
    ALLOWED_IMAGE_HOSTS,
    ALLOWED_LICENSES,
    ResearchedImage,
    _assert_allowed_host,
    _normalize_commons_license,
    download_image,
    format_citation,
    research_images,
    search_wikimedia_commons,
)


def _network_available() -> bool:
    try:
        socket.create_connection(("commons.wikimedia.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


NETWORK = _network_available()


class TestHostGate:
    def test_allowed_host_passes(self):
        _assert_allowed_host("https://commons.wikimedia.org/w/api.php")
        _assert_allowed_host("https://upload.wikimedia.org/wikipedia/commons/x.png")

    def test_off_allowlist_host_rejected(self):
        with pytest.raises(ValueError, match="not in ALLOWED_IMAGE_HOSTS"):
            _assert_allowed_host("https://evil-image-host.example.com/image.jpg")

    def test_allowlist_is_exactly_the_adr_2221_hosts(self):
        # Pinned so a future edit that widens this silently is caught by CI.
        assert ALLOWED_IMAGE_HOSTS == frozenset({
            "commons.wikimedia.org",
            "upload.wikimedia.org",
            "thumb.wikimedia.org",
        })


class TestLicenseGate:
    def test_unlicensed_image_is_rejected(self):
        rejected = _normalize_commons_license("All rights reserved")
        assert rejected not in ALLOWED_LICENSES

    def test_known_free_licenses_pass(self):
        for lic in ("CC0", "CC-BY", "CC-BY-SA", "Public Domain"):
            assert lic in ALLOWED_LICENSES

    def test_commons_spacing_variant_normalizes(self):
        assert _normalize_commons_license("CC BY-SA 4.0") == "CC-BY-SA 4.0"
        assert _normalize_commons_license("CC BY 3.0") == "CC-BY 3.0"


class TestCitationFormat:
    def test_citation_includes_provenance_fields(self):
        img = ResearchedImage(
            title="Test Image",
            source_url="https://upload.wikimedia.org/x.png",
            license="CC-BY-SA 4.0",
            attribution_text="Jane Doe",
            fetched_at="2026-10-05T00:00:00+00:00",
        )
        citation = format_citation(img)
        assert "Test Image" in citation
        assert "Jane Doe" in citation
        assert "CC-BY-SA 4.0" in citation
        assert "https://upload.wikimedia.org/x.png" in citation


@pytest.mark.skipif(not NETWORK, reason="no network access to commons.wikimedia.org")
class TestWikimediaCommonsLive:
    def test_search_returns_license_filtered_results(self):
        results = search_wikimedia_commons("graph database", limit=5)
        assert isinstance(results, list)
        for r in results:
            assert r.license in ALLOWED_LICENSES
            assert r.source_url.startswith(("https://upload.wikimedia.org/", "https://thumb.wikimedia.org/"))

    def test_search_and_download_real_image(self, tmp_path):
        results = search_wikimedia_commons("knowledge graph", limit=3)
        if not results:
            pytest.skip("no Commons results for this query right now")
        downloaded = download_image(results[0], str(tmp_path))
        assert downloaded.local_path is not None
        assert os.path.exists(downloaded.local_path)
        assert os.path.getsize(downloaded.local_path) > 0


@pytest.mark.skipif(not NETWORK, reason="no network access")
class TestResearchImagesMerged:
    def test_merges_both_sources_and_respects_limit(self):
        results = research_images("graph", limit=4)
        assert len(results) <= 4
        for r in results:
            assert r.license in ALLOWED_LICENSES

    def test_unreachable_source_yields_no_candidates(self, monkeypatch):
        import core.skills.video_producer.workers.research_worker as rw

        def _boom(*a, **kw):
            raise RuntimeError("simulated API outage")

        monkeypatch.setattr(rw, "search_wikimedia_commons", _boom)
        # An unreachable source yields no candidates — never an exception.
        assert rw.research_images("graph database", limit=3) == []


class TestHardening:
    def test_nasa_is_not_a_source(self):
        import core.skills.video_producer.workers.research_worker as rw
        assert not any("nasa" in h for h in rw.ALLOWED_IMAGE_HOSTS)
        with pytest.raises(ValueError, match="unknown image source"):
            rw.research_images("x", sources=["nasa"])

    def _fake_fetch(self, monkeypatch, data):
        import core.skills.video_producer.workers.research_worker as rw
        monkeypatch.setattr(rw, "_http_get_bytes", lambda url, max_bytes, timeout: data)
        return rw

    def _img(self):
        return ResearchedImage(title="t", source_url="https://upload.wikimedia.org/x.jpg",
                               license="CC0", attribution_text="a", fetched_at="now")

    def test_truncated_jpeg_is_rejected(self, monkeypatch, tmp_path):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (400, 300), (10, 200, 30)).save(buf, "JPEG", quality=95)
        data = buf.getvalue()[: len(buf.getvalue()) // 3]
        rw = self._fake_fetch(monkeypatch, data)
        with pytest.raises(ValueError, match="not a decodable image"):
            rw.download_image(self._img(), str(tmp_path))

    def test_pixel_bomb_is_rejected(self, monkeypatch, tmp_path):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("1", (6000, 5000)).save(buf, "PNG")
        rw = self._fake_fetch(monkeypatch, buf.getvalue())
        with pytest.raises(ValueError, match="exceeds"):
            rw.download_image(self._img(), str(tmp_path))

    def test_attribution_entities_are_decoded_and_tags_dropped(self):
        from core.skills.video_producer.workers.research_worker import _strip_html
        assert _strip_html('<a href="x">Foo &amp; Bar</a>') == "Foo & Bar"
        assert _strip_html("Jane <img src=x onerror=alert(1)") == "Jane"
