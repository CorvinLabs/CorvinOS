"""Marketplace Download Manager for Plugin System (ADR-0XXX Phase 2a).

Handles:
- Async download from marketplace
- Checksum verification (SHA256)
- ZIP extraction
- Error handling (network, corrupt, etc.)
"""

import hashlib
import zipfile
from pathlib import Path
from typing import Optional
import aiohttp


# ── Exceptions ─────────────────────────────────────────────────────────────

class DownloadError(Exception):
    """Failed to download plugin from marketplace."""
    pass


class ChecksumMismatchError(Exception):
    """Plugin checksum verification failed."""
    pass


# ── MarketplaceDownloadManager ────────────────────────────────────────────

class MarketplaceDownloadManager:
    """Manages plugin downloads from marketplace with verification."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize download manager.
        
        Args:
            cache_dir: Optional directory to cache downloaded plugins
        """
        self.cache_dir = cache_dir

    def calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            SHA256 checksum in format "sha256:hexdigest"
        """
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        
        return f"sha256:{sha256_hash.hexdigest()}"

    def calculate_checksum_from_bytes(self, data: bytes) -> str:
        """Calculate SHA256 checksum from bytes.
        
        Args:
            data: Byte data
            
        Returns:
            SHA256 checksum in format "sha256:hexdigest"
        """
        sha256_hash = hashlib.sha256(data)
        return f"sha256:{sha256_hash.hexdigest()}"

    def verify_checksum(self, file_path: Path, expected_checksum: str) -> bool:
        """Verify a file's checksum.
        
        Args:
            file_path: Path to file
            expected_checksum: Expected checksum (format: "sha256:hexdigest")
            
        Returns:
            True if checksum matches
            
        Raises:
            ChecksumMismatchError: If checksum does not match
        """
        actual = self.calculate_checksum(file_path)
        
        if actual != expected_checksum:
            raise ChecksumMismatchError(
                f"Checksum mismatch: expected {expected_checksum}, got {actual}"
            )
        
        return True

    def extract_zip(self, zip_path: Path, extract_to: Path) -> None:
        """Extract a ZIP file.
        
        Args:
            zip_path: Path to ZIP file
            extract_to: Directory to extract to
            
        Raises:
            zipfile.BadZipFile: If ZIP is corrupted
        """
        if not extract_to.exists():
            extract_to.mkdir(parents=True, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_to)
        except zipfile.BadZipFile as e:
            raise zipfile.BadZipFile(f"Failed to extract {zip_path}: {e}")

    async def download_plugin(
        self,
        url: str,
        expected_checksum: str,
        output_path: Path,
        timeout: int = 30
    ) -> Path:
        """Download plugin from marketplace URL.
        
        Args:
            url: Download URL
            expected_checksum: Expected SHA256 checksum
            output_path: Where to save downloaded file
            timeout: Download timeout in seconds
            
        Returns:
            Path to downloaded file
            
        Raises:
            DownloadError: If download fails
            ChecksumMismatchError: If checksum doesn't match
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise DownloadError(
                            f"Download failed: HTTP {resp.status}"
                        )
                    
                    # Download to file
                    data = await resp.read()
                    output_path.write_bytes(data)
            
            # Verify checksum
            self.verify_checksum(output_path, expected_checksum)
            
            return output_path
        
        except aiohttp.ClientError as e:
            raise DownloadError(f"Network error: {e}")
        except Exception as e:
            raise DownloadError(f"Download failed: {e}")

    async def install_plugin(
        self,
        url: str,
        expected_checksum: str,
        install_dir: Path,
        plugin_id: str
    ) -> Path:
        """Full workflow: Download → Verify → Extract.
        
        Args:
            url: Marketplace URL
            expected_checksum: Expected checksum
            install_dir: Where to install plugin
            plugin_id: Plugin ID (for naming)
            
        Returns:
            Path to extracted plugin directory
        """
        # Download to temp location
        download_dir = install_dir / ".tmp"
        zip_file = download_dir / f"{plugin_id}.zip"
        
        # Download and verify
        await self.download_plugin(url, expected_checksum, zip_file)
        
        # Extract
        extract_dir = install_dir / plugin_id
        self.extract_zip(zip_file, extract_dir)
        
        # Cleanup temp
        import shutil
        shutil.rmtree(download_dir, ignore_errors=True)
        
        return extract_dir
