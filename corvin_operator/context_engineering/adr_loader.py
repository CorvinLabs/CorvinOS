"""ADR Loader: Parse ADRs from Corvin-ADR repo with dependency graph traversal."""

import logging
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Iterable, Optional, Set, Tuple
import yaml

logger = logging.getLogger(__name__)

# --- Lexical relevance (session-drift analysis 2026-10-02, §L1) -------------
# The previous matcher counted a keyword as a hit when it was a SUBSTRING of the
# title or preview, weighted every word equally and accepted a single hit as a
# seed. A drift/memory question therefore surfaced Tree-of-Thoughts and Compute
# Fabric ADRs because they share "layer"/"session"/"across". Matching is now on
# whole (lightly stemmed) tokens, weighted by inverse document frequency over the
# ADR corpus, and an ADR must clear MIN_RELEVANCE to be returned at all.
_TOKEN_RE = re.compile(r"[a-zäöüß0-9]+")
_STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has have
how i if in into is it its may might must no not of on or our shall should so such
than that the their them then there these they this those to too was we were what
when where which while who why will with would you your about above after again
against all also any because before below between both each few further here more
most other over same some only own very just now via per use used using make made
across within without please
new add adds added fix fixes fixed get set run runs ran need needs want wants
der die das und oder mit für von zu zum zur ist sind ein eine einen einem einer
nicht auch auf aus bei bis dass den dem des durch noch nur über unter vom wie wir
ich du sie es was wenn alle alles diese dieser dieses doch hat haben wird werden
mir mich dir dich beim bitte kannst mach mache warum
adr adrs phase status proposed accepted implemented
""".split())
#: Minimum normalised relevance for an ADR to be returned.
MIN_RELEVANCE = 0.25
#: Minimum absolute evidence: the summed idf of the matched terms. Coverage is
#: normalised by the query's own idf mass, so a query made only of words every
#: ADR uses would otherwise "cover" itself fully and match everything.
MIN_EVIDENCE = 4.0
#: A single matched term only counts when it is this rare (idf, natural log):
#: ~ fewer than 1 in 60 ADRs mention it.
SINGLE_TERM_MIN_IDF = 4.1
_INACTIVE_STATUSES = frozenset({"superseded", "rejected", "deprecated", "withdrawn"})
_STATUS_RANK = {"accepted": 3, "implemented": 3, "proposed": 2}


def _stem(word: str) -> str:
    """Tiny suffix stripper — enough to join session/sessions, drift/drifting,
    persist/persistence/persisted without pulling a stemming dependency."""
    for suf in ("ations", "ation", "ences", "ence", "ings", "ing", "ies", "ers",
                "ed", "es", "er", "s"):
        if len(word) - len(suf) >= 4 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def tokenize(text: str) -> List[str]:
    """Whole-word, stop-word-free, stemmed tokens (order-preserving, deduped)."""
    out: Dict[str, None] = {}
    for w in _TOKEN_RE.findall((text or "").lower()):
        if len(w) < 3 or w in _STOPWORDS or w.isdigit():
            continue
        out[_stem(w)] = None
    return list(out)


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

    relevance: float = 0.0
    """Lexical relevance to the query that selected this ADR ([0, 1]); 0.0 when
    the metadata did not come from a scored search."""


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

        for adr_file in sorted(self.decisions_dir.glob("*.md")):
            try:
                metadata = self._parse_adr(adr_file)
                if metadata:
                    prev = self.adrs.get(metadata.id)
                    # ~100 ids are carried by two files. Keep the one a reader
                    # should be pointed at (accepted over proposed over the
                    # rest), not whichever glob happened to yield last.
                    if prev is None or _status_rank(metadata) > _status_rank(prev.metadata):
                        self.adrs[metadata.id] = ADRNode(metadata=metadata)
                    logger.debug(f"Loaded ADR: {metadata.id}")
            except Exception as e:
                logger.warning(f"Failed to parse {adr_file}: {e}")

        logger.info(f"Loaded {len(self.adrs)} ADRs")

        # Build graph after all ADRs loaded
        self._build_graph()
        self._build_index()

    def _build_index(self) -> None:
        """Token sets per ADR plus corpus idf, for :meth:`score_query`."""
        self._title_tokens: Dict[str, Set[str]] = {}
        self._body_tokens: Dict[str, Set[str]] = {}
        df: Dict[str, int] = {}
        for adr_id, node in self.adrs.items():
            m = node.metadata
            tt = set(tokenize(m.title))
            bt = set(tokenize(m.content_preview)) | tt
            self._title_tokens[adr_id] = tt
            self._body_tokens[adr_id] = bt
            for t in bt:
                df[t] = df.get(t, 0) + 1
        n = max(len(self.adrs), 1)
        self._idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self._max_idf = math.log((n + 1) / 0.5)

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

        # The id comes from the frontmatter (ADR-0264 makes it canonical) and
        # falls back to the file name. Both "0269-title.md" and
        # "ADR-0269-title.md" are in the corpus; the old ``re.match(r"(\d{4})")``
        # failed on the second shape and used the whole stem as the id.
        stem = adr_file.stem
        fm_id = re.match(r"^(?:ADR-)?(\d{4})(?!\d)", str(frontmatter.get("id", "")))
        fn_id = re.match(r"^(?:ADR-)?(\d{4})(?!\d)", stem)
        num = fm_id or fn_id
        adr_id = f"ADR-{num.group(1)}" if num else stem

        h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = h1.group(1).strip() if h1 else stem.replace("-", " ").title()
        title = re.sub(r"^ADR[- ]?\d{4}\s*[:—–-]\s*", "", title)

        return ADRMetadata(
            id=adr_id,
            title=title,
            status=str(frontmatter.get("status") or "proposed").strip(),
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

    def score_query(
        self, terms: Iterable[str], min_relevance: float = MIN_RELEVANCE,
    ) -> List[Tuple[str, float]]:
        """Rank ADRs by idf-weighted whole-token overlap with ``terms``.

        A term found in the title counts fully, one found only in the body
        preview counts half. The score is normalised by the query's own idf mass
        (capped at the 6 rarest terms, so a long prompt is not penalised for
        being long) and lies in [0, 1]. An ADR is returned only when it clears
        ``min_relevance`` AND matches either two distinct terms or one term rare
        enough (``SINGLE_TERM_MIN_IDF``) to be specific on its own, and the
        matched terms carry at least ``MIN_EVIDENCE`` idf in total. Superseded /
        rejected ADRs are never returned. Ties break on id, so the order is
        deterministic.
        """
        if not hasattr(self, "_idf"):
            self._build_index()
        q = [t for t in dict.fromkeys(tokenize(" ".join(terms)))]
        if not q:
            return []
        weights = {t: self._idf.get(t, self._max_idf) for t in q}
        # Terms the corpus never uses carry no evidence either way.
        known = {t: w for t, w in weights.items() if t in self._idf}
        if not known:
            return []
        norm = sum(sorted(known.values(), reverse=True)[:6])
        out: List[Tuple[str, float]] = []
        for adr_id, body in self._body_tokens.items():
            if str(self.adrs[adr_id].metadata.status).lower() in _INACTIVE_STATUSES:
                continue
            title = self._title_tokens[adr_id]
            hits = [t for t in known if t in body]
            if not hits:
                continue
            if len(hits) < 2 and known[hits[0]] < SINGLE_TERM_MIN_IDF:
                continue
            raw = sum(known[t] * (1.0 if t in title else 0.5) for t in hits)
            if sum(known[t] for t in hits) < MIN_EVIDENCE:
                continue
            score = min(1.0, raw / norm) if norm > 0 else 0.0
            if score >= min_relevance:
                out.append((adr_id, round(score, 4)))
        out.sort(key=lambda x: (-x[1], x[0]))
        return out

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
        return [adr_id for adr_id, _ in self.score_query(keywords)[:max_results]]


def _status_rank(meta: ADRMetadata) -> int:
    return _STATUS_RANK.get(str(meta.status).strip().lower(), 0)


# One parsed corpus per process. GraphStage builds a fresh GraphTraversal on
# every turn, and parsing ~1 100 ADR files costs ~0.5 s — that was paid on the
# request path every turn. The cache is invalidated when any file is added,
# removed or modified (directory listing + max mtime), so an ADR edited mid-run
# is picked up on the next turn.
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[Tuple[int, int], "ADRLoader"]] = {}


def _dir_signature(d: Path) -> Tuple[int, int]:
    files = list(d.glob("*.md"))
    return (len(files), max((f.stat().st_mtime_ns for f in files), default=0))


def get_loader(adr_repo_path: Optional[str] = None) -> ADRLoader:
    """Shared, freshness-checked :class:`ADRLoader`."""
    if adr_repo_path:
        d: Optional[Path] = Path(adr_repo_path)
    else:
        root = Path(__file__).parent.parent.parent
        d = next((root / sp for sp in ADRLoader.SEARCH_PATHS
                  if (root / sp).is_dir() and any((root / sp).glob("*.md"))), None)
    if d is None or not d.is_dir():
        return ADRLoader(adr_repo_path=adr_repo_path)
    key = str(d.resolve())
    sig = _dir_signature(d)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    loader = ADRLoader(adr_repo_path=str(d))
    with _CACHE_LOCK:
        _CACHE[key] = (sig, loader)
    return loader
