"""Local BM25 retrieval sidecar for the release demo (stdlib only).

Serves the contract of ``corvin_operator/bridges/shared/rag_query_engine.py``:

    POST /search   {"query": "<text>", "limit": <int>}
    200            {"results": [{"content", "score", "metadata", "source_url"}]}

The corpus is built from the synthetic dataset (``generator.py``): one document per
``songs`` row (track, artists, label, release date), per ``artists`` row and per
``albums`` row - the rule ``truth.json`` states under ``doc_corpus``. Scores are
BM25 (k1=1.5, b=0.75) scaled by the best score of the query, so they land in [0, 1].

Usage::

    BM25_DATA=/tmp/demo-data python3 -m uvicorn --app-dir <this dir> bm25_sidecar:app \\
        --host 127.0.0.1 --port 8790
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

_TOKEN = re.compile(r"\w+", re.UNICODE)
K1, B = 1.5, 0.75


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def load_documents(data_dir: Path) -> list[dict]:
    docs: list[dict] = []
    tables = data_dir / "tables"
    with (tables / "songs.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            docs.append({
                "id": r["track_uri"], "kind": "track",
                "content": (f"Track: {r['track_name']} | Artists: {r['artist_names']} | "
                            f"Label: {r['label']} | Released: {r['release_date']}"),
            })
    with (tables / "artists.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            docs.append({"id": r["artist_uri"], "kind": "artist",
                         "content": f"Artist: {r['artist_name']}"})
    with (tables / "albums.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            docs.append({
                "id": r["album_uri"], "kind": "album",
                "content": (f"Album: {r['album_name']} | Artists: {r['artist_names']} | "
                            f"Label: {r['label']} | Released: {r['release_date']}"),
            })
    return docs


class Index:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.tf = [Counter(tokens(d["content"])) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, limit: int) -> list[dict]:
        q = [t for t in tokens(query) if t in self.idf]
        if not q:
            return []
        scored: list[tuple[float, int]] = []
        for i, c in enumerate(self.tf):
            score = 0.0
            for t in q:
                f = c.get(t, 0)
                if f:
                    score += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[i] / self.avg))
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        top = scored[0][0] if scored else 1.0
        return [
            {
                "content": self.docs[i]["content"],
                "score": round(s / top, 4),
                "metadata": {"kind": self.docs[i]["kind"], "id": self.docs[i]["id"]},
                "source_url": None,
            }
            for s, i in scored[: max(1, min(limit, 100))]
        ]


def make_app(index: Index):
    """ASGI application (run it with uvicorn, which CorvinOS already depends on)."""

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        async def reply(status: int, body: bytes) -> None:
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        if scope["method"] != "POST" or scope["path"].rstrip("/") != "/search":
            await reply(404, b'{"error": "not found"}')
            return
        raw = b""
        while True:
            message = await receive()
            raw += message.get("body", b"")
            if not message.get("more_body"):
                break
        try:
            body = json.loads(raw or b"{}")
            results = index.search(str(body.get("query", "")), int(body.get("limit", 5)))
        except (ValueError, TypeError):
            await reply(400, b'{"error": "bad request"}')
            return
        await reply(200, json.dumps({"results": results}).encode())

    return app


# uvicorn entry point:  BM25_DATA=<generator output> uvicorn bm25_sidecar:app --port 8790
app = make_app(Index(load_documents(Path(os.environ["BM25_DATA"])))) if os.environ.get("BM25_DATA") else None
