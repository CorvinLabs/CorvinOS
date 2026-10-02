"""ADR Classifier: Match tasks to relevant ADRs."""

import dataclasses
import logging
import re
from typing import Dict, List, Optional
from .adr_loader import ADRLoader, ADRMetadata, MIN_RELEVANCE, get_loader, tokenize

logger = logging.getLogger(__name__)

#: An ADR the operator names by id ("continue the ADR-0952 work"). Lexically its
#: number is one body token among many — 86 of 400 "Implement ADR-NNNN" queries
#: found the named ADR (review R4-4) — so a named id is a direct lookup.
#: Same shape as ``pipeline._OBSERVER_BLOCK_RE`` (adapter._format_observer_block).
_OBSERVER_BLOCK_RE = re.compile(
    r"---BEGIN-OBSERVER-([0-9a-f]+)---\n.*?\n---END-OBSERVER-\1---\n*", re.DOTALL)
_ADR_REF_RE = re.compile(r"\bADR[-_ ]?(\d{3,4})\b", re.IGNORECASE)


class ADRClassifier:
    """Classify tasks and find relevant ADRs."""

    def __init__(self, adr_loader: Optional[ADRLoader] = None):
        """Initialize ADR classifier.

        Args:
            adr_loader: ADRLoader instance (creates new one if None).
        """
        self.loader = adr_loader or get_loader()
        logger.info("ADRClassifier initialized")

    def find_relevant_adrs(
        self,
        task: object,
        top_n: int = 3,
        max_results: int = 5,
    ) -> List[ADRMetadata]:
        """Find relevant ADRs for a task.

        Pipeline:
        1. Extract keywords from task
        2. Search ADRs by keywords
        3. For each match, traverse dependency graph
        4. Rank by relevance + distance
        5. Return top N

        Args:
            task: Task object (EnrichedTask or similar).
            top_n: Number of seed ADRs to start traversal from.
            max_results: Max ADRs to return.

        Returns:
            List of relevant ADRMetadata objects ranked by relevance.
        """
        summary = self._summary(task)
        named: List[str] = []
        for num in _ADR_REF_RE.findall(summary or ""):
            adr_id = f"ADR-{int(num):04d}"
            if adr_id not in named and self.loader.get_adr(adr_id) is not None:
                named.append(adr_id)
        # The named ids' numbers are not topic words for the lexical match:
        # every ADR that merely cites ADR-0952 would otherwise match it.
        numbers = {n.lstrip("0") for n in _ADR_REF_RE.findall(summary or "")}
        keywords = [k for k in self._extract_keywords(task) if k.lstrip("0") not in numbers]
        if not keywords and not named:
            return []

        # Every returned ADR carries its OWN lexical score against the task.
        # The top_n direct matches seed the graph walk. A graph neighbour (depends_on /
        # related / supersedes) is only added when it is itself relevant: it
        # inherits a decayed share of its seed's score, blended with its own,
        # and must still clear MIN_RELEVANCE. The old code added every 2-hop
        # neighbour unscored and then cut ``list(set)[:max_results]`` — an
        # arbitrary subset, which could drop the best seed for a stranger.
        # With an ADR named, the rest of the request is usually one verb
        # ("Merge ADR-0760", "continue the ADR-0952 work"): a single matched
        # word is then no evidence of a second relevant decision (review R5-2).
        scored = self.loader.score_query(keywords, min_relevance=0.0,
                                         allow_single_term=not named) if keywords else []
        # No extra cut against the named ADR's synthetic 1.0: it dropped the
        # best second ADR of "Does ADR-0952 conflict with the session ledger?"
        # (review R8-CEL-3). The loader already cuts against the best LEXICAL
        # score, and a named request needs two strong matched terms.
        if named:
            # Next to an ADR named by id, another one is admitted only when the
            # request names its TOPIC: two strong (non-weak) request words in
            # its title. A body-only "review … today" match filled four of
            # five slots (review R9-CEL-1); a fixed score cut instead dropped
            # the real neighbour of "Does ADR-0952 conflict with the session
            # ledger design?" (R8-CEL-3). Measured on 2400 tailed requests:
            # 103 with an off-graph extra (prev. 486 / 934), 10 of 12
            # multi-topic requests keep their neighbour (prev. 8 / 12).
            from .adr_loader import _is_weak
            strong_kw = {k for k in keywords if not _is_weak(k)}
            titles = getattr(self.loader, "_title_tokens", {})
            scored = [(i, s) for i, s in scored if len(strong_kw & titles.get(i, set())) >= 2]
        own: Dict[str, float] = dict(scored)
        direct = [(i, 1.0) for i in named[:max_results]]
        seen_ids = set(named)
        for i, s in scored:
            if len(direct) >= max_results or s < MIN_RELEVANCE:
                break
            # One slot per ADR id: a duplicate file (``ADR-NNNN~stem``) of an
            # id already taken must not consume a seed slot (review R4-5).
            meta = self.loader.get_adr(i)
            key = meta.id if meta is not None and meta.id else i
            if key in seen_ids:
                continue
            seen_ids.add(key)
            direct.append((i, s))
        if not direct:
            return []
        final: Dict[str, float] = dict(direct)
        for seed_id, seed_score in direct[:top_n]:
            for n_id in self.loader.find_related_adr_ids(seed_id, depth=2, max_results=max_results * 2):
                if n_id in final:
                    continue
                blended = 0.6 * own.get(n_id, 0.0) + 0.4 * seed_score * 0.5
                if blended >= MIN_RELEVANCE:
                    final[n_id] = round(blended, 4)

        # Deduplicate BEFORE the cut: a second file carrying the same id (stored
        # as ``ADR-NNNN~stem``) must not take a result slot and then be dropped,
        # pushing a relevant ADR out (review R4-5). Named ids lead.
        results = []
        order = sorted(final.items(), key=lambda x: (x[0] not in named, -x[1], x[0]))
        for adr_id, score in order:
            if len(results) >= max_results:
                break
            metadata = self.loader.get_adr(adr_id)
            if metadata and metadata.id and metadata.id not in {r.id for r in results}:
                results.append(dataclasses.replace(metadata, relevance=score))

        logger.info(f"Found {len(results)} relevant ADRs for task")
        return results

    @staticmethod
    def _summary(task: object) -> str:
        if hasattr(task, "normalized") and hasattr(task.normalized, "summary"):
            text = str(task.normalized.summary or "")
        elif hasattr(task, "raw_input"):
            text = str(task.raw_input or "")
        elif hasattr(task, "summary"):
            text = str(task.summary or "")
        else:
            text = str(task)[:500]
        # A bridge group-chat observer transcript in front of the owner's
        # message is framing, not the question: its boilerplate filled the
        # first 24 tokens and steered retrieval (review R4-2). The bridge
        # passes the owner's text alone; this is the backstop.
        return _OBSERVER_BLOCK_RE.sub("", text)

    def _extract_keywords(self, task: object) -> List[str]:
        """Whole-word, stop-word-free, stemmed tokens of the task; the first 24
        carry it (a long brief's tail is usually boilerplate)."""
        summary = self._summary(task)
        return tokenize(summary)[:24] if summary else []
