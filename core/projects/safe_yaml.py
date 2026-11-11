"""YAML for text the console did not write: aliases are refused (REVIEW-0066 M1).

``yaml.safe_load`` builds an alias as a shared reference, which is cheap - but every consumer that copies the value (``json.dumps``,
a deep copy, a template render) expands it, and nine aliases per level grow the result 9x per level: 469 bytes of frontmatter became
593 MB. A project file, a KB document's frontmatter or an imported ``project.yaml`` never needs an alias, so the loader refuses the
first one (and with it every ``<<`` merge key) before any node is built. The size of the input is the caller's limit.
"""
from __future__ import annotations

from typing import Any

import yaml


class AliasRefused(yaml.YAMLError):
    """The text uses a YAML alias (``*name``) or a merge key built on one."""


class _NoAliasLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            raise AliasRefused("YAML aliases are not accepted here")
        return super().compose_node(parent, index)


def load(text: str) -> Any:
    """``yaml.safe_load`` without aliases. Raises :class:`AliasRefused` (a ``yaml.YAMLError``) on the first alias."""
    return yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader subclass
