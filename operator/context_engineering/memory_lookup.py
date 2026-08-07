"""Memory Lookup module (Phase 5.5a).

Searches memory files by keywords and returns ranked matches.
"""

import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta
from functools import lru_cache

from .rich_task_brief import MemoryMatch, MemoryContext, RichTaskBrief

logger = logging.getLogger(__name__)


class MemoryLookup:
    """Search and rank memory files by relevance."""

    def __init__(self, memory_dir: Optional[Path] = None, cache_ttl_minutes: int = 30):
        """Initialize MemoryLookup.

        Args:
            memory_dir: Path to memory files directory.
                        Default: ~/.claude/projects/CorvinOS/memory/
            cache_ttl_minutes: Cache time-to-live in minutes.
        """
        if memory_dir is None:
            memory_dir = Path.home() / ".claude" / "projects" / "CorvinOS" / "memory"

        self.memory_dir = Path(memory_dir)
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._search_cache = {}  # {query_hash: (results, timestamp)}

        logger.info(f"MemoryLookup initialized with dir: {self.memory_dir}")

    def search(
        self, keywords: List[str], max_results: int = 5
    ) -> List[MemoryMatch]:
        """Search memory files by keywords.

        Args:
            keywords: List of search keywords.
            max_results: Maximum results to return.

        Returns:
            List of MemoryMatch, ranked by relevance (highest first).
        """
        if not keywords:
            logger.warning("search() called with empty keywords")
            return []

        # Check cache
        cache_key = hash(tuple(sorted(keywords)))
        if cache_key in self._search_cache:
            cached_results, timestamp = self._search_cache[cache_key]
            if datetime.now() - timestamp < self.cache_ttl:
                logger.debug(f"Cache hit for keywords: {keywords}")
                return cached_results[:max_results]

        logger.debug(f"Searching memory for keywords: {keywords}")

        matches = []

        # Scan memory directory
        if not self.memory_dir.exists():
            logger.warning(f"Memory directory not found: {self.memory_dir}")
            return []

        for md_file in self.memory_dir.glob("*.md"):
            try:
                match = self._score_file(md_file, keywords)
                if match and match.relevance_score >= 0.3:
                    matches.append(match)
            except Exception as e:
                logger.warning(f"Error processing {md_file}: {e}")

        # Rank by relevance
        ranked = sorted(matches, key=lambda m: m.relevance_score, reverse=True)

        # Cache results
        self._search_cache[cache_key] = (ranked, datetime.now())

        logger.info(
            f"Found {len(ranked)} memory matches for {len(keywords)} keywords"
        )

        return ranked[:max_results]

    def _score_file(self, filepath: Path, keywords: List[str]) -> Optional[MemoryMatch]:
        """Score a single memory file against keywords.

        Args:
            filepath: Path to memory file.
            keywords: Search keywords.

        Returns:
            MemoryMatch if score >= 0.3, else None.
        """
        try:
            content = filepath.read_text()
            stat = filepath.stat()

            # Simple TF scoring
            title = self._extract_title(content)
            score = self._calculate_relevance(content, title, keywords)

            # Penalize old files
            age_days = (datetime.now() - datetime.fromtimestamp(stat.st_mtime)).days
            if age_days > 30:
                score *= 0.7  # 30% penalty for files > 30 days old

            # Clamp to [0.0, 1.0]
            score = max(0.0, min(1.0, score))

            if score >= 0.3:
                preview = content[:200].replace("\n", " ")
                return MemoryMatch(
                    filename=filepath.name,
                    title=title,
                    relevance_score=score,
                    source_file=str(filepath),
                    timestamp=datetime.fromtimestamp(stat.st_mtime),
                    content_preview=preview,
                )

            return None

        except Exception as e:
            logger.debug(f"Error scoring {filepath}: {e}")
            return None

    def _extract_title(self, content: str) -> str:
        """Extract title from markdown frontmatter or first heading.

        Args:
            content: File content.

        Returns:
            Title string, or "Untitled" if not found.
        """
        lines = content.split("\n")

        # Look for name: field in YAML frontmatter
        for line in lines[1:20]:  # Scan first 20 lines
            if line.startswith("name:"):
                return line.replace("name:", "").strip()
            if line.startswith("# "):
                return line.replace("# ", "").strip()

        return "Untitled"

    def _calculate_relevance(
        self, content: str, title: str, keywords: List[str]
    ) -> float:
        """Calculate relevance score [0.0, 1.0].

        Simple TF-IDF-like scoring:
        - Title keyword match: 2x weight
        - Body keyword match: 1x weight

        Args:
            content: File content.
            title: File title.
            keywords: Search keywords.

        Returns:
            Relevance score [0.0, 1.0].
        """
        content_lower = content.lower()
        title_lower = title.lower()

        matches = 0.0
        total_weight = 0.0

        for kw in keywords:
            kw_lower = kw.lower()
            total_weight += 1.0  # Base weight per keyword

            # Title match: 2x weight
            if kw_lower in title_lower:
                matches += 2.0
            # Body match: 1x weight
            elif kw_lower in content_lower:
                matches += 1.0

        if total_weight == 0:
            return 0.0

        # Normalize to [0.0, 1.0]
        return min(1.0, matches / total_weight)

    def rank(self, matches: List[MemoryMatch]) -> List[MemoryMatch]:
        """Re-rank matches by recency + relevance.

        Args:
            matches: List of MemoryMatch.

        Returns:
            Re-ranked matches.
        """
        # Sort by relevance score (already sorted by search(), but included for completeness)
        return sorted(matches, key=lambda m: m.relevance_score, reverse=True)

    def enrich_task(self, enriched_task: object) -> RichTaskBrief:
        """Transform EnrichedTask into RichTaskBrief with memory context.

        Args:
            enriched_task: EnrichedTask from Phase 4.

        Returns:
            RichTaskBrief with memory context populated.
        """
        # Extract keywords from task
        keywords = self._extract_keywords(enriched_task)

        # Search memory
        import time

        start = time.perf_counter()
        matches = self.search(keywords, max_results=5)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Calculate average confidence
        avg_confidence = (
            sum(m.relevance_score for m in matches) / len(matches)
            if matches
            else 0.0
        )

        # Create RichTaskBrief
        brief = RichTaskBrief(
            raw_input=getattr(enriched_task, "normalized", None).summary
            if hasattr(enriched_task, "normalized")
            else "unknown",
            enriched_task=enriched_task,
            memory_context=MemoryContext(
                matches=matches,
                search_queries=keywords,
                confidence=avg_confidence,
                cache_hit=False,
                search_duration_ms=elapsed_ms,
            ),
            timestamp=datetime.now(),
            version="0.1",
        )

        logger.info(
            f"Enriched task with memory: {len(matches)} matches, "
            f"confidence={avg_confidence:.2f}"
        )

        return brief

    def _extract_keywords(self, enriched_task: object) -> List[str]:
        """Extract keywords from EnrichedTask.

        Args:
            enriched_task: EnrichedTask object.

        Returns:
            List of keywords.
        """
        keywords = []

        # Try to extract from various task attributes
        if hasattr(enriched_task, "normalized"):
            normalized = enriched_task.normalized
            if hasattr(normalized, "summary"):
                # Simple split on spaces, filter stopwords
                words = normalized.summary.lower().split()
                keywords.extend([w for w in words if len(w) > 3])

        if hasattr(enriched_task, "key_terms"):
            keywords.extend(enriched_task.key_terms)

        # Deduplicate and limit
        return list(set(keywords))[:10]
