"""Sign and verify Skill packages against the install's own operator keys (ADR-2252).

What is signed is NOT just ``skill.json``: it is the manifest together with the package's
own ``.forge/checksum.sha256`` (the SHA-256 of every file). The installer re-hashes every
file in the ZIP against that list, so a signature over the manifest alone cannot be
carried onto a package whose code was swapped.

Trust anchor: the public keys in THIS install's operator key store
(``<tenant>/global/operator_keys``, :class:`OperatorKeyManager`). A key shipped inside the
package would prove nothing (a self-signed package verifies itself), so none is ever read
from the ZIP. No key configured => nothing is signed and nothing verifies; that is the
honest state of an install whose operator never generated a key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import logging
from pathlib import Path
from typing import List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

SIGNATURE_NAME = "skill.sig"
MANIFEST_NAME = "skill.json"
CHECKSUM_REL = ".forge/checksum.sha256"
#: Entries a verified package may carry besides the files its checksum list names.
ALLOWED_FORGE_ENTRIES = frozenset({".forge/checksum.sha256"})
#: Metadata the packager writes BEFORE hashing, so the signature covers it (a verified install
#: must not carry files anyone could have rewritten after signing).
SIGNED_FORGE_ENTRIES = (".forge/generation_context.json", ".forge/audit_trail.jsonl")
_DOMAIN = b"corvin-skill-package-v1\n"
_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH)


def _home(corvin_home: Optional[Path] = None) -> Path:
    if corvin_home is None:
        from core.paths.tenant import corvin_home as _resolve  # honours CORVIN_HOME
        return Path(_resolve())
    return Path(corvin_home)


def _manager(corvin_home: Optional[Path] = None):
    """The key manager, or None when this install never generated a key.

    ``OperatorKeyManager.__init__`` creates its directory, so existence is checked first:
    merely asking whether packages can be verified must not create a key store.
    """
    home = _home(corvin_home)
    if not (home / "tenants" / "_default" / "global" / "operator_keys" / "metadata.json").is_file():
        return None
    from core.skills.signature.key_manager import OperatorKeyManager
    return OperatorKeyManager(home)


class KeyStoreUnreadable(RuntimeError):
    """A key store exists but cannot be read. Distinct from "no key store": a signed package
    must then be REFUSED (ADR-0562: a trust check that cannot decide refuses), not installed
    as "unverifiable"."""


def key_store_present(corvin_home: Optional[Path] = None) -> bool:
    """True when ANY trace of a key store exists - a dangling/symlinked metadata.json or a key file
    without metadata included. Only a store with no trace at all means "never generated a key";
    anything else that cannot be read must refuse (review R2-AP-03)."""
    d = _home(corvin_home) / "tenants" / "_default" / "global" / "operator_keys"
    try:
        return os.path.lexists(d) and (not d.is_dir() or any(True for _ in d.iterdir()))
    except OSError:
        return True


def collision_key(name: str) -> str:
    """The key under which two archive names land on the SAME file on a case-insensitive /
    normalising file system (Windows, macOS). One definition for packager and installer, so the
    packager never signs what the installer refuses (review R2-AP-07)."""
    import unicodedata

    return unicodedata.normalize("NFC", name).lower()


def trusted_public_keys(corvin_home: Optional[Path] = None) -> List:
    """Current + recently rotated operator public keys.

    ``[]`` only when this install never generated a key. A store that exists but cannot be read
    (or yields no usable key) raises :class:`KeyStoreUnreadable`.
    """
    mgr = _manager(corvin_home)
    meta = _home(corvin_home) / "tenants" / "_default" / "global" / "operator_keys" / "metadata.json"
    if mgr is not None and meta.is_symlink():
        raise KeyStoreUnreadable("operator key store metadata is a symbolic link")
    if mgr is None:
        if key_store_present(corvin_home):
            raise KeyStoreUnreadable("operator key store metadata is not a readable file")
        return []
    try:
        keys = list(mgr.all_trusted_public_keys())
    except Exception as exc:  # noqa: BLE001
        logger.warning("operator key store unreadable (%s)", type(exc).__name__)
        raise KeyStoreUnreadable(type(exc).__name__) from exc
    if not keys:
        raise KeyStoreUnreadable("operator key store holds no usable key")
    return keys


def signing_key(corvin_home: Optional[Path] = None):
    """The current private key, or None (no key store, or the key is password-protected)."""
    mgr = _manager(corvin_home)
    if mgr is None:
        return None
    try:
        return mgr.load_current_private_key(None)
    except Exception as exc:  # noqa: BLE001 — packaging then proceeds unsigned, visibly
        logger.warning("operator signing key not usable without a password (%s)", type(exc).__name__)
        return None


def signing_payload(manifest_raw: bytes, checksum_raw: bytes) -> bytes:
    """Domain-separated, canonical bytes that the signature covers."""
    try:
        obj = json.loads(manifest_raw.decode("utf-8"))
        canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        canon = manifest_raw  # unparseable manifest: sign the bytes as they are; install refuses it anyway
    return _DOMAIN + canon + b"\n" + checksum_raw


def sign(manifest_raw: bytes, checksum_raw: bytes, private_key) -> str:
    sig = private_key.sign(signing_payload(manifest_raw, checksum_raw), _PSS, hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def verify(manifest_raw: bytes, checksum_raw: bytes, signature_b64: str, public_keys) -> bool:
    try:
        sig = base64.b64decode(signature_b64.strip(), validate=True)
    except Exception:  # noqa: BLE001
        return False
    payload = signing_payload(manifest_raw, checksum_raw)
    for key in public_keys:
        try:
            key.verify(sig, payload, _PSS, hashes.SHA256())
            return True
        except InvalidSignature:
            continue
        except Exception:  # noqa: BLE001 — a malformed key never verifies
            continue
    return False


def parse_checksums(checksum_raw: bytes) -> dict:
    """``{relative_path: 'sha256:<hex>'}`` from the packager's ``<hash> <path>`` lines."""
    out = {}
    for line in checksum_raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, path = line.partition(" ")
        if digest and path:
            out[path] = digest
    return out


def sha256_tag(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
