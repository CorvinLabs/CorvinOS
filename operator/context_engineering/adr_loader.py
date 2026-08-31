"""ADR Loader: Parse ADRs from Corvin-ADR repo with dependency graph traversal."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Set
import yaml

logger = logging.getLogger(__name__)


@dataclass
class ADRMetadata:
    """ADR frontmatter metadata."""

    id: str
    """ADR identifier (e.g., 'ADR-0269')."""

    title: str
    """ADR title from filename."""

    status: str
    """Status: proposed | accepted | superseded | frozen."""

    depends_on: List[str] = field(default_factory=list)
    """Prerequisites (ADR IDs this depends on)."""

    related: List[str] = field(default_factory=list)
    """Related ADRs (associative, non-blocking)."""

    supersedes: List[str] = field(default_factory=list)
    """ADR IDs this one replaces."""

    paths: List[str] = field(default_factory=list)
    """Code globs this ADR constrains."""

    docs: List[str] = field(default_factory=list)
    """Documentation globs this ADR governs."""

    file_path: str = ""
    """Absolute path to ADR file."""

    content_preview: str = ""
    """First 500 chars of ADR body."""


@dataclass
class ADRNode:
    """Node in ADR dependency graph."""

    metadata: ADRMetadata
    neighbors: Set[str] = field(default_factory=set)
    """All connected ADR IDs (depends_on + related + supersedes)."""


class ADRLoader:
    """Load ADRs from flexible paths and build dependency graph.

    Searches for ADRs in this order:
    1. Separate Corvin-ADR/ repo (sibling to project)
    2. docs/decisions/ (in project repo)
    3. docs/adr/ (alternative naming)
    4. .docs/decisions/ (dotfile variant)

    Falls back gracefully if no ADRs found.
    """

    # Search paths (relative to project root) tried in order
    SEARCH_PATHS = [
        "../Corvin-ADR/decisions",  # Separate repo (like CorvinOS)
        "docs/decisions",            # Same repo, docs/decisions (common pattern)
        "docs/adr",                  # Same repo, docs/adr (alternative)
        ".docs/decisions",           # Dotfile variant
        "docs/architecture/decisions",  # Nested variant
    ]

    def __init__(self, adr_repo_path: Optional[str] = None, project_root: Optional[str] = None):
        """Initialize ADR loader with flexible path discovery.

        Args:
            adr_repo_path: Explicit path to ADR directory (overrides search).
            project_root: Project root to search from (default: detect from file location).
        """
        self.adrs: Dict[str, ADRNode] = {}
        self.decisions_dir: Optional[Path] = None
        self.adr_source: Optional[str] = None  # Where ADRs were loaded from

        # Detect project root if not provided
        if project_root is None:
            # Start from this file's location and find project root
            current = Path(__file__).parent.parent.parent  # operator/ → /
            project_root = str(current)

        self.project_root = Path(project_root)

        # Try to find ADRs
        if adr_repo_path:
            # Explicit path provided
            self.decisions_dir = Path(adr_repo_path)
            if self.decisions_dir.exists():
                self.adr_source = str(self.decisions_dir)
                self._load_adrs()
            else:
                logger.warning(f"Explicit ADR path not found: {adr_repo_path}")
        else:
            # Search in order
            self._search_adr_paths()

        if not self.decisions_dir:
            logger.warning("No ADR directory found (will use Phase 2 fallback)")
        else:
            logger.info(f"ADRLoader initialized: {self.adr_source}")

    def _search_adr_paths(self):
        """Search for ADR directory in standard locations."""
        for search_path in self.SEARCH_PATHS:
            candidate = self.project_root / search_path
            if candidate.exists() and candidate.is_dir():
                # Check if it has .md files (confirm it's an ADR directory)
                md_files = list(candidate.glob("*.md"))
                if md_files:
                    self.decisions_dir = candidate
                    self.adr_source = str(candidate)
                    logger.info(f"Found ADRs at: {self.adr_source} ({len(md_files)} files)")
                    self._load_adrs()
                    return

        logger.warning(f"No ADRs found in standard paths from {self.project_root}")

    def _load_adrs(self):
        """Load all ADRs from decisions directory."""
        if not self.decisions_dir.exists():
            logger.warning(f"Decisions directory not found: {self.decisions_dir}")
            return

        for adr_file in self.decisions_dir.glob("*.md"):
            try:
                metadata = self._parse_adr(adr_file)
                if metadata:
                    node = ADRNode(metadata=metadata)
                    self.adrs[metadata.id] = node
                    logger.debug(f"Loaded ADR: {metadata.id}")
            except Exception as e:
                logger.warning(f"Failed to parse {adr_file}: {e}")

        logger.info(f"Loaded {len(self.adrs)} ADRs")

        # Build graph after all ADRs loaded
        self._build_graph()

    def _parse_adr(self, adr_file: Path) -> Optional[ADRMetadata]:
        """Parse ADR file: extract frontmatter and content preview.

        Args:
            adr_file: Path to ADR markdown file.

        Returns:
            ADRMetadata if valid, None otherwise.
        """
        content = adr_file.read_text(encoding="utf-8")

        # Extract frontmatter (YAML between --- markers)
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        frontmatter_str = match.group(1)
        body_start = match.end()
        body = content[body_start:].strip()

        try:
            frontmatter = yaml.safe_load(frontmatter_str) or {}
        except yaml.YAMLError:
            return None

        # Extract ADR ID from filename (e.g., "0269-title.md" → "ADR-0269")
        stem = adr_file.stem
        match = re.match(r"(\d{4})", stem)
        adr_id = f"ADR-{match.group(1)}" if match else stem

        return ADRMetadata(
            id=adr_id,
            title=stem.replace("-", " ").title(),
            status=frontmatter.get("status", "proposed"),
            depends_on=frontmatter.get("depends_on", []),
            related=frontmatter.get("related", []),
            supersedes=frontmatter.get("supersedes", []),
            paths=frontmatter.get("paths", []),
            docs=frontmatter.get("docs", []),
            file_path=str(adr_file),
            content_preview=body[:500] if body else "",
        )

    def _build_graph(self):
        """Build adjacency graph: each ADR connects to its depends_on/related/supersedes."""
        for adr_id, node in self.adrs.items():
            # Add neighbors
            node.neighbors.update(node.metadata.depends_on)
            node.neighbors.update(node.metadata.related)
            node.neighbors.update(node.metadata.supersedes)

            # Only keep neighbors that exist in loaded ADRs
            node.neighbors = {n for n in node.neighbors if n in self.adrs}

    def get_adr(self, adr_id: str) -> Optional[ADRMetadata]:
        """Get ADR by ID."""
        if adr_id in self.adrs:
            return self.adrs[adr_id].metadata
        return None

    def find_related_adr_ids(
        self, seed_adr_id: str, depth: int = 2, max_results: int = 5
    ) -> List[str]:
        """Find related ADRs via BFS from seed ADR.

        Args:
            seed_adr_id: Starting ADR ID (e.g., 'ADR-0269').
            depth: Max traversal depth.
            max_results: Max related ADRs to return.

        Returns:
            List of related ADR IDs ranked by distance.
        """
        if seed_adr_id not in self.adrs:
            return []

        visited = {seed_adr_id}
        queue = [(seed_adr_id, 0)]
        results = []

        while queue:
            adr_id, dist = queue.pop(0)

            if dist > 0:  # Don't include seed itself
                results.append((adr_id, dist))

            if dist < depth:
                node = self.adrs[adr_id]
                for neighbor in node.neighbors:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, dist + 1))

        # Sort by distance (closest first), return top N
        results.sort(key=lambda x: x[1])
        return [adr_id for adr_id, _ in results[:max_results]]

    def search_by_keywords(self, keywords: List[str], max_results: int = 5) -> List[str]:
        """Find ADRs by keyword matching against title + content preview.

        Args:
            keywords: Search keywords.
            max_results: Max results to return.

        Returns:
            List of ADR IDs ranked by keyword match score.
        """
        if not keywords:
            return []

        # Score each ADR
        scores = {}
        keywords_lower = [kw.lower() for kw in keywords]

        for adr_id, node in self.adrs.items():
            score = 0
            title_lower = node.metadata.title.lower()
            content_lower = node.metadata.content_preview.lower()

            # Title matches are weighted higher
            for kw in keywords_lower:
                if kw in title_lower:
                    score += 2
                if kw in content_lower:
                    score += 1

            if score > 0:
                scores[adr_id] = score

        # Sort by score, return top N
        sorted_adr_ids = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [adr_id for adr_id, _ in sorted_adr_ids[:max_results]]
