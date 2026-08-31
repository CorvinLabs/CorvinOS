"""Active learning loop: exec → event → confidence → suggest (Phase 3)."""
from __future__ import annotations
from .storage import LearningEventStore
from .confidence import update_confidence
from .models import LearningEvent, TreeNode
from typing import Callable, Any


class ActiveLearningLoop:
    """Closed-loop learning: method execution → metrics → confidence update."""
    
    def __init__(self, store: LearningEventStore):
        self.store = store
    
    async def execute_with_learning(
        self,
        method_id: str,
        method_fn: Callable,
        context: dict[str, Any] = None,
        *args,
        **kwargs
    ) -> dict:
        """Execute a method and emit LearningEvent.
        
        Returns: {
            "result": method_fn(...) result,
            "success": bool,
            "event_recorded": bool,
            "suggestions": [list of alternative methods],
            "warnings": [antipattern alerts],
        }
        """
        context = context or {}
        success = False
        error_type = None
        result = None
        
        try:
            # 1. Execute the method
            result = await method_fn(*args, **kwargs)
            success = True
        except Exception as e:
            error_type = type(e).__name__
            result = None
        
        # 2. Emit learning event
        event_type = "used" if success else "failed"
        confidence_delta = +0.05 if success else -0.15
        
        event = LearningEvent(
            subject_id=method_id,
            event_type=event_type,
            confidence_delta=confidence_delta,
            reason=f"{event_type} in production",
            context={"error_type": error_type, **context}
        )
        
        self.store.append_event(method_id, event)
        
        # 3. Update confidence
        node = self.store.get_node(method_id)
        if node:
            new_conf = update_confidence(node, event)
            
            # 4. Auto-suggest if confidence drops
            suggestions = []
            if new_conf < 0.5:
                alternatives = self.find_alternatives(method_id, threshold=0.7)
                suggestions = [alt.id for alt in alternatives]
            
            # 5. Check antipatterns
            warnings = []
            if node.anti_when:
                for anti_context in node.anti_when:
                    if any(anti_context in str(v) for v in context.values()):
                        warnings.append(f"⚠️ Antipattern detected: '{anti_context}' context")
            
            return {
                "result": result,
                "success": success,
                "event_recorded": True,
                "new_confidence": new_conf,
                "suggestions": suggestions,
                "warnings": warnings,
            }
        
        return {
            "result": result,
            "success": success,
            "event_recorded": True,
            "suggestions": [],
            "warnings": [],
        }
    
    def find_alternatives(self, method_id: str, threshold: float = 0.7) -> list[TreeNode]:
        """Find alternative methods with higher confidence."""
        current = self.store.get_node(method_id)
        if not current:
            return []
        
        alternatives = []
        for node in self.store.all_nodes():
            if node.level == "method" and node.id != method_id and node.confidence >= threshold:
                alternatives.append(node)
        
        return sorted(alternatives, key=lambda n: n.confidence, reverse=True)[:3]
