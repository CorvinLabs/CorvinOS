"""Fact extraction and preservation from storyboards.

Ensures that optimized narratives preserve all original facts,
preventing hallucination and maintaining accuracy.
"""

from typing import List, Dict, Any, Set
import re
from dataclasses import dataclass


@dataclass
class Fact:
    """Represents a factual statement"""
    text: str
    source_scene_id: str
    fact_type: str  # "narration", "asset", "visual"
    importance_score: float = 1.0

    def __hash__(self):
        return hash(self.text.lower().strip())

    def __eq__(self, other):
        if isinstance(other, Fact):
            return self.text.lower().strip() == other.text.lower().strip()
        return self.text.lower().strip() == str(other).lower().strip()


class FactExtractor:
    """Extract facts from storyboard, ensure preservation in optimized narrative"""

    def __init__(self):
        self.extracted_facts: Set[Fact] = set()
        self.fact_dependencies: Dict[str, List[str]] = {}

    def extract_facts(self, storyboard: Dict[str, Any]) -> List[Fact]:
        """Pull all key facts from original storyboard.

        Args:
            storyboard: Dictionary with 'scenes' containing 'narration' and 'assets'

        Returns:
            List of extracted facts
        """
        self.extracted_facts.clear()

        for scene in storyboard.get("scenes", []):
            scene_id = scene.get("id", "unknown")

            # Extract facts from narration
            if "narration" in scene:
                narration_facts = self._extract_from_text(
                    scene["narration"],
                    scene_id,
                    "narration"
                )
                self.extracted_facts.update(narration_facts)

            # Extract facts from visual assets
            if "assets" in scene:
                asset_facts = self._extract_from_assets(
                    scene["assets"],
                    scene_id
                )
                self.extracted_facts.update(asset_facts)

            # Extract facts from metadata
            if "metadata" in scene:
                metadata_facts = self._extract_from_metadata(
                    scene["metadata"],
                    scene_id
                )
                self.extracted_facts.update(metadata_facts)

        return sorted(list(self.extracted_facts),
                     key=lambda f: f.importance_score,
                     reverse=True)

    def _extract_from_text(self, text: str, scene_id: str,
                          fact_type: str) -> Set[Fact]:
        """Extract factual sentences from narration text"""
        facts = set()

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())

        for sentence in sentences:
            if len(sentence.strip()) > 10:  # Ignore very short sentences
                fact = Fact(
                    text=sentence.strip(),
                    source_scene_id=scene_id,
                    fact_type=fact_type,
                    importance_score=self._score_importance(sentence)
                )
                facts.add(fact)

        return facts

    def _extract_from_assets(self, assets: List[Dict[str, Any]],
                             scene_id: str) -> Set[Fact]:
        """Extract facts from visual assets"""
        facts = set()

        for asset in assets:
            if "description" in asset:
                fact = Fact(
                    text=f"Asset: {asset['description']}",
                    source_scene_id=scene_id,
                    fact_type="asset",
                    importance_score=self._score_importance(asset["description"])
                )
                facts.add(fact)

            if "transcript" in asset:
                for line in asset["transcript"].split("\n"):
                    if line.strip():
                        fact = Fact(
                            text=line.strip(),
                            source_scene_id=scene_id,
                            fact_type="asset",
                            importance_score=0.9
                        )
                        facts.add(fact)

        return facts

    def _extract_from_metadata(self, metadata: Dict[str, Any],
                               scene_id: str) -> Set[Fact]:
        """Extract facts from scene metadata"""
        facts = set()

        if "key_messages" in metadata:
            for msg in metadata["key_messages"]:
                fact = Fact(
                    text=msg,
                    source_scene_id=scene_id,
                    fact_type="metadata",
                    importance_score=1.0
                )
                facts.add(fact)

        if "statistics" in metadata:
            for stat in metadata["statistics"]:
                fact = Fact(
                    text=str(stat),
                    source_scene_id=scene_id,
                    fact_type="metadata",
                    importance_score=1.0
                )
                facts.add(fact)

        return facts

    def _score_importance(self, text: str) -> float:
        """Score importance of a fact (0.0 to 1.0)"""
        score = 0.5  # Base score

        # Numbers and statistics are important
        if re.search(r'\d+%|\$\d+|#\d+', text):
            score += 0.3

        # Keywords indicating importance
        important_keywords = ["must", "critical", "essential", "important",
                             "key", "only", "first", "unique", "breakthrough"]
        for keyword in important_keywords:
            if keyword.lower() in text.lower():
                score += 0.1

        # Longer sentences tend to have more substance
        if len(text.split()) > 10:
            score += 0.1

        return min(score, 1.0)

    def validate_preservation(self, original_facts: List[Fact],
                             optimized_narrative: Dict[str, Any]) -> bool:
        """Ensure optimized narrative includes all original facts.

        Args:
            original_facts: List of facts extracted from original storyboard
            optimized_narrative: The optimized narrative structure

        Returns:
            True if all facts are preserved, False otherwise
        """
        optimized_text = self._extract_narrative_text(optimized_narrative)

        for fact in original_facts:
            if not self._fact_is_present(fact, optimized_text):
                return False

        return True

    def _extract_narrative_text(self, narrative: Dict[str, Any]) -> str:
        """Extract all text from a narrative structure"""
        text_parts = []

        for scene in narrative.get("scenes", []):
            if "narration" in scene:
                text_parts.append(scene["narration"])
            if "description" in scene:
                text_parts.append(scene["description"])

        return " ".join(text_parts).lower()

    def _fact_is_present(self, fact: Fact, text: str) -> bool:
        """Check if a fact is present in text (fuzzy matching)"""
        # Exact match
        if fact.text.lower() in text:
            return True

        # Partial match (key phrases)
        phrases = self._extract_key_phrases(fact.text)
        for phrase in phrases:
            if phrase.lower() in text:
                return True

        return False

    def _extract_key_phrases(self, text: str) -> List[str]:
        """Extract key phrases from text for fuzzy matching"""
        phrases = []

        # Extract noun phrases and important words
        words = text.split()
        for i in range(len(words)):
            for j in range(i + 1, min(i + 4, len(words) + 1)):
                phrase = " ".join(words[i:j])
                if len(phrase) > 3:  # Ignore very short phrases
                    phrases.append(phrase)

        return phrases

    def get_facts_by_type(self, fact_type: str) -> List[Fact]:
        """Get all facts of a specific type"""
        return [f for f in self.extracted_facts if f.fact_type == fact_type]

    def get_critical_facts(self, threshold: float = 0.9) -> List[Fact]:
        """Get facts marked as critical (high importance score)"""
        return [f for f in self.extracted_facts if f.importance_score >= threshold]
