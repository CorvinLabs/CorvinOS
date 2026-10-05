"""E2E tests for the ADR-2221 image research worker.

Deterministic gate tests (host allowlist, license allowlist) run always.
The live-network tests hit the real Wikimedia Commons / NASA APIs — they
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
    search_nasa_images,
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
        _assert_allowed_host("https://images-api.nasa.gov/search")

    def test_off_allowlist_host_rejected(self):
        with pytest.raises(ValueError, match="not in ALLOWED_IMAGE_HOSTS"):
            _assert_allowed_host("https://evil-image-host.example.com/image.jpg")

    def test_allowlist_is_exactly_the_adr_2221_hosts(self):
        # Pinned so a future edit that widens this silently is caught by CI.
        assert ALLOWED_IMAGE_HOSTS == frozenset({
            "commons.wikimedia.org",
            "upload.wikimedia.org",
            "thumb.wikimedia.org",
            "images-api.nasa.gov",
            "images-assets.nasa.gov",
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


@pytest.mark.skipif(not NETWORK, reason="no network access to images-api.nasa.gov")
class TestNasaImagesLive:
    def test_search_returns_public_domain_results(self):
        results = search_nasa_images("network", limit=3)
        assert isinstance(results, list)
        for r in results:
            assert r.license == "Public Domain"
            assert r.source_url.startswith("https://images-assets.nasa.gov/")


@pytest.mark.skipif(not NETWORK, reason="no network access")
class TestResearchImagesMerged:
    def test_merges_both_sources_and_respects_limit(self):
        results = research_images("graph", limit=4)
        assert len(results) <= 4
        for r in results:
            assert r.license in ALLOWED_LICENSES

    def test_one_source_failing_does_not_fail_the_whole_query(self, monkeypatch):
        import core.skills.video_producer.workers.research_worker as rw

        def _boom(*a, **kw):
            raise RuntimeError("simulated API outage")

        monkeypatch.setattr(rw, "search_nasa_images", _boom)
        results = rw.research_images("graph database", limit=3)
        # Commons alone should still produce results despite NASA "outage".
        assert isinstance(results, list)
