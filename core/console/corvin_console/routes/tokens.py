"""Token management routes — removed.

Static atlr_* bearer tokens have been removed from Corvin.
For local deployments the loopback auto-login (/auth/local-login)
creates sessions directly. OIDC/Google OAuth will provide cloud auth.

This module is kept as an empty stub to avoid ImportError from app.py.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
