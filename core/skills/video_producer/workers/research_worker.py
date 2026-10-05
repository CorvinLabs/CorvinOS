"""Image research worker for Video Producer (ADR-2221).

Fetches images from a fixed, keyless, license-clear host allowlist —
Wikimedia Commons and the NASA Image Library — never a generic web search.
This is the image half of ADR-2221's two-mechanism design: text/fact
research happens upstream (agent-driven WebSearch, written to a
research_findings.json asset consumed by AssetAnalyzerWorker per
ADR-0693); only image fetching needs a worker-side egress client, because
only images need bytes pulled from the network at render time.

Every returned ResearchedImage carries a license checked against
ALLOWED_LICENSES. An image whose license is missing or outside that set is
discarded — a citation is provenance, not permission, and this worker never
treats the two as interchangeable (see ADR-2221 "Alternatives considered").
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Fixed allowlist — not a config value, so a caller cannot widen it without
# editing (and re-reviewing) this file. Mirrors ADR-2221's two image hosts.
ALLOWED_IMAGE_HOSTS = frozenset({
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "images-api.nasa.gov",
    "images-assets.nasa.gov",
})

ALLOWED_LICENSES = frozenset({
    "CC0",
    "CC-BY",
    "CC-BY-SA",
    "CC-BY 4.0",
    "CC-BY-SA 4.0",
    "CC-BY 3.0",
    "CC-BY-SA 3.0",
    "Public Domain",
    "PDM",
})

_USER_AGENT = "CorvinOS-VideoProducer-ResearchWorker/1.0 (ADR-2221)"


@dataclass
class ResearchedImage:
    title: str
    source_url: str
    license: str
    attribution_text: str
    fetched_at: str
    local_path: Optional[str] = None


def format_citation(img: ResearchedImage) -> str:
    """Host-neutral citation line attached to every embedded image."""
    return f'"{img.title}" — {img.attribution_text}, {img.license}, {img.source_url}'


def _assert_allowed_host(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ALLOWED_IMAGE_HOSTS:
        raise ValueError(f"research_worker: host {host!r} is not in ALLOWED_IMAGE_HOSTS")


def _http_get_json(url: str, timeout: float = 15.0) -> dict:
    _assert_allowed_host(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_commons_license(license_short_name: str) -> str:
    name = (license_short_name or "").strip()
    if name in ALLOWED_LICENSES:
        return name
    # Commons often returns bare "CC BY-SA 4.0" style; normalize dashes/spacing.
    normalized = name.replace("CC BY-SA", "CC-BY-SA").replace("CC BY", "CC-BY")
    return normalized if normalized in ALLOWED_LICENSES else name


def search_wikimedia_commons(query: str, limit: int = 5) -> list[ResearchedImage]:
    """Search Wikimedia Commons for license-tagged images matching query."""
    search_url = (
        "https://commons.wikimedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": "6",
            "gsrlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
        })
    )
    data = _http_get_json(search_url)
    pages = data.get("query", {}).get("pages", {})
    now = datetime.now(timezone.utc).isoformat()
    results: list[ResearchedImage] = []
    for page in pages.values():
        imageinfo = (page.get("imageinfo") or [{}])[0]
        url = imageinfo.get("url", "")
        if not url:
            continue
        extmeta = imageinfo.get("extmetadata", {})
        license_short = extmeta.get("LicenseShortName", {}).get("value", "")
        license_norm = _normalize_commons_license(license_short)
        if license_norm not in ALLOWED_LICENSES:
            continue
        artist = extmeta.get("Artist", {}).get("value", "Unknown")
        # extmetadata Artist is HTML; keep it simple for an attribution line.
        import re
        artist_plain = re.sub(r"<[^>]+>", "", artist).strip() or "Unknown"
        title = page.get("title", "").removeprefix("File:")
        results.append(ResearchedImage(
            title=title,
            source_url=url,
            license=license_norm,
            attribution_text=artist_plain,
            fetched_at=now,
        ))
    return results


def search_nasa_images(query: str, limit: int = 5) -> list[ResearchedImage]:
    """Search NASA Image Library (U.S. government work, public domain)."""
    search_url = "https://images-api.nasa.gov/search?" + urllib.parse.urlencode({
        "q": query, "media_type": "image",
    })
    data = _http_get_json(search_url)
    items = data.get("collection", {}).get("items", [])[:limit]
    now = datetime.now(timezone.utc).isoformat()
    results: list[ResearchedImage] = []
    for item in items:
        links = item.get("links") or []
        href = next((l["href"] for l in links if l.get("rel") == "preview"), None)
        if not href:
            continue
        data_meta = (item.get("data") or [{}])[0]
        title = data_meta.get("title", "NASA Image")
        center = data_meta.get("center", "NASA")
        results.append(ResearchedImage(
            title=title,
            source_url=href,
            license="Public Domain",
            attribution_text=f"NASA / {center}",
            fetched_at=now,
        ))
    return results


def download_image(img: ResearchedImage, dest_dir: str) -> ResearchedImage:
    """Download the image bytes to dest_dir, filling in local_path.
    Re-validates the host (defense in depth — a caller should never pass an
    off-allowlist ResearchedImage, but this is the function that actually
    touches the network for bytes, not just metadata)."""
    _assert_allowed_host(img.source_url)
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlparse(img.source_url).path).suffix or ".jpg"
    safe_name = "".join(c if c.isalnum() else "_" for c in img.title)[:60]
    local_path = str(Path(dest_dir) / f"{safe_name}{ext}")

    req = urllib.request.Request(img.source_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=30.0) as resp, open(local_path, "wb") as f:
        f.write(resp.read())

    img.local_path = local_path
    return img


def research_images(query: str, limit: int = 5) -> list[ResearchedImage]:
    """Search both allowed sources, merge results. License-filtered already
    at the per-source level (search_wikimedia_commons / search_nasa_images)."""
    results: list[ResearchedImage] = []
    try:
        results.extend(search_wikimedia_commons(query, limit=limit))
    except Exception:
        pass  # a source being unreachable must not fail the whole query
    try:
        results.extend(search_nasa_images(query, limit=limit))
    except Exception:
        pass
    return results[:limit]
