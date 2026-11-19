"""Memory Lookup module (Phase 5.5a).

Searches memory files by keywords and returns ranked matches with confidence scores.
Implements caching, age decay, and TF-IDF-like relevance scoring.
"""

import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from datetime import datetime, timedelta

from .rich_task_brief import MemoryMatch, MemoryContext, RichTaskBrief

logger = logging.getLogger(__name__)


def _default_memory_dir() -> Path:
    """Locate this project's memory directory.

    Claude Code stores per-project memory under
    ``~/.claude/projects/<escaped-repo-path>/memory``, where <escaped-repo-path>
    is the repo root's absolute path with every ``/`` and ``.`` replaced by ``-``.
    The old hard-coded ``projects/CorvinOS/memory`` omitted that escaped prefix,
    so it never resolved and every turn's memory stage found nothing — even
    though the real directory holds ~180 memories. Derive the path from THIS
    file's location so it is correct on any machine / checkout, not just one.
    """
    repo_root = Path(__file__).resolve().parents[2]  # …/operator/context_engineering → repo
    escaped = str(repo_root).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / escaped / "memory"


def tenant_memory_dir(tenant_id: str) -> Path:
    """``<tenant_home>/memory`` — the memory directory a tenant owns.

    The tenant is always explicit (``forge.paths.tenant_home`` validates it and never
    falls back to the process environment)."""
    from corvin_operator.forge.forge.paths import tenant_home  # noqa: PLC0415
    return Path(tenant_home(tenant_id)) / "memory"


def _boot_tenant() -> str:
    import os  # noqa: PLC0415
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


def _is_owner_session(tenant_id: str, session: object) -> bool:
    """True when ``session`` is provably the operator's own turn. Unknown is False.

    * ``session.host_memory`` — an explicit caller statement wins (True/False).
    * a bridge session (``adapter._cel_session``: ``sid = "<channel>:<chat>[#n]"`` plus the
      turn's ``sender``) is the operator's when the sender is on the channel's whitelist
      (``roles.is_intrinsic_owner``). A group observer, a granted user and a chat with no
      sender are not.
    * a console session (it carries the authenticated record's ``tenant_id`` and no
      ``sender``) is the operator's when that tenant is the one this turn runs for: the
      console's only login is the local one.
    * no session (ACS manager brief, library callers): nobody is named.
    """
    if session is None:
        return False
    explicit = getattr(session, "host_memory", None)
    if explicit is not None:
        return explicit is True
    if hasattr(session, "sender"):
        sender = str(getattr(session, "sender", "") or "")
        sid = str(getattr(session, "sid", "") or "")
        channel = sid.split(":", 1)[0] if ":" in sid else ""
        if not sender or not channel:
            return False
        try:
            try:
                import roles as _roles  # type: ignore  # noqa: PLC0415
            except ImportError:
                from corvin_operator.bridges.shared import roles as _roles  # noqa: PLC0415
            return bool(_roles.is_intrinsic_owner(channel, sender))
        except Exception:  # noqa: BLE001 — an unanswerable owner question is "no"
            return False
    sess_tenant = getattr(session, "tenant_id", None)
    return isinstance(sess_tenant, str) and bool(sess_tenant) and sess_tenant == tenant_id


def _host_memory_passes_l34(tenant_id: str, session: object) -> bool:
    """The host memory is the operator's working notes: class INTERNAL at least. It is
    injected only when the tenant's L34 policy lets that class reach the engine of the
    turn (no policy = the documented opt-in default, allowed). Any error is a no."""
    try:
        try:
            import spawn_gates as _sg  # type: ignore  # noqa: PLC0415
        except ImportError:
            from corvin_operator.bridges.shared import spawn_gates as _sg  # noqa: PLC0415
        sid = str(getattr(session, "sid", "") or "")
        channel, _, chat = sid.partition(":") if hasattr(session, "sender") else ("web", "", sid)
        engine = str(getattr(session, "engine_id", "") or "claude_code")
        return _sg.check_l34(engine, tenant_id, classification="INTERNAL",
                             channel=channel, chat_key=chat.split("#", 1)[0]) is None
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory source: L34 check for the host memory failed (%s) — withheld",
                       type(exc).__name__)
        return False


def resolve_memory_source(tenant_id: str, session: object = None) -> "Tuple[Path, str]":
    """Which memory directory the CEL memory stage may read for this turn: ``(dir, source)``.

    The HOST source (``~/.claude/projects/<repo>/memory``) is the Claude Code auto-memory of
    the operator's own account on this machine. It used to be read for every tenant and
    every bridge chat, so a stranger's Discord message or a second tenant's console chat
    got the operator's notes in its brief. It is now read only when ALL of these hold:

    * the turn runs for the BOOT tenant (the process tenant, ``CORVIN_TENANT_ID`` or
      ``_default``) — never for another tenant of a multi-tenant host;
    * the session is provably the operator's own (:func:`_is_owner_session`);
    * class INTERNAL passes the tenant's L34 policy for the turn's engine.

    Every other turn reads the tenant's own ``<tenant_home>/memory`` (absent on most
    installs: no matches). ``source`` is ``"host"`` or ``"tenant"``. Never raises.
    """
    try:
        tenant = str(tenant_id or "")
        if (tenant and tenant == _boot_tenant() and _is_owner_session(tenant, session)
                and _host_memory_passes_l34(tenant, session)):
            return _default_memory_dir(), "host"
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory source: owner check failed (%s) — tenant memory only",
                       type(exc).__name__)
    try:
        return tenant_memory_dir(tenant_id), "tenant"
    except Exception as exc:  # noqa: BLE001 — an invalid tenant has no memory at all
        logger.warning("memory source: no tenant memory directory (%s)", type(exc).__name__)
        return Path("/nonexistent/corvin-no-memory"), "tenant"


class MemoryLookup:
    """Search and rank memory files by relevance.

    Features:
    - TF-IDF-like relevance scoring (title: 2x, body: 1x)
    - Age decay (files > 30 days old get 0.7x penalty)
    - LRU caching (30-min TTL by default)
    - Deduplication (same file matched multiple times → highest score)
    """

    def __init__(self, memory_dir: Optional[Path] = None, cache_ttl_minutes: int = 30):
        """Initialize MemoryLookup.

        Args:
            memory_dir: Path to memory files directory.
                        Default: the host auto-memory (``_default_memory_dir``). The
                        CEL memory stage never relies on that default — it passes the
                        directory ``resolve_memory_source`` cleared for the turn.
            cache_ttl_minutes: Cache time-to-live in minutes (default: 30).
        """
        if memory_dir is None:
            memory_dir = _default_memory_dir()

        self.memory_dir = Path(memory_dir)
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._search_cache: Dict[int, Tuple[List[MemoryMatch], datetime]] = {}

        logger.info(f"MemoryLookup initialized: {self.memory_dir} (TTL: {cache_ttl_minutes}min)")

    def search(
        self, keywords: List[str], max_results: int = 5
    ) -> List[MemoryMatch]:
        """Search memory files by keywords.

        Pipeline:
        1. Validate input (empty keywords → return [])
        2. Check cache (hit → return cached results)
        3. Scan memory directory (*.md files)
        4. Score each file (TF + age decay)
        5. Filter (score >= 0.3)
        6. Rank by relevance
        7. Cache results (LRU)
        8. Return top N results

        Args:
            keywords: List of search keywords (case-insensitive).
            max_results: Maximum results to return (default: 5).

        Returns:
            List of MemoryMatch, ranked by relevance score (highest first).
        """
        if not keywords:
            logger.warning("search() called with empty keywords, returning []")
            return []

        # Check cache
        cache_key = hash(tuple(sorted(keywords)))
        if cache_key in self._search_cache:
            cached_results, timestamp = self._search_cache[cache_key]
            age = datetime.now() - timestamp
            if age < self.cache_ttl:
                logger.debug(
                    f"Cache hit: keywords={keywords}, age={age.total_seconds():.0f}s, "
                    f"results={len(cached_results)}"
                )
                return cached_results[:max_results]
            else:
                # Expired cache entry, remove it
                del self._search_cache[cache_key]

        logger.debug(f"Cache miss: searching memory for {len(keywords)} keywords")

        matches = []

        # Scan memory directory
        if not self.memory_dir.exists():
            logger.debug(f"Memory directory not found: {self.memory_dir}")
            return []

        md_files = list(self.memory_dir.glob("*.md"))
        logger.debug(f"Scanning {len(md_files)} memory files")

        for md_file in md_files:
            try:
                match = self._score_file(md_file, keywords)
                if match and match.relevance_score >= 0.3:
                    matches.append(match)
            except Exception as e:
                logger.warning(f"Error processing {md_file.name}: {e}")

        # Rank by relevance (highest first)
        ranked = sorted(matches, key=lambda m: m.relevance_score, reverse=True)

        # Cache results
        self._search_cache[cache_key] = (ranked, datetime.now())

        # Log summary
        logger.info(
            f"Search complete: {len(keywords)} keywords → {len(ranked)} matches "
            f"(threshold 0.3), returning top {min(len(ranked), max_results)}"
        )

        return ranked[:max_results]

    def recall(
        self, keywords: List[str], max_results: int = 5,
        confidence_gate_enabled: bool = False,
        confidence_threshold: float = 0.5
    ) -> List[MemoryMatch]:
        """Search memory with optional confidence gating (ADR-0387).

        When confidence_gate_enabled is True, filters matches where relevance_score
        is below the confidence_threshold before returning.

        Args:
            keywords: List of search keywords (case-insensitive).
            max_results: Maximum results to return (default: 5).
            confidence_gate_enabled: Whether to apply confidence gating (default: False).
            confidence_threshold: Minimum confidence score required (default: 0.5).

        Returns:
            List of MemoryMatch, filtered by confidence if enabled.
        """
        # Search normally
        matches = self.search(keywords, max_results)

        # Apply confidence gate if enabled
        if confidence_gate_enabled:
            matches = [
                m for m in matches
                if m.relevance_score >= confidence_threshold
            ]
            logger.debug(
                f"Confidence gating applied: {len(matches)} matches "
                f"passed threshold {confidence_threshold}"
            )

        return matches

    def _score_file(self, filepath: Path, keywords: List[str]) -> Optional[MemoryMatch]:
        """Score a single memory file against keywords.

        Scoring pipeline:
        1. Read file content
        2. Extract title (from frontmatter or first heading)
        3. Calculate TF relevance (title: 2x, body: 1x)
        4. Apply age decay (files > 30 days → 0.7x)
        5. Clamp to [0.0, 1.0]
        6. Filter (score >= 0.3)

        Args:
            filepath: Path to memory file.
            keywords: Search keywords (case-insensitive).

        Returns:
            MemoryMatch if score >= 0.3, else None.
        """
        try:
            content = filepath.read_text(encoding="utf-8")
            stat = filepath.stat()

            # Extract title
            title = self._extract_title(content)

            # Calculate relevance
            score = self._calculate_relevance(content, title, keywords)

            # Apply age decay
            age_days = (datetime.now() - datetime.fromtimestamp(stat.st_mtime)).days
            if age_days > 30:
                # Linear decay: 30 days → 0.7, 60 days → 0.4, 90+ days → 0.1
                decay = max(0.1, 1.0 - (age_days - 30) / 300)
                score *= decay

            # Clamp to [0.0, 1.0]
            score = max(0.0, min(1.0, score))

            # Filter threshold
            if score < 0.3:
                return None

            # Extract content preview (ADR-0389: bounded to 50 chars)
            preview = content[:50].replace("\n", " ").strip()

            return MemoryMatch(
                filename=filepath.name,
                title=title,
                relevance_score=score,
                source_file=str(filepath),
                timestamp=datetime.fromtimestamp(stat.st_mtime),
                content_preview=preview,
            )

        except Exception as e:
            logger.debug(f"Error scoring {filepath.name}: {e}")
            return None

    def _extract_title(self, content: str) -> str:
        """Extract title from markdown frontmatter or first heading.

        Precedence:
        1. YAML frontmatter `name:` field
        2. First markdown heading `# ...`
        3. "Untitled" as fallback

        Args:
            content: File content (markdown).

        Returns:
            Title string.
        """
        lines = content.split("\n")

        # Scan first 20 lines for name: or # heading
        for line in lines[1:20]:
            if line.startswith("name:"):
                return line.replace("name:", "").strip()
            if line.startswith("# "):
                return line.replace("# ", "").strip()

        return "Untitled"

    def _calculate_relevance(
        self, content: str, title: str, keywords: List[str]
    ) -> float:
        """Calculate TF-IDF-like relevance score [0.0, 1.0].

        Scoring:
        - Title keyword match: +2.0 weight per keyword
        - Body keyword match: +1.0 weight per keyword
        - Normalized by total keyword weight

        Args:
            content: File content.
            title: File title.
            keywords: Search keywords (case-insensitive).

        Returns:
            Relevance score [0.0, 1.0].
        """
        content_lower = content.lower()
        title_lower = title.lower()

        matches = 0.0
        total_weight = len(keywords)  # Base weight per keyword

        if total_weight == 0:
            return 0.0

        for kw in keywords:
            kw_lower = kw.lower()

            # Title match: 2x weight
            if kw_lower in title_lower:
                matches += 2.0
            # Body match: 1x weight
            elif kw_lower in content_lower:
                matches += 1.0

        # Normalize: matches / total_weight, clamped to [0.0, 1.0]
        # Example: 3 keywords, 2 title matches → 4.0 / 3 = 1.33 → clamped to 1.0
        return min(1.0, matches / total_weight)

    def rank(self, matches: List[MemoryMatch]) -> List[MemoryMatch]:
        """Re-rank matches by relevance score (descending).

        Args:
            matches: List of MemoryMatch objects.

        Returns:
            Re-ranked matches (highest score first).
        """
        return sorted(matches, key=lambda m: m.relevance_score, reverse=True)

    def enrich_task(self, enriched_task: object) -> RichTaskBrief:
        """Transform EnrichedTask into RichTaskBrief with memory context.

        Pipeline:
        1. Extract keywords from task
        2. Search memory
        3. Calculate avg confidence
        4. Create RichTaskBrief

        Args:
            enriched_task: EnrichedTask from Phase 4 (Enrich).

        Returns:
            RichTaskBrief with memory context populated.
        """
        # Extract keywords
        keywords = self._extract_keywords(enriched_task)

        # Search memory + time it
        start = time.perf_counter()
        matches = self.search(keywords, max_results=5)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Calculate average confidence
        avg_confidence = (
            sum(m.relevance_score for m in matches) / len(matches)
            if matches
            else 0.0
        )

        # Get raw input
        raw_input = "unknown"
        if hasattr(enriched_task, "normalized") and hasattr(enriched_task.normalized, "summary"):
            raw_input = enriched_task.normalized.summary

        # Create RichTaskBrief
        brief = RichTaskBrief(
            raw_input=raw_input,
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
            f"Enriched task: {len(keywords)} keywords → {len(matches)} memory matches, "
            f"confidence={avg_confidence:.2f}, latency={elapsed_ms:.0f}ms"
        )

        return brief

    def _extract_keywords(self, enriched_task: object) -> List[str]:
        """Extract keywords from EnrichedTask.

        Extracts from:
        - normalized.summary (split on whitespace, filter short words)
        - key_terms attribute
        - Deduplicates, limits to 10 keywords

        Args:
            enriched_task: EnrichedTask object.

        Returns:
            List of keywords (lowercase, deduped, up to 10 items).
        """
        keywords = []

        # Try normalized.summary
        if hasattr(enriched_task, "normalized"):
            normalized = enriched_task.normalized
            if hasattr(normalized, "summary"):
                words = normalized.summary.lower().split()
                # Filter: words > 3 chars, not common stopwords
                stopwords = {"the", "this", "that", "with", "from", "and", "or", "is", "a", "be"}
                keywords.extend([w for w in words if len(w) > 3 and w not in stopwords])

        # Try key_terms
        if hasattr(enriched_task, "key_terms"):
            keywords.extend(enriched_task.key_terms)

        # Deduplicate and limit
        unique_keywords = list(dict.fromkeys(keywords))  # Preserve order, dedupe
        return unique_keywords[:10]
