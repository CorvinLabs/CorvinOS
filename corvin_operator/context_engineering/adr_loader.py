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
mein meine meinen meiner unser unsere heute morgen gestern hoch gibt geht gehen
setze setzen ohne nach vor neu neue neuen neuer immer schon sehr kann soll
funktioniert funktionieren zwischen jetzt dann hier dort welche welcher
eines einen einem einer eine ihr ihre ihren ihrem sein seine seinen ob sag sage
alle allen nach pro bzw usw etwa also
adr adrs phase status proposed accepted implemented
write writes wrote written explain explains help helps learn create creates give gives
tell show suggest recommend draft find know think like good best thing things way lot
one two three four five day days week weeks minute minutes hour hours work works
""".split())
#: The last three lines above (review R4-3): the verbs and fillers of an
#: everyday REQUEST ("write me…", "help me learn…", "a three day…") carry no
#: topic, but each is rare enough in ADR prose to score — they made 8 of 40
#: everyday non-CorvinOS requests return ADRs. Measured on that tuning set
#: and on a separate held-out set in tests/adr_retrieval_eval.py.

#: German → English for the core vocabulary of this corpus (ADRs are English).
#: Applied before stemming; deliberately small — a translation table is not a
#: goal, only the words operators actually ask about in German.
_DE_EN = {
    "kontext": "context", "sitzung": "session", "sitzungen": "sessions",
    "sitzungsinhalte": "session", "verlauf": "history", "gedächtnis": "memory",
    "erinnerung": "memory", "vergessen": "forget",
    "kompaktierung": "compaction", "löschen": "erasure", "löschung": "erasure",
    "kette": "chain", "prüfung": "verification", "verifizieren": "verify",
    "zähler": "counter", "nutzung": "usage", "kosten": "cost",
    "modell": "model", "modelle": "models", "sprache": "speech",
    "spracherkennung": "transcription", "einwilligung": "consent",
    "mandant": "tenant", "mandanten": "tenant", "berechtigung": "permission",
    "fehler": "error", "absturz": "crash", "neustart": "restart",
    "lösche": "erasure", "lösch": "erasure", "gelöscht": "erasure",
    "daten": "data", "nutzer": "user", "nutzers": "user", "benutzer": "user",
    "schlüssel": "key", "verschlüsselung": "encryption", "verschlüsselt": "encryption",
    "rotiere": "rotation", "rotieren": "rotation",
    "prüfe": "verify", "prüfen": "verify", "intakt": "intact",
    "protokoll": "log", "einwilligungen": "consent",
}
#: A match below this share of the best match's score is dropped.
RELATIVE_CUTOFF = 0.5
#: Minimum normalised relevance for an ADR to be returned.
MIN_RELEVANCE = 0.22
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
    """Tiny suffix stripper that maps a word family onto ONE stem
    (route/routes/routing → rout, classify/classifier/classification → classif,
    erase/erasure → eras, cache/caching/cached → cach) without a stemming
    dependency. Longest suffix first; a trailing ``i``/``e`` left by a suffix
    (classifi-er, eras-ure) is folded the same way the bare form is."""
    if word.endswith("sses"):
        word = word[:-2]          # classes/processes/accesses → class/process/access
    for suf in ("ications", "ication", "ations", "ation", "ences", "ence", "ings",
                "ions", "ing", "ion", "ures", "ure", "ies", "ers", "ied", "ed", "es",
                "er", "y", "s", "e"):
        if suf == "s" and word.endswith("ss"):
            break                 # class, process, loss: not a plural
        if len(word) - len(suf) >= 3 and word.endswith(suf):
            word = word[: -len(suf)]
            break
    if len(word) > 4 and word[-1] in "ie":
        word = word[:-1]
    return word


def tokenize(text: str) -> List[str]:
    """Whole-word, stop-word-free, stemmed tokens (order-preserving, deduped)."""
    out: Dict[str, None] = {}
    prev = ""
    for w in _TOKEN_RE.findall((text or "").lower()):
        w = _DE_EN.get(w, w)
        if re.fullmatch(r"l\d{1,2}", w):        # layer ids: L4, L35
            out[w] = None
        elif w.isdigit() and prev == "layer" and len(w) <= 2:
            out.pop(_stem("layer"), None)      # "Layer 36" ≡ "L36": the generic word goes
            out["l" + str(int(w))] = None
        elif w.isdigit() and (len(w) >= 3 or prev in ("art", "artikel")):
            # "Art. 17" vs "Art. 32", "Art. 5", "403": numbers carry topic. A
            # bare two-digit number ("10 minutes") is a quantity, not a topic.
            out[(prev + w) if prev in ("art", "artikel") else w] = None
        elif not (w in _STOPWORDS or len(w) < 3 or w.isdigit()):
            out[_stem(w)] = None
        prev = w
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
                    # ~100 ids are carried by two files. EVERY document stays
                    # retrievable (dropping one made ~21 % of the corpus
                    # unreachable): the preferred file — accepted over proposed
                    # over the rest — holds the plain id as its key, a sibling is
                    # keyed "<id>~<file stem>". ``metadata.id`` is always the
                    # plain ADR id, which is what a reader is shown.
                    prev = self.adrs.get(metadata.id)
                    if prev is None:
                        self.adrs[metadata.id] = ADRNode(metadata=metadata)
                    elif _status_rank(metadata) > _status_rank(prev.metadata):
                        self.adrs[f"{metadata.id}~{Path(prev.metadata.file_path).stem}"] = prev
                        self.adrs[metadata.id] = ADRNode(metadata=metadata)
                    else:
                        self.adrs[f"{metadata.id}~{adr_file.stem}"] = ADRNode(metadata=metadata)
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
        _vals = sorted(self._idf.values())
        self._median_idf = _vals[len(_vals) // 2] if _vals else 1.0
        self._low_idf = _vals[len(_vals) // 4] if _vals else 1.0

    def _parse_adr(self, adr_file: Path) -> Optional[ADRMetadata]:
        """Parse ADR file: extract frontmatter and content preview.

        Args:
            adr_file: Path to ADR markdown file.

        Returns:
            ADRMetadata if valid, None otherwise.
        """
        content = adr_file.read_text(encoding="utf-8")

        # Frontmatter (YAML between --- markers). A file without it, or with
        # unparseable YAML, is still a decision: its id comes from the file
        # name and its status from the body's "Status:" line (eight numbered
        # ADRs — incl. ADR-0255, a live flag's decision — were unretrievable).
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        frontmatter: dict = {}
        if match:
            body = content[match.end():].strip()
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                frontmatter = {}
            if not isinstance(frontmatter, dict):
                frontmatter = {}
        else:
            body = content.strip()
        if "status" not in frontmatter:
            st = re.search(r"(?im)^\**status:?\**\s*:?\s*\**\s*([A-Za-z_]+)", body)
            if st:
                frontmatter["status"] = st.group(1)

        # The id is the file name's leading number ("0269-title.md" and
        # "ADR-0269-title.md" both occur), falling back to the frontmatter id.
        # Renumbering moved files to new numbers but left ~57 of them carrying
        # their OLD frontmatter id (ADR-0785-0407-… says ``id: ADR-0407``), so
        # the file name is the current one. A file with no 4-digit id at all
        # (``ADR-0XXX-…`` drafts) is not a decision and is skipped.
        stem = adr_file.stem
        fm_id = re.match(r"^(?:ADR-)?(\d{4})(?!\d)", str(frontmatter.get("id", "")))
        fn_id = re.match(r"^(?:ADR-)?(\d{4})(?!\d)", stem)
        num = fn_id or fm_id
        # ``DOC-…`` reports and placeholder drafts (``ADR-0XXX-…``, id 0000)
        # live in decisions/ but decide nothing.
        if not num or num.group(1) == "0000" or stem.startswith("DOC-"):
            return None
        adr_id = f"ADR-{num.group(1)}"

        # Title: frontmatter ``title:`` first, else the first H1 OUTSIDE code
        # fences (a "# Begin transaction" comment in a code block is not a
        # title), else the file stem; the "ADR-NNNN:" prefix is stripped in any
        # case form.
        title = str(frontmatter.get("title") or "").strip()
        if not title:
            in_fence = False
            for line in body.splitlines():
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if not in_fence and line.startswith("# "):
                    title = line[2:].strip()
                    break
        if not title:
            title = stem.replace("-", " ").title()
        title = re.sub(r"^ADR[- ]?\d{4}(?:[- ]\d{4})?\s*[:—–-]?\s*", "", title, flags=re.IGNORECASE)
        title = title or stem

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
        """Rank ADRs by idf-weighted whole-token overlap with ``terms`` (the
        output of :func:`tokenize`). See docs/CONTEXT_ENGINEERING_LAYER.md §2 for
        the measured rules; this docstring states them in brief.

        A term found in the title counts fully, one found only in the body
        preview counts half; the sum is normalised by the question's 6 heaviest
        terms, unknown terms weighted at the corpus's 25th-percentile idf → [0, 1].
        Returned only when the score clears ``min_relevance``, the matched terms
        carry ≥ min(``MIN_EVIDENCE``, 80 % of the question's known idf), and two
        terms match — one suffices for a one-word question, or for a ≤ 3-word
        all-known question whose rare (``SINGLE_TERM_MIN_IDF``) word is in the
        title. Results under ``RELATIVE_CUTOFF`` × best are cut; superseded /
        rejected ADRs never match; ties break on raw evidence, then id.
        """
        if not hasattr(self, "_idf"):
            self._build_index()
        # ``terms`` are tokens from :func:`tokenize` — NOT re-tokenised here:
        # stemming is not idempotent (eras → era), so a second pass silently
        # turned the most specific matches into misses.
        q = list(dict.fromkeys(t for t in terms if t))
        if not q:
            return []
        known = {t: self._idf[t] for t in q if t in self._idf}
        if not known:
            return []
        # A term the corpus never uses still belongs to the question: it counts
        # in the denominator (at the 25th-percentile idf — an unknown word is
        # evidence of a gap, not a near-unique term), so "summarize this PDF" is
        # not treated as a one-word query that "summarize" alone fully covers.
        unknown = len(q) - len(known)
        weights = sorted(list(known.values()) + [self._low_idf] * unknown, reverse=True)
        norm = sum(weights[:6])
        # The evidence floor scales down for a question whose own words are all
        # common ("audit chain": 3.8 idf in total) — a full match of such a
        # question is still a match.
        evidence_floor = min(MIN_EVIDENCE, 0.8 * sum(known.values()))
        out: List[Tuple[str, float, float]] = []
        for adr_id, body in self._body_tokens.items():
            if str(self.adrs[adr_id].metadata.status).lower() in _INACTIVE_STATUSES:
                continue
            title = self._title_tokens[adr_id]
            hits = [t for t in known if t in body]
            if not hits:
                continue
            # One matched word is topic evidence for a one-word question, or for
            # a short question (≤ 3 words, all known to the corpus) when that
            # word is rare and in the ADR's title ("Bedrock 403"). Measured: +2
            # on the held-out set; "summarize this PDF" → ADR-0596 (voice
            # summarization) is its recorded borderline case.
            if len(hits) < 2:
                # ...and the word must be in the TITLE: a preview mention is
                # not what the decision is about.
                short_ok = len(q) <= 3 and unknown == 0 and hits[0] in title
                if not (len(q) == 1 or short_ok) or known[hits[0]] < SINGLE_TERM_MIN_IDF:
                    continue
            raw = sum(known[t] * (1.0 if t in title else 0.5) for t in hits)
            if sum(known[t] for t in hits) < evidence_floor:
                continue
            score = min(1.0, raw / norm) if norm > 0 else 0.0
            if score >= min_relevance:
                out.append((adr_id, round(score, 4), raw))
        # Ties (several ADRs at the 1.0 cap) break on raw evidence, then id.
        out.sort(key=lambda x: (-x[1], -x[2], x[0]))
        # Relative cut: when a strong match exists, a match far below it is
        # noise ("Layer 36 erasure" → the L36 ADR at 1.0, then other layers
        # that merely mention erasure).
        if out:
            floor = RELATIVE_CUTOFF * out[0][1]
            # Strictly above: a document naming every term only in its body
            # scores exactly half of one naming them in its title.
            out = [x for x in out if x[1] > floor]
        return [(i, sc) for i, sc, _ in out]

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
        return [adr_id for adr_id, _ in self.score_query(tokenize(" ".join(keywords)))[:max_results]]


def _status_rank(meta: ADRMetadata) -> int:
    return _STATUS_RANK.get(str(meta.status).strip().lower(), 0)


# One parsed corpus per process. GraphStage builds a fresh GraphTraversal on
# every turn, and parsing ~1 100 ADR files costs ~0.5 s — that was paid on the
# request path every turn. The cache is invalidated when any file is added,
# removed or modified (directory listing + max mtime), so an ADR edited mid-run
# is picked up on the next turn.
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[Tuple[int, int, int], "ADRLoader"]] = {}


def _dir_signature(d: Path) -> Tuple[int, int, int]:
    """Count, newest mtime and a hash of the sorted names: a rename changes
    neither count nor any mtime, so the names must be part of it."""
    files = sorted(d.glob("*.md"))
    return (len(files), max((f.stat().st_mtime_ns for f in files), default=0),
            hash(tuple(f.name for f in files)))


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
