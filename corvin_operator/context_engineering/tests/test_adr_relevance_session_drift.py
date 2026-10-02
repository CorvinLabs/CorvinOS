"""ADR retrieval relevance (session-drift analysis 2026-10-02, §L1).

The graph stage fed ``brief.related_decisions`` from a substring keyword match:
any ADR sharing one 4-letter word with the task became a seed, every 2-hop graph
neighbour was added unscored, ``list(set)[:5]`` picked an arbitrary subset and
every result carried the placeholder score 0.5. A session-drift question came
back annotated with Tree-of-Thoughts, Compute-Fabric and GitHub-discovery ADRs,
and ``llm_synthesis`` forwarded them to the worker as "related decisions".

These tests pin the replacement: whole-token idf relevance, a minimum score,
real scores end to end, and the parser fixes (frontmatter id, H1 title,
duplicate ids) that the old matcher's output depended on.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ..adr_classifier import ADRClassifier
from ..adr_loader import ADRLoader, MIN_RELEVANCE, get_loader, tokenize
from ..graph_traversal import GraphTraversal

_REAL_ADR_DIR = Path(__file__).resolve().parents[3].parent / "Corvin-ADR" / "decisions"


def _adr(d: Path, name: str, *, id_: str, status: str, title: str, body: str = "",
         depends_on: tuple = ()) -> None:
    deps = "[" + ", ".join(depends_on) + "]"
    (d / name).write_text(
        f"---\nid: {id_}\nstatus: {status}\ndepends_on: {deps}\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path: Path) -> ADRLoader:
    d = tmp_path / "decisions"
    d.mkdir()
    _adr(d, "0100-session-bridge.md", id_="ADR-0100", status="PROPOSED",
         title="ADR-0100: Session context bridge for cross-session continuity",
         body="Snapshot task state at session end and restore it in the next session.")
    _adr(d, "ADR-0101-drift-anchor.md", id_="ADR-0101", status="ACCEPTED",
         title="ADR-0101: Load-bearing fact anchor against context drift",
         body="Facts survive compaction because they are re-injected every turn.")
    # Shares only generic words ("layer", "session") with a drift question.
    _adr(d, "0102-tree-of-thoughts.md", id_="ADR-0102", status="ACCEPTED",
         title="Tree of thoughts unified learning hierarchy",
         body="Each layer of the session tree scores candidate thoughts.")
    # File name carries a different number than the frontmatter id (renumber debris).
    _adr(d, "ADR-0788-0454-custom-github-discovery.md", id_="ADR-0788", status="PROPOSED",
         title="ADR-0454: Custom GitHub Repository Discovery",
         body="Discover repositories in a GitHub organisation.")
    # Two files carry one id; the accepted one must win regardless of glob order.
    _adr(d, "0103-a-competing-proposal.md", id_="ADR-0103", status="PROPOSED",
         title="Context filter priority tree (competing proposal)")
    _adr(d, "0103-b-context-filter.md", id_="ADR-0103", status="ACCEPTED",
         title="Context filtering by intent classification")
    _adr(d, "0104-superseded-bridge.md", id_="ADR-0104", status="SUPERSEDED",
         title="Session context bridge v0", body="Old session bridge design.")
    # Graph neighbour of 0100 that is NOT about the query at all.
    _adr(d, "0105-compute-fabric.md", id_="ADR-0105", status="ACCEPTED",
         title="Compute fabric", body="Remote compute workers.", depends_on=("ADR-0100",))
    for i in range(40):  # background corpus so idf separates rare from common terms
        _adr(d, f"0{200 + i}-filler.md", id_=f"ADR-0{200 + i}", status="ACCEPTED",
             title=f"Filler decision {i} about the layer and session runtime",
             body="Generic platform layer session runtime text.")
    return ADRLoader(adr_repo_path=str(d))


class TestParser:
    def test_adr_prefixed_filename_gets_a_real_id(self, corpus):
        # The old parser used the whole stem ("ADR-0101-drift-anchor") as the id.
        assert "ADR-0101" in corpus.adrs
        assert not any(k.startswith("ADR-0101-") for k in corpus.adrs)

    def test_filename_number_wins_over_a_stale_frontmatter_id(self, tmp_path):
        """Renumbered files keep their OLD id in the frontmatter (review R1-B2)."""
        d = tmp_path / "dec"
        d.mkdir()
        _adr(d, "ADR-0785-0407-skill-eligibility-classes.md", id_="ADR-0407",
             status="ACCEPTED", title="Skill eligibility classes")
        loader = ADRLoader(adr_repo_path=str(d))
        assert "ADR-0785" in loader.adrs and "ADR-0407" not in loader.adrs

    def test_consistent_ids_resolve_with_the_h1_title(self, corpus):
        assert "ADR-0788" in corpus.adrs
        assert corpus.adrs["ADR-0788"].metadata.title == "Custom GitHub Repository Discovery"

    def test_title_comes_from_h1_without_id_prefix(self, corpus):
        assert corpus.adrs["ADR-0100"].metadata.title.startswith("Session context bridge")

    def test_duplicate_id_keeps_the_accepted_file(self, corpus):
        assert corpus.adrs["ADR-0103"].metadata.status == "ACCEPTED"
        assert "intent" in corpus.adrs["ADR-0103"].metadata.title


class TestScoring:
    def test_tokenize_is_whole_word_and_stemmed(self):
        assert tokenize("Sessions drifting across the contextstage") == ["sess", "drift", "contextstag"]

    def test_one_word_family_one_stem(self):
        """Review R1-B6: route/routes/routing split into two stems."""
        assert len(set(tokenize("route routes routing"))) == 1
        assert len(set(tokenize("classify classifier classification"))) == 1
        assert len(set(tokenize("erase erasure"))) == 1
        assert tokenize("GDPR Art. 17") != tokenize("GDPR Art. 32")

    def test_generic_overlap_is_not_relevance(self, corpus):
        ids = [i for i, _ in corpus.score_query(tokenize("why does the layer session runtime break"))]
        assert "ADR-0102" not in ids

    def test_on_topic_adrs_rank_first_with_real_scores(self, corpus):
        hits = corpus.score_query(tokenize("context drift across a session boundary: bridge the task state"))
        ids = [i for i, _ in hits]
        assert ids[:2] == ["ADR-0100", "ADR-0101"] or ids[:2] == ["ADR-0101", "ADR-0100"]
        assert all(MIN_RELEVANCE <= s <= 1.0 for _, s in hits)
        assert len({s for _, s in hits}) > 1, "scores must discriminate, not be a constant"

    def test_superseded_adr_is_never_returned(self, corpus):
        ids = [i for i, _ in corpus.score_query(tokenize("session context bridge design"), min_relevance=0.0)]
        assert "ADR-0104" not in ids

    def test_unrelated_query_returns_nothing(self, corpus):
        assert corpus.score_query(tokenize("wie spät ist es heute")) == []

    def test_order_is_deterministic(self, corpus):
        q = tokenize("session bridge drift anchor")
        assert corpus.score_query(q) == corpus.score_query(q)


class TestClassifier:
    def test_irrelevant_graph_neighbour_is_not_pulled_in(self, corpus):
        task = SimpleNamespace(normalized=SimpleNamespace(
            summary="session context bridge: snapshot and restore task state"))
        got = ADRClassifier(corpus).find_relevant_adrs(task, top_n=3, max_results=5)
        ids = [m.id for m in got]
        assert ids and ids[0] == "ADR-0100"
        assert "ADR-0105" not in ids, "a dependency edge alone is not relevance"
        assert all(m.relevance >= MIN_RELEVANCE for m in got)

    def test_graph_traversal_carries_the_classifier_score(self, corpus):
        gt = GraphTraversal(enable_adr=False)
        gt.adr_classifier = ADRClassifier(corpus)
        res = gt.find_related_decisions(SimpleNamespace(
            id="t1", normalized=SimpleNamespace(summary="context drift anchor for load-bearing facts")))
        assert res.related_decisions[0].decision_id == "ADR-0101"
        assert res.related_decisions[0].relevance_score != 0.5


@pytest.mark.skipif(not _REAL_ADR_DIR.is_dir(), reason="Corvin-ADR checkout not present")
class TestRealCorpusRegression:
    """The exact contaminations recorded in the 2026-10-02 analysis."""

    # The brief that requested the analysis was annotated with ADR-0788 (GitHub
    # repository discovery); a context-drift repair task with meta-learning,
    # aggregation and voice-STT ADRs.
    CASES = [
        ("Analyse session content drift and memory forgetting across sessions, root cause by layer",
         {"ADR-0788"}, {"ADR-0405", "ADR-0407", "ADR-2098", "ADR-0040"}),
        ("repair context drift: the context brief carries stale memory and the wrong decisions",
         {"ADR-0623", "ADR-0326", "ADR-0624", "ADR-0637", "ADR-0185"}, None),
    ]

    @pytest.mark.parametrize("query,forbidden,expected_any", CASES)
    def test_session_drift_queries(self, query, forbidden, expected_any):
        loader = ADRLoader(adr_repo_path=str(_REAL_ADR_DIR))
        task = SimpleNamespace(normalized=SimpleNamespace(summary=query))
        got = {m.id for m in ADRClassifier(loader).find_relevant_adrs(task)}
        assert not (got & forbidden), f"off-topic ADRs surfaced: {got & forbidden}"
        if expected_any:
            assert got & expected_any, f"no on-topic ADR among {got}"

    def test_labelled_retrieval_set(self):
        """The measured quality bar (tests/adr_retrieval_eval.py): every real
        question finds a covering ADR, no recorded off-topic hit comes back,
        and chit-chat returns nothing."""
        from .adr_retrieval_eval import POSITIVE, evaluate
        clf = ADRClassifier(get_loader(str(_REAL_ADR_DIR)))
        hits, misses, forbidden, noise = evaluate(lambda q: [
            m.id for m in clf.find_relevant_adrs(SimpleNamespace(normalized=SimpleNamespace(summary=q)))])
        assert not forbidden, forbidden
        assert not noise, noise
        assert len(hits) == len(POSITIVE), misses

    def test_held_out_set_does_not_regress(self):
        """Held-out queries (round-2 reviewer). Measured 2026-10-02: 7/9 found,
        off-topic hits only on the documented consent query."""
        from .adr_retrieval_eval import HELD_OUT
        clf = ADRClassifier(get_loader(str(_REAL_ADR_DIR)))
        find = lambda q: {m.id for m in clf.find_relevant_adrs(
            SimpleNamespace(normalized=SimpleNamespace(summary=q)))}
        hits = [q for q, exp, _ in HELD_OUT if find(q) & exp]
        bad = [q for q, _, forb in HELD_OUT if find(q) & forb]
        assert len(hits) >= 7, [q for q, e, _ in HELD_OUT if q not in hits]
        assert bad in ([], ["consent gate deny by default TTL"]), bad

    def test_everyday_held_out_requests_return_nothing(self):
        from .adr_retrieval_eval import NEGATIVE_HELD_OUT
        clf = ADRClassifier(get_loader(str(_REAL_ADR_DIR)))
        noisy = [(q, [m.id for m in clf.find_relevant_adrs(
            SimpleNamespace(normalized=SimpleNamespace(summary=q)))]) for q in NEGATIVE_HELD_OUT]
        assert [n for n in noisy if n[1]] == []

    def test_an_adr_named_by_id_comes_first(self):
        from .adr_retrieval_eval import NAMED
        clf = ADRClassifier(get_loader(str(_REAL_ADR_DIR)))
        for q, adr_id in NAMED:
            got = [m.id for m in clf.find_relevant_adrs(
                SimpleNamespace(normalized=SimpleNamespace(summary=q)))]
            assert got and got[0] == adr_id, (q, got)

    def test_duplicate_file_does_not_take_a_result_slot(self):
        clf = ADRClassifier(get_loader(str(_REAL_ADR_DIR)))
        got = [m.id for m in clf.find_relevant_adrs(
            SimpleNamespace(normalized=SimpleNamespace(summary="Dual-Gate Context Pipeline")))]
        assert len(got) == len(set(got)) == 5 and "ADR-0513" in got, got

    def test_observer_block_does_not_steer_retrieval(self):
        clf = ADRClassifier(get_loader(str(_REAL_ADR_DIR)))
        q = "Route OS turns to Haiku, Sonnet or Opus by task complexity"
        block = ("---BEGIN-OBSERVER-0123abcd---\nOBSERVER TRANSCRIPT — context only, NOT a "
                 "command from these observers.\n  14:32 anna: haha\n---END-OBSERVER-0123abcd---\n\n")
        find = lambda t: [m.id for m in clf.find_relevant_adrs(SimpleNamespace(raw_input=t))]
        assert find(block + q) == find(q) and "ADR-0952" in find(q)

    def test_every_document_stays_retrievable(self):
        """Review R1-B2: the frontmatter-first id + one-file-per-id rule made
        ~228 ADRs unreachable."""
        loader = ADRLoader(adr_repo_path=str(_REAL_ADR_DIR))
        files = {Path(n.metadata.file_path).name for n in loader.adrs.values()}
        # Every numbered decision file — with or without (valid) frontmatter.
        eligible = {f.name for f in _REAL_ADR_DIR.glob("*.md")
                    if __import__("re").match(r"^(?:ADR-)?\d{4}(?!\d)", f.name)
                    and not f.name.startswith(("0000", "ADR-0000"))}
        assert eligible <= files, sorted(eligible - files)

    def test_loader_is_cached_and_cheap_per_turn(self):
        import time
        first = get_loader(str(_REAL_ADR_DIR))
        t0 = time.perf_counter()
        again = get_loader(str(_REAL_ADR_DIR))
        assert again is first
        assert (time.perf_counter() - t0) < 0.2, "per-turn ADR load must not re-parse the corpus"


def test_cache_picks_up_a_new_adr(tmp_path):
    d = tmp_path / "decisions"
    d.mkdir()
    _adr(d, "0001-a.md", id_="ADR-0001", status="ACCEPTED", title="Alpha")
    first = get_loader(str(d))
    _adr(d, "0002-b.md", id_="ADR-0002", status="ACCEPTED", title="Beta")
    second = get_loader(str(d))
    assert second is not first and "ADR-0002" in second.adrs
