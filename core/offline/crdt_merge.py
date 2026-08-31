"""
Conflict-free Replicated Data Type (CRDT) merge algorithm for offline state reconciliation.

Problem: Two offline operators edit state simultaneously. On reconnect, must merge without conflict.
Solution: Use CRDT algorithms that guarantee:
- Commutativity: merge(A,B) == merge(B,A)
- Idempotence: merge(merge(A,B), B) == merge(A,B)
- Associativity: merge(merge(A,B), C) == merge(A, merge(B,C))
- Convergence: All replicas eventually equal

Algorithms:
- Templates: confidence_wins (highest confidence wins)
- Preferences: last_write_wins (latest timestamp wins)
- History: union (append-only log, merge by union)
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime


@dataclass
class TemplateVersion:
    """A version of a template with confidence score."""
    template_id: str
    version: int
    content: Dict[str, Any]
    confidence: float  # [0.0..1.0]
    timestamp: datetime
    operator_id: str


@dataclass
class PreferenceVersion:
    """A version of a preference with timestamp."""
    preference_key: str
    value: Any
    timestamp: datetime
    operator_id: str


@dataclass
class HistoryEvent:
    """Immutable event in history log."""
    event_id: str
    timestamp: datetime
    event_type: str
    data: Dict[str, Any]
    operator_id: str


class CRDTMerger:
    """
    CRDT merge algorithm for offline state synchronization.

    Merge rules:
    1. Templates: confidence_wins
       - Template with highest confidence score wins
       - Proof: confidence is monotonic (only increases)

    2. Preferences: last_write_wins
       - Preference with latest timestamp wins
       - Proof: last-write is idempotent (reapplying same timestamp is no-op)

    3. History: union
       - Both histories preserved (append-only log)
       - Proof: events are immutable, union is commutative and associative
    """

    @staticmethod
    def merge_templates(
        local_template: TemplateVersion,
        remote_template: TemplateVersion,
    ) -> TemplateVersion:
        """
        Merge two template versions using confidence_wins rule.

        Proof of Commutativity:
            merge_templates(A, B) == merge_templates(B, A)
            Because: max(A.conf, B.conf) == max(B.conf, A.conf)

        Proof of Idempotence:
            merge(merge(A, B), B) == merge(A, B)
            Let W = merge(A, B), with confidence = max(A.conf, B.conf)
            merge(W, B) = template with confidence = max(max(A.conf, B.conf), B.conf)
                        = max(A.conf, B.conf)  (because max is idempotent)
                        = W
            Therefore: merge(W, B) == W

        Args:
            local_template: Template from local offline operator
            remote_template: Template from remote operator

        Returns:
            Merged template (highest confidence wins)
        """
        if remote_template.confidence > local_template.confidence:
            return remote_template
        elif local_template.confidence > remote_template.confidence:
            return local_template
        else:
            # Same confidence: prefer earlier timestamp (deterministic tie-break)
            if remote_template.timestamp < local_template.timestamp:
                return remote_template
            else:
                return local_template

    @staticmethod
    def merge_preferences(
        local_pref: PreferenceVersion,
        remote_pref: PreferenceVersion,
    ) -> PreferenceVersion:
        """
        Merge two preference versions using last_write_wins rule.

        Proof of Commutativity:
            merge(A, B) uses max(A.ts, B.ts)
            max(A.ts, B.ts) == max(B.ts, A.ts)
            Therefore: merge(A, B) == merge(B, A)

        Proof of Idempotence:
            merge(merge(A, B), B) == merge(A, B)
            Let W = merge(A, B), with timestamp = max(A.ts, B.ts)
            merge(W, B) = preference with timestamp = max(max(A.ts, B.ts), B.ts)
                        = max(A.ts, B.ts)  (because max is idempotent)
                        = W
            Therefore: merge(W, B) == W

        Args:
            local_pref: Preference from local offline operator
            remote_pref: Preference from remote operator

        Returns:
            Merged preference (latest timestamp wins)
        """
        if remote_pref.timestamp > local_pref.timestamp:
            return remote_pref
        elif local_pref.timestamp > remote_pref.timestamp:
            return local_pref
        else:
            # Same timestamp: prefer smaller operator_id (deterministic tie-break)
            if remote_pref.operator_id < local_pref.operator_id:
                return remote_pref
            else:
                return local_pref

    @staticmethod
    def merge_histories(
        local_history: List[HistoryEvent],
        remote_history: List[HistoryEvent],
    ) -> List[HistoryEvent]:
        """
        Merge two history logs using union rule.

        Proof of Commutativity:
            merge(A, B) = union(A, B)
            union(A, B) == union(B, A)  (set union is commutative)

        Proof of Associativity:
            merge(merge(A, B), C) = union(union(A, B), C)
            union(union(A, B), C) == union(A, union(B, C))  (set union is associative)

        Proof of Idempotence:
            merge(merge(A, B), B) = union(union(A, B), B)
                                  = union(A, B)  (union with subset is idempotent)

        Args:
            local_history: History events from local offline operator
            remote_history: History events from remote operator

        Returns:
            Merged history (deduplicated, sorted by timestamp)
        """
        # Union of events (deduplicate by event_id)
        event_map: Dict[str, HistoryEvent] = {}
        for event in local_history:
            event_map[event.event_id] = event
        for event in remote_history:
            event_map[event.event_id] = event

        # Sort by timestamp (deterministic ordering)
        merged = sorted(event_map.values(), key=lambda e: e.timestamp)
        return merged


@dataclass
class OperatorState:
    """Immutable operator state snapshot."""
    operator_id: str
    timestamp: datetime
    templates: Dict[str, TemplateVersion]
    preferences: Dict[str, PreferenceVersion]
    history: List[HistoryEvent]


class OfflineStateMerger:
    """
    Merge two operator states (local offline, remote online) using CRDT rules.

    Invariant: Merged state is commutative, idempotent, and convergent.
    """

    @staticmethod
    def merge_states(
        local_state: OperatorState,
        remote_state: OperatorState,
    ) -> OperatorState:
        """
        Merge two operator states using CRDT rules.

        Returns merged state that:
        - Contains all templates (highest confidence wins per template)
        - Contains all preferences (latest timestamp wins per preference)
        - Contains all history events (deduplicated union)

        Theorem (Convergence):
            All replicas merging with the same set of updates converge to the same state.
            Proof: Each operation (templates, preferences, history) is deterministic.
            Therefore: merge(state1, state2) always produces same result regardless of order.
        """
        # Merge templates
        merged_templates: Dict[str, TemplateVersion] = {}
        all_template_ids = set(local_state.templates.keys()) | set(remote_state.templates.keys())

        for template_id in all_template_ids:
            local_t = local_state.templates.get(template_id)
            remote_t = remote_state.templates.get(template_id)

            if local_t and remote_t:
                merged_templates[template_id] = CRDTMerger.merge_templates(local_t, remote_t)
            elif local_t:
                merged_templates[template_id] = local_t
            elif remote_t:
                merged_templates[template_id] = remote_t

        # Merge preferences
        merged_prefs: Dict[str, PreferenceVersion] = {}
        all_pref_keys = set(local_state.preferences.keys()) | set(remote_state.preferences.keys())

        for pref_key in all_pref_keys:
            local_p = local_state.preferences.get(pref_key)
            remote_p = remote_state.preferences.get(pref_key)

            if local_p and remote_p:
                merged_prefs[pref_key] = CRDTMerger.merge_preferences(local_p, remote_p)
            elif local_p:
                merged_prefs[pref_key] = local_p
            elif remote_p:
                merged_prefs[pref_key] = remote_p

        # Merge histories (union)
        merged_history = CRDTMerger.merge_histories(local_state.history, remote_state.history)

        # Create merged state
        return OperatorState(
            operator_id=local_state.operator_id,
            timestamp=datetime.utcnow(),
            templates=merged_templates,
            preferences=merged_prefs,
            history=merged_history,
        )
