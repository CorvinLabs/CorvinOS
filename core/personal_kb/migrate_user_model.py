"""One-time import of a legacy L28.2 user-model file (ADR-2327 item 7).

Only for a chat that a confirmed pairing bound to a personal space and that was private when the
code was redeemed. The legacy model was distilled from user AND assistant text, so its items pass
the special-category, secret and unsafe-value filters of any capture plus two text filters standing in
for the extractor's flags (another person named or referred to, a hedged statement), and arrive as ``note`` entries (never injected), ``origin=derived``,
``review=pending``, linked ``derived_from legacy:user_model``, expiring after 30 days unless accepted.
The legacy file is then deleted and distillation stops for that chat. Any other legacy file is untouched.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import audit, model
from .capture import _ART9, DERIVED_TTL_S, _is_secret

#: A legacy item has no ``about_other_person`` / ``uncertain`` flag: these stand in for them (fail-closed).
_OTHER_PERSON = re.compile(
    r"(?i)\b(?:he|she|him|her|his|hers|they|them|their|my\s+(?:wife|husband|partner|girlfriend|boyfriend|mother|"
    r"father|mum|mom|dad|son|daughter|child|children|kids?|brother|sister|friend|colleague|boss|manager|team)|"
    r"er|sie|ihm|ihr|ihre|ihren|sein|seine|meine?[nrs]?\s+(?:frau|mann|partner(?:in)?|freund(?:in)?|mutter|vater|"
    r"sohn|tochter|kinder?|bruder|schwester|kolleg(?:e|in)|chef(?:in)?))\b")
_HEDGED = re.compile(r"(?i)\b(?:maybe|perhaps|probably|might|possibly|seems?|vielleicht|wahrscheinlich|eventuell|"
                     r"vermutlich|scheint|wohl)\b")
from .store import PersonalStore


def _user_model_module():
    shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.append(str(shared))
    import user_model  # type: ignore[import-not-found]
    return user_model


def legacy_path(channel: str, chat_key: str, tenant_id: str) -> Path:
    return _user_model_module()._user_model_path(channel, chat_key, tenant_id)


def migrate(store: PersonalStore, *, channel: str, chat_key: str, now: float | None = None) -> int:
    ts = time.time() if now is None else now
    try:
        um = _user_model_module()
        path = um._user_model_path(channel, chat_key, store.tenant_id)
    except Exception:  # noqa: BLE001
        return 0
    if not path.is_file():
        return 0
    try:
        spec = (json.loads(path.read_text(encoding="utf-8")) or {}).get("spec") or {}
    except (OSError, ValueError):
        return 0
    items: list[str] = []
    if isinstance(spec.get("communication_style"), str) and spec["communication_style"].strip():
        items.append("Communication style: " + spec["communication_style"].strip())
    for field in ("preferences", "recurring_topics", "goals", "patterns", "do_not_assume"):
        for v in spec.get(field) or []:
            if isinstance(v, str) and v.strip():
                items.append(f"{field.replace('_', ' ').capitalize()}: {v.strip()}")
    n = 0
    for text in items[:60]:
        if (_ART9.search(text) or _is_secret(text) or model.unsafe_value(text) or _OTHER_PERSON.search(text)
                or _HEDGED.search(text)):
            continue
        ent = model.new_entity("note", text[:model.MAX_TITLE], origin="derived", review="pending",
                               links=[{"rel": "derived_from", "to": "legacy:user_model"}], labels=["legacy"],
                               expires_at=ts + DERIVED_TTL_S, now=ts)
        store.put(ent, now=ts)
        n += 1
    audit.emit("personal_kb.migrated", tenant_id=store.tenant_id, owner_hash=store.owner_hash, count=n)
    um.forget(channel, chat_key, tenant_id=store.tenant_id)
    return n
