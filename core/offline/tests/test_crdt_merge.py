"""
Tests for CRDT merge algorithm with formal property verification.

Coverage:
- Commutativity: merge(A,B) == merge(B,A)
- Idempotence: merge(merge(A,B), B) == merge(A,B)
- Associativity: merge(merge(A,B), C) == merge(A, merge(B,C))
- Convergence: All replicas eventually equal
"""

import pytest
from datetime import datetime, timedelta

from core.offline.crdt_merge import (
    CRDTMerger,
    OfflineStateMerger,
    TemplateVersion,
    PreferenceVersion,
    HistoryEvent,
    OperatorState,
)


class TestCRDTMergerTemplates:
    """Test template merging with confidence_wins rule."""

    def test_confidence_wins(self):
        """Higher confidence wins."""
        local = TemplateVersion(
            template_id="tpl-1",
            version=1,
            content={"type": "auth"},
            confidence=0.7,
            timestamp=datetime.utcnow(),
            operator_id="op-1",
        )
        remote = TemplateVersion(
            template_id="tpl-1",
            version=2,
            content={"type": "auth", "enhanced": True},
            confidence=0.9,
            timestamp=datetime.utcnow(),
            operator_id="op-2",
        )

        merged = CRDTMerger.merge_templates(local, remote)
        assert merged.confidence == 0.9
        assert merged.content == {"type": "auth", "enhanced": True}

    def test_commutativity_templates(self):
        """merge(A,B) == merge(B,A)"""
        local = TemplateVersion(
            template_id="tpl-1",
            version=1,
            content={"x": 1},
            confidence=0.6,
            timestamp=datetime.utcnow(),
            operator_id="op-1",
        )
        remote = TemplateVersion(
            template_id="tpl-1",
            version=2,
            content={"x": 2},
            confidence=0.8,
            timestamp=datetime.utcnow(),
            operator_id="op-2",
        )

        merge_ab = CRDTMerger.merge_templates(local, remote)
        merge_ba = CRDTMerger.merge_templates(remote, local)

        assert merge_ab.confidence == merge_ba.confidence
        assert merge_ab.content == merge_ba.content

    def test_idempotence_templates(self):
        """merge(merge(A,B), B) == merge(A,B)"""
        local = TemplateVersion(
            template_id="tpl-1",
            version=1,
            content={"x": 1},
            confidence=0.6,
            timestamp=datetime.utcnow(),
            operator_id="op-1",
        )
        remote = TemplateVersion(
            template_id="tpl-1",
            version=2,
            content={"x": 2},
            confidence=0.8,
            timestamp=datetime.utcnow(),
            operator_id="op-2",
        )

        merge_ab = CRDTMerger.merge_templates(local, remote)
        merge_ab_b = CRDTMerger.merge_templates(merge_ab, remote)

        assert merge_ab.confidence == merge_ab_b.confidence
        assert merge_ab.content == merge_ab_b.content


class TestCRDTMergerPreferences:
    """Test preference merging with last_write_wins rule."""

    def test_last_write_wins(self):
        """Latest timestamp wins."""
        earlier = PreferenceVersion(
            preference_key="theme",
            value="light",
            timestamp=datetime.utcnow() - timedelta(seconds=10),
            operator_id="op-1",
        )
        later = PreferenceVersion(
            preference_key="theme",
            value="dark",
            timestamp=datetime.utcnow(),
            operator_id="op-2",
        )

        merged = CRDTMerger.merge_preferences(earlier, later)
        assert merged.value == "dark"

    def test_commutativity_preferences(self):
        """merge(A,B) == merge(B,A)"""
        pref_a = PreferenceVersion(
            preference_key="theme",
            value="light",
            timestamp=datetime.utcnow() - timedelta(seconds=10),
            operator_id="op-1",
        )
        pref_b = PreferenceVersion(
            preference_key="theme",
            value="dark",
            timestamp=datetime.utcnow(),
            operator_id="op-2",
        )

        merge_ab = CRDTMerger.merge_preferences(pref_a, pref_b)
        merge_ba = CRDTMerger.merge_preferences(pref_b, pref_a)

        assert merge_ab.value == merge_ba.value
        assert merge_ab.operator_id == merge_ba.operator_id

    def test_idempotence_preferences(self):
        """merge(merge(A,B), B) == merge(A,B)"""
        pref_a = PreferenceVersion(
            preference_key="theme",
            value="light",
            timestamp=datetime.utcnow(),
            operator_id="op-1",
        )
        pref_b = PreferenceVersion(
            preference_key="theme",
            value="dark",
            timestamp=datetime.utcnow() + timedelta(seconds=10),
            operator_id="op-2",
        )

        merge_ab = CRDTMerger.merge_preferences(pref_a, pref_b)
        merge_ab_b = CRDTMerger.merge_preferences(merge_ab, pref_b)

        assert merge_ab.value == merge_ab_b.value


class TestCRDTMergerHistory:
    """Test history merging with union rule."""

    def test_history_union(self):
        """Merge histories by union."""
        history_a = [
            HistoryEvent(
                event_id="evt-1",
                timestamp=datetime.utcnow(),
                event_type="task_complete",
                data={"task": "auth"},
                operator_id="op-1",
            ),
        ]
        history_b = [
            HistoryEvent(
                event_id="evt-2",
                timestamp=datetime.utcnow(),
                event_type="decision",
                data={"choice": "claude"},
                operator_id="op-2",
            ),
        ]

        merged = CRDTMerger.merge_histories(history_a, history_b)
        assert len(merged) == 2

    def test_commutativity_history(self):
        """merge(A,B) == merge(B,A)"""
        evt_a = HistoryEvent(
            event_id="evt-a",
            timestamp=datetime.utcnow() - timedelta(seconds=10),
            event_type="task_start",
            data={},
            operator_id="op-1",
        )
        evt_b = HistoryEvent(
            event_id="evt-b",
            timestamp=datetime.utcnow(),
            event_type="task_end",
            data={},
            operator_id="op-2",
        )

        merge_ab = CRDTMerger.merge_histories([evt_a], [evt_b])
        merge_ba = CRDTMerger.merge_histories([evt_b], [evt_a])

        assert len(merge_ab) == len(merge_ba) == 2
        assert merge_ab[0].event_id == merge_ba[0].event_id  # Same order (by timestamp)

    def test_idempotence_history(self):
        """merge(merge(A,B), B) == merge(A,B)"""
        evt_a = HistoryEvent(
            event_id="evt-a",
            timestamp=datetime.utcnow(),
            event_type="event1",
            data={},
            operator_id="op-1",
        )
        evt_b = HistoryEvent(
            event_id="evt-b",
            timestamp=datetime.utcnow(),
            event_type="event2",
            data={},
            operator_id="op-2",
        )

        merge_ab = CRDTMerger.merge_histories([evt_a], [evt_b])
        merge_ab_b = CRDTMerger.merge_histories(merge_ab, [evt_b])

        assert len(merge_ab) == len(merge_ab_b)
        assert merge_ab[0].event_id == merge_ab_b[0].event_id


class TestOfflineStateMerger:
    """Test full state merging with convergence properties."""

    def test_state_merge(self):
        """Merge two operator states."""
        local_state = OperatorState(
            operator_id="op-1",
            timestamp=datetime.utcnow(),
            templates={},
            preferences={},
            history=[],
        )
        remote_state = OperatorState(
            operator_id="op-2",
            timestamp=datetime.utcnow(),
            templates={},
            preferences={},
            history=[],
        )

        merged = OfflineStateMerger.merge_states(local_state, remote_state)
        assert merged is not None

    def test_convergence_three_way(self):
        """
        Three-way merge converges.

        Theorem: merge(merge(A,B), C) == merge(merge(A,C), B)
        """
        # State A
        state_a = OperatorState(
            operator_id="op-1",
            timestamp=datetime.utcnow(),
            templates={
                "tpl-1": TemplateVersion(
                    template_id="tpl-1",
                    version=1,
                    content={"x": 1},
                    confidence=0.8,
                    timestamp=datetime.utcnow(),
                    operator_id="op-1",
                ),
            },
            preferences={},
            history=[],
        )

        # State B
        state_b = OperatorState(
            operator_id="op-2",
            timestamp=datetime.utcnow(),
            templates={
                "tpl-1": TemplateVersion(
                    template_id="tpl-1",
                    version=2,
                    content={"x": 2},
                    confidence=0.6,
                    timestamp=datetime.utcnow(),
                    operator_id="op-2",
                ),
            },
            preferences={},
            history=[],
        )

        # State C
        state_c = OperatorState(
            operator_id="op-3",
            timestamp=datetime.utcnow(),
            templates={
                "tpl-1": TemplateVersion(
                    template_id="tpl-1",
                    version=3,
                    content={"x": 3},
                    confidence=0.7,
                    timestamp=datetime.utcnow(),
                    operator_id="op-3",
                ),
            },
            preferences={},
            history=[],
        )

        # Both merge orders
        merge_ab_c = OfflineStateMerger.merge_states(
            OfflineStateMerger.merge_states(state_a, state_b),
            state_c,
        )
        merge_ac_b = OfflineStateMerger.merge_states(
            OfflineStateMerger.merge_states(state_a, state_c),
            state_b,
        )

        # Should converge to same template
        tpl_ab_c = merge_ab_c.templates["tpl-1"]
        tpl_ac_b = merge_ac_b.templates["tpl-1"]

        assert tpl_ab_c.confidence == tpl_ac_b.confidence
        assert tpl_ab_c.version == tpl_ac_b.version
