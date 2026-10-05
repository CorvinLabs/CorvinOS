"""Image research worker for Video Producer (ADR-2221).

Fetches images from a fixed, keyless host allowlist — Wikimedia Commons only —
never a generic web search. Commons attaches a machine-readable licence to
every file. The NASA Image Library was dropped (adversarial review
2026-10-05): its API carries no licence field, and the library holds
copyrighted and ESA (CC BY) images that the worker had labelled
"Public Domain" across the board.
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

import html
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
})
SOURCES = frozenset({"commons"})

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
# Decoded-size cap. Commons accepts user uploads; a 163 KB PNG of
# 12500x13500 px passed Pillow's default limit and cost ~900 MB to convert.
MAX_IMAGE_PIXELS = 24_000_000
MAX_ATTRIBUTION = 160
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
    """Plain text from extmetadata HTML: drop tags (also an unclosed one),
    then decode entities once so "&amp;" does not reach the frame as text.
    The compiler escapes the result again for HTML."""
    no_tags = re.sub(r"<[^>]*(>|$)", "", value or "")
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


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
        artist = _strip_html(extmeta.get("Artist", {}).get("value", ""))
        attribution_required = str(extmeta.get("AttributionRequired", {}).get("value", "")).lower() == "true"
        if not artist:
            if attribution_required:
                continue  # the licence demands a name the file does not give
            artist = "Unknown"
        if len(artist) > MAX_ATTRIBUTION:
            continue  # never shorten a credit line; take another candidate
        title = page.get("title", "").removeprefix("File:")
        results.append(ResearchedImage(
            title=title,
            source_url=url,
            license=license_norm,
            attribution_text=artist,
            fetched_at=now,
            page_url=imageinfo.get("descriptionurl"),
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
            w, h = im.size
            if w * h > MAX_IMAGE_PIXELS:
                raise ValueError(f"{w}x{h} px exceeds {MAX_IMAGE_PIXELS} px")
            im.load()  # full decode: verify() skips JPEG pixel data, a truncated file passed it
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
    """Search the allowed sources (today: Commons only). License-filtered at
    the per-source level. An unreachable source yields no candidates (the
    worker then fails the ref); an unknown source name is an error."""
    sources = sources or sorted(SOURCES)
    unknown = sorted(set(sources) - SOURCES)
    if unknown:
        raise ValueError(f"research_worker: unknown image source(s) {unknown}; allowed: {sorted(SOURCES)}")
    results: list[ResearchedImage] = []
    try:
        results.extend(search_wikimedia_commons(query, limit=limit))
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
    "sources": ["commons"]}}. For every ref it downloads the first
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
            try:
                candidates = research_images(query, limit=self.candidates_per_query, sources=sources)
            except ValueError as e:
                return ImageResearchResult({}, [], success=False, error=str(e))
            for candidate in candidates:
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
