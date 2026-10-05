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

Network hardening: https only, every redirect hop re-checked against
ALLOWED_IMAGE_HOSTS (urllib follows redirects silently, so a check on the
first URL alone would let a 302 leave the allowlist), a byte cap on every
response, and every downloaded file must decode as an image before it is
accepted.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .job_tmp import job_scoped_dir

# Fixed allowlist — not a config value, so a caller cannot widen it without
# editing (and re-reviewing) this file. Mirrors ADR-2221's two image hosts.
ALLOWED_IMAGE_HOSTS = frozenset({
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "thumb.wikimedia.org",   # Commons serves iiurlwidth thumbnails from here
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
    "CC-BY 2.0",
    "CC-BY-SA 2.0",
    "Public Domain",
    "PDM",
})

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
THUMB_WIDTH = 1280

_USER_AGENT = "CorvinOS-VideoProducer-ResearchWorker/1.1 (ADR-2221)"
_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class ResearchedImage:
    title: str
    source_url: str
    license: str
    attribution_text: str
    fetched_at: str
    local_path: Optional[str] = None
    page_url: Optional[str] = None


def format_citation(img: ResearchedImage) -> str:
    """Host-neutral citation line attached to every embedded image."""
    source = img.page_url or img.source_url
    return f'"{img.title}" — {img.attribution_text}, {img.license}, {source}'


def _assert_allowed_host(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        raise ValueError(f"research_worker: {url!r} is not https")
    if host not in ALLOWED_IMAGE_HOSTS:
        raise ValueError(f"research_worker: host {host!r} is not in ALLOWED_IMAGE_HOSTS")


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_allowed_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_AllowlistRedirectHandler())


def _http_get_bytes(url: str, max_bytes: int, timeout: float) -> bytes:
    _assert_allowed_host(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with _OPENER.open(req, timeout=timeout) as resp:
        _assert_allowed_host(resp.geturl())
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"research_worker: response from {urllib.parse.urlparse(url).hostname} exceeds {max_bytes} bytes")
    return data


def _http_get_json(url: str, timeout: float = 15.0) -> dict:
    return json.loads(_http_get_bytes(url, MAX_JSON_BYTES, timeout).decode("utf-8"))


def _normalize_commons_license(license_short_name: str) -> str:
    name = (license_short_name or "").strip()
    if name in ALLOWED_LICENSES:
        return name
    normalized = name.replace("CC BY-SA", "CC-BY-SA").replace("CC BY", "CC-BY")
    if normalized.lower() in ("public domain", "pd"):
        return "Public Domain"
    return normalized if normalized in ALLOWED_LICENSES else name


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


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
            "iiurlwidth": str(THUMB_WIDTH),
        })
    )
    data = _http_get_json(search_url)
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    now = datetime.now(timezone.utc).isoformat()
    results: list[ResearchedImage] = []
    for page in pages:
        imageinfo = (page.get("imageinfo") or [{}])[0]
        url = imageinfo.get("thumburl") or imageinfo.get("url", "")
        if not url:
            continue
        extmeta = imageinfo.get("extmetadata", {})
        license_norm = _normalize_commons_license(extmeta.get("LicenseShortName", {}).get("value", ""))
        if license_norm not in ALLOWED_LICENSES:
            continue
        artist = _strip_html(extmeta.get("Artist", {}).get("value", "")) or "Unknown"
        title = page.get("title", "").removeprefix("File:")
        results.append(ResearchedImage(
            title=title,
            source_url=url,
            license=license_norm,
            attribution_text=artist[:120],
            fetched_at=now,
            page_url=imageinfo.get("descriptionurl"),
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
        title = (data_meta.get("title") or "NASA Image")[:120]
        center = data_meta.get("center", "NASA")
        nasa_id = data_meta.get("nasa_id")
        results.append(ResearchedImage(
            title=title,
            source_url=href,
            license="Public Domain",
            attribution_text=f"NASA / {center}",
            fetched_at=now,
            page_url=f"https://images.nasa.gov/details/{urllib.parse.quote(nasa_id)}" if nasa_id else None,
        ))
    return results


def download_image(img: ResearchedImage, dest_dir: str) -> ResearchedImage:
    """Download the image bytes to dest_dir and prove they decode as an image.
    Re-validates the host on every hop; rejects oversize or non-image bodies."""
    from io import BytesIO

    from PIL import Image

    data = _http_get_bytes(img.source_url, MAX_IMAGE_BYTES, timeout=30.0)
    try:
        with Image.open(BytesIO(data)) as im:
            im.verify()
    except Exception as e:
        raise ValueError(f"research_worker: {img.title!r} is not a decodable image: {e}") from e

    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlparse(img.source_url).path).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ext = ".img"
    safe_name = "".join(c if c.isalnum() else "_" for c in img.title)[:60] or "image"
    local_path = Path(dest_dir) / f"{safe_name}{ext}"
    local_path.write_bytes(data)
    img.local_path = str(local_path)
    return img


def research_images(query: str, limit: int = 5, sources: Optional[List[str]] = None) -> list[ResearchedImage]:
    """Search the allowed sources (default: both), merge results. License-filtered
    at the per-source level. One source being unreachable never fails the query."""
    sources = sources or ["commons", "nasa"]
    results: list[ResearchedImage] = []
    if "commons" in sources:
        try:
            results.extend(search_wikimedia_commons(query, limit=limit))
        except Exception:
            pass
    if "nasa" in sources:
        try:
            results.extend(search_nasa_images(query, limit=limit))
        except Exception:
            pass
    return results[:limit]


@dataclass
class ImageResearchResult:
    images: Dict[str, dict]
    citations: List[str]
    success: bool = True
    error: Optional[str] = None


class ImageResearchWorker:
    """Maestro worker for VideoJobPhase.IMAGE_RESEARCH.

    Reads job.research_queries: {ref: query} or {ref: {"query": str,
    "sources": ["commons"|"nasa"]}}. For every ref it downloads the first
    license-clean, decodable image. Fail-closed: if ANY ref ends up with no
    image, the phase fails — a diagram spec that references `research:<ref>`
    must never be rendered with a hole where the image should be.
    """

    def __init__(self, out_dir: Optional[str] = None, candidates_per_query: int = 5):
        self.name = "image_research"
        self.version = "1.0.0"
        self.out_dir = out_dir
        self.candidates_per_query = candidates_per_query

    def execute(self, job) -> ImageResearchResult:
        queries = getattr(job, "research_queries", None) or {}
        if not queries:
            return ImageResearchResult({}, [], success=False, error="no research_queries on job")

        base = Path(self.out_dir) if self.out_dir else Path(job_scoped_dir(job.job_id))
        dest = base / "research"
        images: Dict[str, dict] = {}
        for ref, spec in queries.items():
            if not isinstance(ref, str) or not _REF_RE.match(ref):
                return ImageResearchResult({}, [], success=False, error=f"invalid research ref {ref!r}")
            query, sources = (spec, None) if isinstance(spec, str) else (spec.get("query"), spec.get("sources"))
            if not isinstance(query, str) or not query.strip():
                return ImageResearchResult({}, [], success=False, error=f"research ref {ref!r} has no query")

            chosen = None
            for candidate in research_images(query, limit=self.candidates_per_query, sources=sources):
                try:
                    chosen = download_image(candidate, str(dest / ref))
                    break
                except Exception:
                    continue
            if chosen is None:
                return ImageResearchResult({}, [], success=False,
                                           error=f"no license-clean image found for ref {ref!r} (query {query!r})")
            record = asdict(chosen)
            record["citation"] = format_citation(chosen)
            images[ref] = record

        return ImageResearchResult(images=images, citations=[r["citation"] for r in images.values()])
