"""HTTPRestAdapter — paginates a REST API (ADR-0026 Section D).

Uses stdlib urllib.request (no requests dependency by default).
URL params built as dict — NOT string interpolation.
supports_pushdown=False (client-side FilterExpr)
supports_incremental=True (cursor / timestamp from manifest)
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

try:
    import requests as _requests  # type: ignore[import]
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from ..protocol import (
    BaseDataSourceAdapter,
    ColumnInfo,
    DataCursor,
    FilterExpr,
    PingResult,
    SecretEnv,
    SourceConfig,
    SourceQuery,
    SourceSchema,
    SourceSession,
    tcp_reachability_ping,
)


class _HTTPSession(SourceSession):
    def __init__(self, base_url: str, headers: dict[str, str]) -> None:
        self.base_url = base_url
        self.headers = headers

    def close(self) -> None:
        pass


class HTTPRestAdapter(BaseDataSourceAdapter):
    """Reads data from a paginated REST API."""

    adapter_name = "http_rest"
    display_name = "HTTP REST API"
    description = "Fetch data from a paginated HTTP REST endpoint."
    supported_formats = frozenset({"json"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "base_url":    {"type": "string"},
            "page_param":  {"type": "string", "default": "page"},
            "size_param":  {"type": "string", "default": "size"},
            "data_path":   {"type": "string", "default": ""},
        },
    }

    supports_streaming: bool = False
    supports_pushdown: bool = False  # client-side filters
    supports_schema_discovery: bool = True
    supports_incremental: bool = True

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _HTTPSession:
        base_url = config.raw.get("base_url", "")
        auth_header_name = config.raw.get("auth_header", "Authorization")
        # Read token from vault-injected env (BEARER_TOKEN or API_KEY)
        token = secret_env.get("BEARER_TOKEN") or secret_env.get("API_KEY")
        headers: dict[str, str] = {}
        if token:
            headers[auth_header_name] = f"Bearer {token}"
        return _HTTPSession(base_url, headers)

    def discover_schema(
        self, session: _HTTPSession, config: SourceConfig
    ) -> SourceSchema:
        """Infer schema from first page of results."""
        page = self._fetch_page(session, config, cursor=None, page_num=1)
        records = _extract_records(page, config)
        if not records:
            return SourceSchema(columns=[], source_format="http_rest")
        sample = records[0]
        columns = [ColumnInfo(name=k, dtype=_infer_dtype(v)) for k, v in sample.items()]
        return SourceSchema(columns=columns, source_format="http_rest")

    def create_cursor(
        self,
        session: _HTTPSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        pagination_mode = config.raw.get("pagination", "page")
        count = 0

        if pagination_mode == "cursor":
            cursor = None
            while True:
                page = self._fetch_page(session, config, cursor=cursor, page_num=None)
                records = _extract_records(page, config)
                if not records:
                    break
                for rec in records:
                    if query.columns:
                        rec = {k: rec[k] for k in query.columns if k in rec}
                    if _passes_filters(rec, query.filters):
                        yield rec
                        count += 1
                        if query.limit and count >= query.limit:
                            return
                # Advance cursor
                cursor_key = config.raw.get("cursor_field", "next_cursor")
                cursor = page.get(cursor_key)
                if not cursor:
                    break

        elif pagination_mode == "offset":
            offset = 0
            page_size = config.raw.get("page_size", 100)
            while True:
                page = self._fetch_page(session, config, cursor=None, page_num=None, offset=offset)
                records = _extract_records(page, config)
                if not records:
                    break
                for rec in records:
                    if query.columns:
                        rec = {k: rec[k] for k in query.columns if k in rec}
                    if _passes_filters(rec, query.filters):
                        yield rec
                        count += 1
                        if query.limit and count >= query.limit:
                            return
                if len(records) < page_size:
                    break
                offset += page_size

        else:  # page-based (default)
            page_num = 1
            while True:
                page = self._fetch_page(session, config, cursor=None, page_num=page_num)
                records = _extract_records(page, config)
                if not records:
                    break
                for rec in records:
                    if query.columns:
                        rec = {k: rec[k] for k in query.columns if k in rec}
                    if _passes_filters(rec, query.filters):
                        yield rec
                        count += 1
                        if query.limit and count >= query.limit:
                            return
                page_num += 1
                total_pages = page.get(config.raw.get("total_pages_field", "total_pages"))
                if total_pages is not None and page_num > int(total_pages):
                    break

    def _fetch_page(
        self,
        session: _HTTPSession,
        config: SourceConfig,
        cursor: Optional[str],
        page_num: Optional[int],
        offset: Optional[int] = None,
    ) -> dict:
        """Fetch a single page from the API.

        URL params are built as a dict — NEVER via string interpolation.
        """
        params: dict[str, Any] = {}
        page_param = config.raw.get("page_param", "page")
        cursor_param = config.raw.get("cursor_param", "cursor")
        offset_param = config.raw.get("offset_param", "offset")
        page_size = config.raw.get("page_size", 100)
        page_size_param = config.raw.get("page_size_param", "per_page")

        params[page_size_param] = page_size

        if cursor is not None:
            params[cursor_param] = cursor
        if page_num is not None:
            params[page_param] = page_num
        if offset is not None:
            params[offset_param] = offset

        # Merge static params from config (no interpolation)
        for k, v in config.raw.get("params", {}).items():
            params[k] = v

        endpoint = config.raw.get("endpoint", "")
        url = session.base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        if params:
            url = url + "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, headers=session.headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def estimate_rows(
        self,
        session: _HTTPSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _HTTPSession) -> None:
        session.close()

    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Credential-free reachability probe: TCP-connect to the API host.

        Parses ``base_url`` for host/port (defaulting to 443 for https, 80
        otherwise) and verifies the endpoint is reachable. Does NOT send the
        bearer token or hit the API — pure transport-layer reachability.
        """
        raw = config.raw if config is not None else {}
        base_url = raw.get("base_url", "")
        if not base_url:
            return PingResult(ok=False, latency_ms=0.0, detail="no base_url configured")
        parsed = urllib.parse.urlparse(base_url)
        host = parsed.hostname or ""
        if not host:
            return PingResult(ok=False, latency_ms=0.0, detail="invalid base_url")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return tcp_reachability_ping(host, port, timeout_s)


def _extract_records(page: dict, config: SourceConfig) -> list[dict]:
    """Extract the list of records from a page response."""
    records_key = config.raw.get("records_key", "data")
    data = page.get(records_key, page)
    if isinstance(data, list):
        return data
    return []


def _passes_filters(row: dict, filters: list[FilterExpr]) -> bool:
    for f in filters:
        val = row.get(f.col)
        if f.op == "=" and val != f.value:
            return False
        if f.op == "!=" and val == f.value:
            return False
        if f.op == "is_null" and val is not None:
            return False
    return True


def _infer_dtype(val: Any) -> str:
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "float"
    return "string"


__all__ = ["HTTPRestAdapter", "REQUESTS_AVAILABLE"]
