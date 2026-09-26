"""YouTube Data API v3 client -- real upload when configured, honest "not
configured" when not.

Real YouTube upload structurally requires an operator-provisioned Google
Cloud OAuth2 client (own console project, consent screen, a stored token) --
an agent cannot provision that. Until 2026-09-26 this module always returned
a hardcoded fake video id ("dQw4w9WgXcQ") regardless of authentication state,
so every "upload" silently lied about having happened. This version never
fabricates a video id: with no token file configured (the default on every
fresh install), every call reports ``status: "not_configured"`` with a
`reason` explaining exactly what's missing, and the durable local MP4 +
generated metadata are still produced by the caller (``uploader.py``) either
way -- "local-only mode".

Configuration (once an operator has a Google Cloud OAuth2 client):
    CORVIN_YOUTUBE_TOKEN_PATH -- path to a stored ``google.oauth2.credentials
    .Credentials``-compatible authorized-user JSON file (refresh token
    included). There is no interactive consent-flow entry point here --
    obtaining that file is a one-time, human, out-of-band step (e.g. via
    ``google-auth-oauthlib``'s own ``InstalledAppFlow.run_local_server()``
    run once, locally, by the operator).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

_YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def _token_path() -> Optional[Path]:
    raw = os.environ.get("CORVIN_YOUTUBE_TOKEN_PATH", "").strip()
    return Path(raw).expanduser() if raw else None


class YouTubeAPI:
    """Real YouTube Data API v3 client, fail-closed to "not configured"."""

    def __init__(self, oauth_token: Optional[str] = None):
        """``oauth_token``, when given, is used as a bare short-lived access
        token (dependency injection for callers/tests that already manage
        their own token) -- real usage is expected to come from
        ``CORVIN_YOUTUBE_TOKEN_PATH`` (a refreshable token file) instead.
        Either way this produces a real ``google.oauth2.credentials
        .Credentials`` object; there is no separate "fake" authenticated
        state -- a bad token fails at the real API call, honestly, rather
        than being accepted here and faked downstream."""
        self._credentials = None
        self._load_error: Optional[str] = None
        if oauth_token:
            self._try_use_injected_token(oauth_token)
        else:
            self._try_load_credentials()

    def _try_use_injected_token(self, oauth_token: str) -> None:
        try:
            from google.oauth2.credentials import Credentials
        except ImportError:
            self._load_error = (
                "google-auth-oauthlib/google-api-python-client not installed "
                "(pip install 'corvinos[youtube]')"
            )
            return
        self._credentials = Credentials(token=oauth_token)

    def _try_load_credentials(self) -> None:
        token_path = _token_path()
        if token_path is None:
            self._load_error = "CORVIN_YOUTUBE_TOKEN_PATH not set"
            return
        if not token_path.exists():
            self._load_error = f"token file not found: {token_path}"
            return

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError:
            self._load_error = (
                "google-auth-oauthlib/google-api-python-client not installed "
                "(pip install 'corvinos[youtube]')"
            )
            return

        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path), scopes=[_YOUTUBE_UPLOAD_SCOPE]
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            if not creds.valid:
                self._load_error = "stored YouTube credentials are invalid/expired"
                return
            self._credentials = creds
        except Exception as e:  # noqa: BLE001 -- any load failure -> not configured
            self._load_error = f"failed to load YouTube credentials: {e}"

    async def is_authenticated(self) -> bool:
        return self._credentials is not None

    async def authenticate(self, client_id: str, client_secret: str) -> bool:
        """No interactive consent-flow entry point here (see module
        docstring) -- this reports whether a valid token is ALREADY loaded,
        it never silently claims success for tokens it can't verify."""
        return self._credentials is not None

    async def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "private",  # private, unlisted, public
    ) -> dict[str, Any]:
        """Upload video to YouTube, or report why it can't."""
        if self._credentials is None:
            logger.info(f"YouTube upload skipped (not configured): {self._load_error}")
            return {
                "status": "not_configured",
                "video_id": None,
                "url": None,
                "reason": self._load_error,
            }

        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError

        try:
            youtube = build("youtube", "v3", credentials=self._credentials)
            body = {
                "snippet": {"title": title, "description": description, "tags": tags},
                "status": {"privacyStatus": privacy},
            }
            media = MediaFileUpload(video_path, chunksize=-1, resumable=False)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = request.execute()
            video_id = response["id"]
            return {
                "status": "uploaded",
                "video_id": video_id,
                "url": f"https://youtube.com/watch?v={video_id}",
            }
        except HttpError as e:
            logger.error(f"YouTube upload failed: {e}")
            return {"status": "error", "video_id": None, "url": None, "reason": str(e)}

    async def update_video_metadata(
        self,
        video_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Update video metadata (title, description, tags)."""
        if self._credentials is None:
            return False
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        try:
            youtube = build("youtube", "v3", credentials=self._credentials)
            youtube.videos().update(
                part="snippet",
                body={"id": video_id, "snippet": metadata},
            ).execute()
            return True
        except HttpError as e:
            logger.error(f"YouTube metadata update failed: {e}")
            return False

    async def upload_captions(
        self,
        video_id: str,
        srt_path: str,
        language: str = "en",
    ) -> bool:
        """Upload SRT subtitle file."""
        if self._credentials is None:
            return False
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError

        try:
            youtube = build("youtube", "v3", credentials=self._credentials)
            youtube.captions().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "language": language,
                        "name": "",
                        "isDraft": False,
                    }
                },
                media_body=MediaFileUpload(srt_path),
            ).execute()
            return True
        except HttpError as e:
            logger.error(f"YouTube caption upload failed: {e}")
            return False

    async def set_thumbnail(
        self,
        video_id: str,
        thumbnail_path: str,
    ) -> bool:
        """Upload custom thumbnail."""
        if self._credentials is None:
            return False
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError

        try:
            youtube = build("youtube", "v3", credentials=self._credentials)
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path),
            ).execute()
            return True
        except HttpError as e:
            logger.error(f"YouTube thumbnail upload failed: {e}")
            return False
