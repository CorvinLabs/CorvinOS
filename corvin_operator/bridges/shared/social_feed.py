"""Layer 39 CorvinFed — local feed store and post publish/retract (M1).

M1 scope: posts.db CRUD (SQLite + FTS5), local publish, local retract.
No HTTP push yet — that is social_registry.py for M2.

Must NOT do:
  - Don't import anthropic (CI AST lint).
  - Don't import any ML library (trending is boost-count only — ADR-0053).
  - Don't put post content in audit event details.
  - Don't allow WorkerEngine spawn from any code path in this module.
  - Don't allow publish without prior social consent.

Audit details allow-list (per ADR-0053):
  post_id, actor_id, post_type, tag_count, attachment_count, recipient_count,
  failure_count, reason, duration_ms, compliance_zone, rate_limit_name.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
    from .audit import audit_event  # type: ignore[import-not-found]
    from . import social_actor  # type: ignore[import-not-found]
    from . import social_consent  # type: ignore[import-not-found]
    from . import social_envelope  # type: ignore[import-not-found]
    from .social_sanitizer import sanitize_post_content, frame_for_llm, InjectionAttempt  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]
    from audit import audit_event  # type: ignore[import-not-found]
    import social_actor  # type: ignore[import-not-found]
    import social_consent  # type: ignore[import-not-found]
    import social_envelope  # type: ignore[import-not-found]
    from social_sanitizer import sanitize_post_content, frame_for_llm, InjectionAttempt  # type: ignore[import-not-found]

# Re-export for callers that want a single import point.
ConsentRequired = social_consent.ConsentRequired


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FeedError(Exception):
    """Raised on operational failures in the feed store or publish flow."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS posts (
    post_id          TEXT PRIMARY KEY,
    actor_id         TEXT NOT NULL,
    post_type        TEXT NOT NULL,
    content          TEXT,
    visibility       TEXT NOT NULL,
    in_reply_to      TEXT,
    boost_of         TEXT,
    issued_at        REAL NOT NULL,
    received_at      REAL NOT NULL,
    is_ai            INTEGER NOT NULL,
    tag_count        INTEGER,
    attachment_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_posts_actor ON posts(actor_id);
CREATE INDEX IF NOT EXISTS idx_posts_issued ON posts(issued_at);

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    content, post_id UNINDEXED, actor_id UNINDEXED
);
"""

# ---------------------------------------------------------------------------
# SocialFeedStore
# ---------------------------------------------------------------------------

class SocialFeedStore:
    """SQLite-backed store for local and received CorvinFed posts (M1)."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id
        db_path = self._db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------ internal

    def _db_path(self) -> Path:
        return tenant_global_dir(self._tenant_id) / "social" / "posts.db"

    def _init_schema(self) -> None:
        try:
            self._conn.executescript(_DDL)
            self._conn.commit()
        except sqlite3.OperationalError as exc:
            raise FeedError(f"schema init failed: {exc}") from exc

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["is_ai"] = bool(d.get("is_ai"))
        return d

    # ------------------------------------------------------------------ store

    def store_post(self, envelope_dict: dict, *, is_own: bool = False) -> None:
        """Store a received or locally published post.

        For own posts (is_own=True): ``content`` in the envelope is the raw
        text; this method sanitizes it via sanitize_post_content() which now
        returns RAW (unframed) content (FIX-1/FIX-2).
        For received posts (is_own=False): content is already sanitized (raw)
        by the caller (inbound path in social_http_server.py, M2).
        Stored content is ALWAYS raw/unframed — call get_post_framed() when
        presenting to an LLM context.
        Writes to both the posts table and the posts_fts virtual table.
        """
        post_id = envelope_dict["post_id"]
        actor_id = envelope_dict["actor_id"]
        raw_content = envelope_dict.get("content", "") or ""

        if is_own:
            try:
                content = sanitize_post_content(raw_content, actor_id, post_id)
            except InjectionAttempt as exc:
                raise FeedError(f"injection attempt in own post: {exc.reason}") from exc
        else:
            content = raw_content

        now = time.time()
        tags = envelope_dict.get("tags") or []
        attachments = envelope_dict.get("attachments") or []

        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO posts (
                    post_id, actor_id, post_type, content, visibility,
                    in_reply_to, boost_of, issued_at, received_at,
                    is_ai, tag_count, attachment_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    actor_id,
                    envelope_dict.get("post_type", "status"),
                    content,
                    envelope_dict.get("visibility", "public"),
                    envelope_dict.get("in_reply_to"),
                    envelope_dict.get("boost_of"),
                    envelope_dict.get("issued_at", now),
                    now,
                    1 if envelope_dict.get("is_ai") else 0,
                    len(tags),
                    len(attachments),
                ),
            )
            # Upsert FTS5 row (delete-then-insert for idempotency).
            self._conn.execute(
                "DELETE FROM posts_fts WHERE post_id = ?", (post_id,)
            )
            self._conn.execute(
                "INSERT INTO posts_fts (content, post_id, actor_id) VALUES (?, ?, ?)",
                (content, post_id, actor_id),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise FeedError(f"store_post failed: {exc}") from exc

    # ------------------------------------------------------------------ delete

    def delete_post(self, post_id: str) -> bool:
        """Delete post by post_id from both tables. Returns True if deleted."""
        try:
            cur = self._conn.execute(
                "DELETE FROM posts WHERE post_id = ?", (post_id,)
            )
            self._conn.execute(
                "DELETE FROM posts_fts WHERE post_id = ?", (post_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise FeedError(f"delete_post failed: {exc}") from exc

    # ------------------------------------------------------------------ read

    def get_post(self, post_id: str) -> dict | None:
        """Return post dict or None."""
        try:
            cur = self._conn.execute(
                "SELECT * FROM posts WHERE post_id = ?", (post_id,)
            )
            row = cur.fetchone()
        except sqlite3.Error as exc:
            raise FeedError(f"get_post failed: {exc}") from exc
        return self._row_to_dict(row) if row else None

    def get_post_framed(
        self, post_id: str, fence_token: str | None = None
    ) -> dict | None:
        """Return post dict with content wrapped via frame_for_llm().

        Use this method when presenting a post to an LLM context.
        The fence_token is NOT persisted — it is caller-local per call.
        Returns None if the post does not exist.
        Raises InjectionAttempt if content contains a framing delimiter.
        """
        post = self.get_post(post_id)
        if post is None:
            return None
        framed = frame_for_llm(
            content=post.get("content") or "",
            actor_id=post.get("actor_id") or "",
            post_id=post_id,
            fence_token=fence_token,
        )
        result = dict(post)
        result["content"] = framed
        return result

    def list_posts(
        self,
        since: float | None = None,
        limit: int = 50,
        tag: str | None = None,
        actor_id: str | None = None,
    ) -> list[dict]:
        """List posts ordered by issued_at DESC.

        Filters:
          since     — include only posts with issued_at > since (unix timestamp)
          limit     — max rows returned
          tag       — FTS5 match on #{tag} within content
          actor_id  — filter by actor_id
        """
        if tag is not None:
            # FTS5 tag search: match "#<tag>" in content.
            fts_query = f"#{tag}"
            try:
                cur = self._conn.execute(
                    """
                    SELECT p.*
                    FROM posts p
                    JOIN posts_fts f ON f.post_id = p.post_id
                    WHERE posts_fts MATCH ?
                    {}{}
                    ORDER BY p.issued_at DESC
                    LIMIT ?
                    """.format(
                        " AND p.issued_at > ?" if since is not None else "",
                        " AND p.actor_id = ?" if actor_id is not None else "",
                    ),
                    _bind(fts_query, since, actor_id, limit),
                )
            except sqlite3.Error as exc:
                raise FeedError(f"list_posts (tag) failed: {exc}") from exc
        else:
            where_clauses = []
            params: list[Any] = []
            if since is not None:
                where_clauses.append("issued_at > ?")
                params.append(since)
            if actor_id is not None:
                where_clauses.append("actor_id = ?")
                params.append(actor_id)
            where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)
            try:
                cur = self._conn.execute(
                    f"SELECT * FROM posts {where} ORDER BY issued_at DESC LIMIT ?",
                    params,
                )
            except sqlite3.Error as exc:
                raise FeedError(f"list_posts failed: {exc}") from exc

        return [self._row_to_dict(r) for r in cur.fetchall()]

    def timeline(self, limit: int = 50) -> list[dict]:
        """Return own + received posts ordered by issued_at DESC."""
        try:
            cur = self._conn.execute(
                "SELECT * FROM posts ORDER BY issued_at DESC LIMIT ?", (limit,)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except sqlite3.Error as exc:
            raise FeedError(f"timeline failed: {exc}") from exc

    def trending_by_boost_count(
        self, window_hours: int = 24, limit: int = 20
    ) -> list[dict]:
        """Return posts with most boosts in the window.

        Algorithm: boost_count = count of boost posts referencing this post_id.
        MUST NOT import any ML library. Pure SQL, boost-count only (ADR-0053).
        """
        window_start = time.time() - window_hours * 3600
        try:
            cur = self._conn.execute(
                """
                SELECT p.*,
                       COALESCE(bc.boost_count, 0) AS boost_count
                FROM posts p
                JOIN (
                    SELECT boost_of, COUNT(*) AS boost_count
                    FROM posts
                    WHERE post_type = 'boost' AND issued_at > ?
                    GROUP BY boost_of
                    ORDER BY boost_count DESC
                    LIMIT ?
                ) bc ON bc.boost_of = p.post_id
                ORDER BY bc.boost_count DESC
                LIMIT ?
                """,
                (window_start, limit, limit),
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except sqlite3.Error as exc:
            raise FeedError(f"trending_by_boost_count failed: {exc}") from exc

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 full-text search over post content."""
        try:
            cur = self._conn.execute(
                """
                SELECT p.*
                FROM posts p
                JOIN posts_fts f ON f.post_id = p.post_id
                WHERE posts_fts MATCH ?
                ORDER BY p.issued_at DESC
                LIMIT ?
                """,
                (query, limit),
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except sqlite3.Error as exc:
            raise FeedError(f"search failed: {exc}") from exc

    def boost_count(self, post_id: str) -> int:
        """Return the number of boost posts referencing post_id."""
        try:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM posts WHERE post_type = 'boost' AND boost_of = ?",
                (post_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error as exc:
            raise FeedError(f"boost_count failed: {exc}") from exc

    def delete_actor_posts(self, actor_id: str) -> int:
        """Delete all posts by actor_id. Returns count deleted.

        Used by /social-leave or retract-all. Part of the L36 ErasureHandler
        for the social.participation data class.
        """
        try:
            # Collect post_ids first to clean FTS5.
            cur = self._conn.execute(
                "SELECT post_id FROM posts WHERE actor_id = ?", (actor_id,)
            )
            post_ids = [r[0] for r in cur.fetchall()]
            if not post_ids:
                return 0
            placeholders = ",".join("?" * len(post_ids))
            self._conn.execute(
                f"DELETE FROM posts_fts WHERE post_id IN ({placeholders})",
                post_ids,
            )
            del_cur = self._conn.execute(
                "DELETE FROM posts WHERE actor_id = ?", (actor_id,)
            )
            self._conn.commit()
            return del_cur.rowcount
        except sqlite3.Error as exc:
            raise FeedError(f"delete_actor_posts failed: {exc}") from exc

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _bind(fts_query: str, since: float | None, actor_id: str | None, limit: int) -> list:
    """Build the parameter list for the FTS5 tag query in list_posts."""
    params: list[Any] = [fts_query]
    if since is not None:
        params.append(since)
    if actor_id is not None:
        params.append(actor_id)
    params.append(limit)
    return params


def _load_actor_info(tenant_id: str | None) -> tuple[str, str, str]:
    """Return (actor_id, key_id, private_key_hex) for the local actor."""
    try:
        actor_doc = social_actor.load_actor_document(tenant_id)
    except social_actor.ActorError as exc:
        raise FeedError(f"actor document not available: {exc.reason}") from exc
    actor_id = actor_doc.get("instance_id", "")
    key_id = actor_doc.get("public_key", {}).get("key_id", "")
    try:
        private_key_hex, _ = social_actor.load_keypair(tenant_id)
    except social_actor.ActorError as exc:
        raise FeedError(f"keypair not available: {exc.reason}") from exc
    return actor_id, key_id, private_key_hex


# ---------------------------------------------------------------------------
# publish_post
# ---------------------------------------------------------------------------

def publish_post(
    content: str,
    post_type: str = "status",
    visibility: str = "public",
    content_warning: str | None = None,
    in_reply_to: str | None = None,
    boost_of: str | None = None,
    tags: list[str] | None = None,
    tenant_id: str | None = None,
    channel: str = "",
    chat_key: str = "",
) -> dict:
    """Full local publish flow (M1 — no HTTP push; M2 adds federation push).

    Steps:
    1. Check consent via social_consent.require_consent().
    2. Sanitize content via sanitize_post_content().
    3. Load actor_id and key_id from actor document.
    4. Load keypair for signing.
    5. Build PostEnvelope dict via social_envelope.build_envelope().
    6. Sign via social_envelope.sign_envelope().
    7. Write social.post_published to L16 audit (BEFORE store).
       Details allow-list: post_id, actor_id, post_type, tag_count,
       attachment_count.
    8. Store in posts.db via SocialFeedStore.
    9. Return the signed envelope dict.

    Raises:
      ConsentRequired  — if social participation not enabled.
      FeedError        — on operational failure.
    """
    # 1. Consent gate.
    social_consent.require_consent(tenant_id)

    # 2. Build a temporary post_id; build_envelope will generate the real post_id.
    # Pattern: pre-allocate a uuid4, pass it to sanitize, then pass the
    # sanitized text and the same id to build_envelope.
    import uuid
    post_id = str(uuid.uuid4())

    # 2. Sanitize (FIX-1/FIX-2: sanitize_post_content returns RAW content now).
    # We do an early check before loading the actor_id to fail fast.
    try:
        sanitized_content = sanitize_post_content(content, "", post_id)
    except InjectionAttempt as exc:
        audit_event(
            "social.content_policy_blocked",
            channel=channel,
            chat_key=chat_key,
            severity="WARNING",
            details={"reason": exc.reason},
        )
        raise FeedError(f"content injection attempt: {exc.reason}") from exc

    # 3 + 4. Load actor info.
    actor_id, key_id, private_key_hex = _load_actor_info(tenant_id)

    # sanitized_content is already raw; no need to re-sanitize.
    # (actor_id was not embedded in the sanitized content under the new API)

    # 5. Build envelope (uses the pre-allocated post_id by injecting it).
    tags_list: list[str] = tags or []
    envelope = social_envelope.build_envelope(
        actor_id=actor_id,
        post_type=post_type,
        visibility=visibility,
        content=sanitized_content,
        content_warning=content_warning,
        in_reply_to=in_reply_to,
        boost_of=boost_of,
        tags=tags_list,
        attachments=[],
        is_ai=True,  # Corvin actors are always AI — hard-coded; no override.
        ai_model="claude-sonnet",
        key_id=key_id,
    )
    # Overwrite the random post_id from build_envelope with our pre-allocated one
    # so that the sanitized framing matches the stored post_id.
    envelope["post_id"] = post_id

    # 6. Sign.
    signature = social_envelope.sign_envelope(envelope, private_key_hex)
    envelope["signature"] = signature

    tag_count = len(tags_list)
    attachment_count = 0

    # 7. Audit BEFORE store.
    audit_event(
        "social.post_published",
        channel=channel,
        chat_key=chat_key,
        severity="INFO",
        details={
            "post_id": post_id,
            "actor_id": actor_id,
            "post_type": post_type,
            "tag_count": tag_count,
            "attachment_count": attachment_count,
        },
    )

    # 8. Store (content is already sanitized; pass is_own=False to skip re-sanitization).
    store = SocialFeedStore(tenant_id)
    store.store_post(envelope, is_own=False)

    # 9. Return.
    return envelope


# ---------------------------------------------------------------------------
# retract_post
# ---------------------------------------------------------------------------

def retract_post(
    post_id: str,
    tenant_id: str | None = None,
    channel: str = "",
    chat_key: str = "",
) -> dict:
    """Local retract flow (M1 — no HTTP push; M2 adds retract propagation).

    Steps:
    1. Check consent.
    2. Load the post from posts.db; verify actor_id matches local actor_id.
    3. Build retract PostEnvelope (post_type="retract", in_reply_to=post_id).
    4. Sign the retract envelope.
    5. Write social.retract_sent (INFO) to audit; details: post_id,
       recipient_count=0 (M1 — no HTTP push).
    6. Delete post from posts.db.
    7. Return the signed retract envelope.

    Raises:
      ConsentRequired  — if social participation not enabled.
      FeedError        — if post not found, actor mismatch, or other failure.
    """
    # 1. Consent gate.
    social_consent.require_consent(tenant_id)

    # 2. Load and verify.
    store = SocialFeedStore(tenant_id)
    existing = store.get_post(post_id)
    if existing is None:
        raise FeedError(f"post not found: {post_id!r}")

    actor_id, key_id, private_key_hex = _load_actor_info(tenant_id)

    if existing["actor_id"] != actor_id:
        raise FeedError(
            f"actor mismatch: post belongs to {existing['actor_id']!r}, "
            f"local actor is {actor_id!r}"
        )

    # 3. Build retract envelope.
    envelope = social_envelope.build_envelope(
        actor_id=actor_id,
        post_type="retract",
        visibility="public",
        content="",
        content_warning=None,
        in_reply_to=post_id,  # references the retracted post
        boost_of=None,
        tags=[],
        attachments=[],
        is_ai=True,
        ai_model="claude-sonnet",
        key_id=key_id,
    )

    # 4. Sign.
    signature = social_envelope.sign_envelope(envelope, private_key_hex)
    envelope["signature"] = signature

    # 5. Audit BEFORE deletion.
    audit_event(
        "social.retract_sent",
        channel=channel,
        chat_key=chat_key,
        severity="INFO",
        details={
            "post_id": post_id,
            "actor_id": actor_id,
            "post_type": "retract",
            "recipient_count": 0,  # M1 — no HTTP push yet
        },
    )

    # 6. Delete from posts.db.
    store.delete_post(post_id)

    # 7. Return signed retract envelope.
    return envelope


__all__ = [
    "ConsentRequired",
    "FeedError",
    "SocialFeedStore",
    "publish_post",
    "retract_post",
    "frame_for_llm",
]
