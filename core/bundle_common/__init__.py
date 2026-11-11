"""Validators shared by the console's two ZIP formats (ADR-2323 Decision 11).

Lifted without a behaviour change from ``core/forge_bundle/validate.py`` (ADR-2229): the container
rules for one archive (:mod:`.names`), the compression-ratio rule with a shared uncompressed budget and
the budgeted inflate of gzip/bz2/xz streams (:mod:`.ratio`), and the credential-shape scan with its
linear JWT check (:mod:`.secrets`). One scanner, one ratio rule, one JWT check for the Forge Bundle and
the project bundle (``core/projects/bundle.py``).

Limits are parameters (``limits: Mapping``); the default is the Forge Bundle's ``LIMITS``. Every refusal
is a :class:`BundleCheckError` with the stage, the reason the Forge Bundle has always reported (it may
name an entry) and ``detail`` - a short reason that never carries an entry name or any content, for a
caller whose message must not echo the archive.
"""
from __future__ import annotations


class BundleCheckError(Exception):
    """A shared validator refused the archive. ``reason`` is the Forge Bundle wording (may name an entry); ``detail`` is
    content-free."""

    def __init__(self, stage: str, reason: str, detail: str = ""):
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = reason
        self.detail = detail or "the archive is not safe to read"


__all__ = ["BundleCheckError"]
