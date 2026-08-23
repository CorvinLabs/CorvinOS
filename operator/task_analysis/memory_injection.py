"""Phase 3b: Memory Context Injection — Auto-link ADRs, incidents, memory files.

Automatically enriches task context by linking related:
- ADRs (architectural decisions)
- Incidents (past issues, resolved patterns)
- Memory files (operator notes, learnings)

Prevents injection of sensitive data (secrets, PII).

ADR: ADR-0270 (Memory Context Injection)
"""

import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import json

logger = logging.getLogger(__name__)

# Security: patterns that indicate sensitive data (prevent injection)
SECRET_PATTERNS = [
    r"api[_-]?key\s*[=:]\s*['\"]?[a-z0-9]{20,}",  # API keys
    r"password\s*[=:]\s*['\"][^'\"]+['\"]",  # Passwords
    r"token\s*[=:]\s*['\"]?[a-z0-9._\-]+",  # Tokens
    r"secret\s*[=:]",  # Generic secrets
    r"aws_access_key|aws_secret",  # AWS
    r"gcp[_-]key|google[_-]api",  # GCP
    r"azure[_-]key|client[_-]secret",  # Azure
]


def contains_secret(text: str) -> bool:
    """Check if text contains apparent secrets.

    Args:
        text: Text to scan

    Returns:
        True if suspicious patterns found, False otherwise
    """
    if not text:
        return False

    text_lower = text.lower()
    for pattern in SECRET_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            logger.warning(f"Blocked injection: potential secret detected ({pattern[:30]}...)")
            return True

    return False


@dataclass
class MemoryLink:
    """Link to a related memory resource."""

    resource_type: str
    """Type: 'adr', 'incident', 'memory', 'memory_file'."""

    resource_id: str
    """ID or path (e.g., 'ADR-0260', 'incident#123', 'adr-0260.md')."""

    title: str
    """Human-readable title."""

    relevance: float
    """Relevance score (0.0-1.0), higher = more relevant."""

    content_preview: str
    """First 200 chars of content (for display)."""

    safe_to_inject: bool = True
    """False if contains secrets (will be excluded)."""


class MemoryLinker:
    """Link tasks to related memory resources."""

    def __init__(self, repo_root: Optional[Path] = None, memory_root: Optional[Path] = None):
        """Initialize memory linker.

        Args:
            repo_root: Path to CorvinOS repo (default: infer)
            memory_root: Path to memory directory (default: ~/.claude/projects/CorvinOS/memory)
        """
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]  # CorvinOS/
        self.repo_root = repo_root

        if memory_root is None:
            # Try multiple locations
            default_memory = Path.home() / ".claude" / "projects" / "CorvinOS" / "memory"
            if default_memory.exists():
                memory_root = default_memory
            else:
                memory_root = repo_root.parent / "memory"

        self.memory_root = memory_root
        self.adr_root = repo_root.parent / "Corvin-ADR" / "decisions"

        logger.info(f"MemoryLinker initialized: repo={self.repo_root}, memory={self.memory_root}")

    def find_related_adrs(self, task_description: str, affected_layers: List[str]) -> List[MemoryLink]:
        """Find ADRs related to task's affected layers.

        Args:
            task_description: Task description (for keyword matching)
            affected_layers: Affected layers (e.g., ['L10', 'L16'])

        Returns:
            List of MemoryLink objects, sorted by relevance (descending)
        """
        if not self.adr_root.exists():
            logger.warning(f"ADR root not found: {self.adr_root}")
            return []

        links = []
        keywords = set(task_description.lower().split())

        for adr_file in self.adr_root.glob("*.md"):
            try:
                content = adr_file.read_text()
                if not content:
                    continue

                # Parse frontmatter for affected_layers
                relevance = 0.0

                # Check layer mentions
                for layer in affected_layers:
                    if f'"{layer}"' in content or f"'{layer}'" in content or layer in content:
                        relevance = max(relevance, 0.8)

                # Check keyword matches
                keywords_found = sum(1 for kw in keywords if kw in content.lower())
                if keywords_found > 0:
                    relevance = max(relevance, min(0.5 + keywords_found * 0.1, 0.9))

                if relevance > 0.0:
                    # Extract title (first # line)
                    title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
                    title = title_match.group(1) if title_match else adr_file.stem

                    # Preview
                    preview = content[:200].replace("\n", " ")

                    link = MemoryLink(
                        resource_type="adr",
                        resource_id=adr_file.stem,
                        title=title,
                        relevance=relevance,
                        content_preview=preview,
                        safe_to_inject=not contains_secret(content),
                    )
                    links.append(link)
            except Exception as e:
                logger.warning(f"Failed to parse ADR {adr_file}: {e}")

        # Sort by relevance (descending)
        links.sort(key=lambda x: x.relevance, reverse=True)
        return links

    def find_related_memory(self, task_description: str) -> List[MemoryLink]:
        """Find memory files related to task.

        Args:
            task_description: Task description

        Returns:
            List of MemoryLink objects (top 5 by relevance)
        """
        if not self.memory_root.exists():
            logger.warning(f"Memory root not found: {self.memory_root}")
            return []

        links = []
        keywords = set(task_description.lower().split())

        for memory_file in self.memory_root.glob("*.md"):
            try:
                content = memory_file.read_text()
                if not content:
                    continue

                # Skip MEMORY.md index
                if memory_file.name == "MEMORY.md":
                    continue

                # Keyword matching
                keywords_found = sum(1 for kw in keywords if kw in content.lower())
                if keywords_found == 0:
                    continue

                relevance = min(keywords_found * 0.1, 0.8)

                # Extract title
                title_match = re.search(r"^---\nname:\s*(.+?)$", content, re.MULTILINE)
                if not title_match:
                    title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
                title = title_match.group(1).strip() if title_match else memory_file.stem

                # Preview
                preview = content[:200].replace("\n", " ")

                link = MemoryLink(
                    resource_type="memory",
                    resource_id=memory_file.stem,
                    title=title,
                    relevance=relevance,
                    content_preview=preview,
                    safe_to_inject=not contains_secret(content),
                )
                links.append(link)
            except Exception as e:
                logger.warning(f"Failed to parse memory {memory_file}: {e}")

        # Sort by relevance and trim to top 5
        links.sort(key=lambda x: x.relevance, reverse=True)
        return links[:5]

    def inject_context(
        self,
        task_description: str,
        affected_layers: List[str] = None,
        max_links: int = 5,
    ) -> Dict:
        """Gather and inject memory context for a task.

        Args:
            task_description: Task description
            affected_layers: Affected layers (optional)
            max_links: Max # of links to include (token budget)

        Returns:
            Dict with injected context:
            - memory_links: List of safe MemoryLink objects
            - total_links: Total found before filtering
            - unsafe_links_skipped: # of links skipped (contained secrets)
            - injection_metadata: For audit trail
        """
        if affected_layers is None:
            affected_layers = []

        adr_links = self.find_related_adrs(task_description, affected_layers)
        memory_links = self.find_related_memory(task_description)

        # Combine and sort by relevance
        all_links = adr_links + memory_links
        all_links.sort(key=lambda x: x.relevance, reverse=True)

        # Filter unsafe (containing secrets)
        safe_links = [link for link in all_links if link.safe_to_inject]
        unsafe_count = len(all_links) - len(safe_links)

        if unsafe_count > 0:
            logger.warning(f"Skipped {unsafe_count} links containing secrets")

        # Trim to budget
        injected_links = safe_links[:max_links]

        return {
            "memory_links": injected_links,
            "total_links_found": len(all_links),
            "unsafe_links_skipped": unsafe_count,
            "injection_metadata": {
                "timestamp": str(Path.ctime(Path.cwd())),
                "links_injected": len(injected_links),
                "max_relevance": max([l.relevance for l in injected_links]) if injected_links else 0.0,
            },
        }

    def format_for_system_prompt(self, links: List[MemoryLink]) -> str:
        """Format memory links for system prompt injection (Tier 2 memory).

        Args:
            links: List of MemoryLink objects

        Returns:
            Formatted string for system prompt
        """
        if not links:
            return ""

        lines = ["## Related Context (from Memory):\n"]
        for i, link in enumerate(links, 1):
            lines.append(f"{i}. **{link.resource_type.upper()}**: {link.title} ({link.relevance:.2f})")
            lines.append(f"   ID: {link.resource_id}")
            lines.append(f"   Preview: {link.content_preview[:100]}...")
            lines.append("")

        return "\n".join(lines)
