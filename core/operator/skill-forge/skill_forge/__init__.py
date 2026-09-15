"""SkillForge — runtime generation of Markdown skills (knowledge, not code).

Sister plugin to forge: forge generates executable tools (sandboxed code),
SkillForge generates Skill markdown files (prompt-injected knowledge). Shares
the four-scope mechanic (task/session/project/user) and the hash-chain
audit log with forge.
"""

from . import session_cleanup

__all__ = ["session_cleanup"]
