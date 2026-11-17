"""The bridge-side facade: everything the adapter calls (PLAN-0973 section 5.2).

* :func:`context_block` - the framed block for a private turn of a bound owner, or "".
* :func:`after_turn` - automatic capture after the answer (owner text only).
* :func:`handle_command` - the ``/me`` slash commands.
* :func:`chat_is_owned` - lets the legacy L28.2 distiller stop for an owned chat.

Every function is fail-safe for the turn: an error returns "" / does nothing and never raises into
the adapter. Personal features exist only where the daemon reported ``chat_type == "private"``.
"""
from __future__ import annotations

import contextlib
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

from . import audit, capture, decoder, erasure, model, owner, render, session
from .store import PersonalStore

ERASE_PHRASE = "ERASE MY PERSONAL SPACE"
CONSENT_TEXT = ("Your personal space stays on this computer, encrypted. Corvin will add facts you state about "
                "yourself automatically (language, tone, format, role, tools and similar settings) and keep your "
                "ideas. You can review, change or forget every entry, export everything, or erase the space at any "
                "time. To agree, send: /personal on yes")


@dataclass
class CommandReply:
    handled: bool
    reply: str = ""


_STORES: dict[tuple[str, str], PersonalStore] = {}
_STORES_LOCK = threading.Lock()


def _open(tenant_id: str, channel: str, uid: str) -> PersonalStore | None:
    """The owner's store, kept per owner while the device unlock is cached, reloaded when another
    process changed it; dropped with the cached keys."""
    oid = owner.resolve_owner(tenant_id, channel=channel, uid=uid)
    if oid is None:
        return None
    keys = session.get_bridge(tenant_id, oid)
    if keys is None:
        keys = owner.unlock_with_device(tenant_id, oid, surface=channel)
        session.put_bridge(tenant_id, oid, keys)
    with _STORES_LOCK:
        s = _STORES.get((tenant_id, oid))
    if s is not None and s.keys.epoch == keys.epoch:
        try:
            s.reload_if_changed()
            return s
        except Exception:  # noqa: BLE001 - reopen below
            pass
    s = PersonalStore.open(tenant_id, oid, keys)
    with _STORES_LOCK:
        for k in [k for k in _STORES if session.get_bridge(*k) is None]:
            _STORES.pop(k, None)
        _STORES[(tenant_id, oid)] = s
    return s


def context_block(tenant_id: str, *, channel: str, uid: str, chat_type: str, task_text: str) -> str:
    if chat_type != "private" or not uid:
        return ""
    try:
        store = _open(tenant_id, channel, uid)
        if store is None:
            return ""
        return render.block(store, task_text, surface=channel)
    except Exception:  # noqa: BLE001 - a personal-KB failure never breaks the turn
        return ""


def after_turn(tenant_id: str, *, channel: str, uid: str, chat_type: str, user_text: str,
               attachments: int = 0, forwarded: bool = False, refused: bool = False,
               extractor: Callable[[str], dict | None] | None = None) -> capture.CaptureResult | None:
    if chat_type != "private" or not uid:
        return None
    try:
        store = _open(tenant_id, channel, uid)
        if store is None:
            return None
        return capture.capture_turn(store, user_text, attachments=attachments, forwarded=forwarded,
                                    refused=refused, extractor=extractor)
    except Exception:  # noqa: BLE001
        return None


def note_alias(tenant_id: str, *, channel: str, uid: str, alias: str) -> None:
    """Record the alias (Teams UPN) of a bound identity so an erasure request by that name finds the space."""
    try:
        oid = owner.resolve_owner(tenant_id, channel=channel, uid=uid)
        if oid and alias and alias != uid:
            owner.add_alias(tenant_id, channel=channel, alias=alias, owner_id=oid, bound_uid=uid)
    except Exception:  # noqa: BLE001
        pass


def block_owner_hash(tenant_id: str, *, channel: str, uid: str) -> str | None:
    """The audit owner hash of the space a block was rendered from (for a withheld record later on)."""
    try:
        oid = owner.resolve_owner(tenant_id, channel=channel, uid=uid)
        return audit.owner_hash(oid) if oid else None
    except Exception:  # noqa: BLE001
        return None


def note_withheld(tenant_id: str, owner_hash: str, surface: str) -> None:
    """A rendered block removed before the spawn (provider not cleared): the audit chain says so."""
    try:
        audit.emit("personal_kb.context_withheld", tenant_id=tenant_id, owner_hash=owner_hash,
                   entry_count=0, surface=surface)
    except Exception:  # noqa: BLE001
        pass


def chat_is_owned(tenant_id: str, *, channel: str, uid: str) -> bool:
    try:
        return owner.resolve_owner(tenant_id, channel=channel, uid=uid) is not None
    except Exception:  # noqa: BLE001
        return False


def _find(store: PersonalStore, ref: str) -> dict[str, Any] | None:
    ref = ref.strip()
    for e in store.entries():
        if e["uid"] == ref or (e.get("display_id") or "").lower() == ref.lower():
            return e
    return None


# The same explicit whitespace set as corvin_operator/bridges/shared/adapter.py (_PKB_WS_LIST): folded to a plain space
# before the command is parsed, so a separator the adapter accepted is never a silent no-op here.
_WS_FOLD = re.compile("[" + "".join(
    [*[chr(c) for c in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680)],
     *[chr(c) for c in range(0x2000, 0x200B)], chr(0x2028), chr(0x2029), chr(0x202F), chr(0x205F), chr(0x3000),
     chr(0xFEFF)]) + "]")


_CMD = re.compile(r"^/me(?:\s+(\S+))?(?:\s+([\s\S]*))?$", re.I)


def is_command(text: str) -> bool:
    return bool(re.match(r"^/me(\s|$)", (text or "").strip(), re.I))


def handle_command(tenant_id: str, text: str, *, channel: str, uid: str, display: str, chat_type: str,
                   chat_key: str = "", generate: decoder.Generate | None = None) -> CommandReply:
    m = _CMD.match(_WS_FOLD.sub(" ", text or "").strip())
    if not m:
        return CommandReply(False)
    sub = (m.group(1) or "help").lower()
    arg = (m.group(2) or "").strip()
    if chat_type != "private":
        if sub == "link" and arg:
            with contextlib.suppress(owner.OwnerError):
                owner.redeem_pair_code(tenant_id, arg, channel=channel, uid=uid, display=display,
                                       chat_type=chat_type)
            return CommandReply(True, "Use a private chat for /personal. A link code sent here is no longer valid.")
        return CommandReply(True, "Use a private chat with me for /personal commands.")
    try:
        return CommandReply(True, _dispatch(tenant_id, sub, arg, channel=channel, uid=uid, display=display,
                                            chat_key=chat_key, generate=generate))
    except (owner.OwnerError, decoder.DecoderError, model.ValidationError) as exc:
        return CommandReply(True, str(exc))
    except Exception:  # noqa: BLE001
        return CommandReply(True, "That did not work. Your personal space is unchanged.")


def _dispatch(tenant_id: str, sub: str, arg: str, *, channel: str, uid: str, display: str, chat_key: str,
              generate: decoder.Generate | None) -> str:
    if sub == "help":
        return ("/personal on - create your personal space · /personal status · /personal remember <text> · "
                "/personal idea <text> · /personal unfold <IDEA-0001> · /personal forget <id> · /personal auto on|off · "
                "/personal link <code> · /personal unlink · /personal erase  (/me works too, except in Discord and Slack; "
                "in Slack, start with a space if /personal is not set up as a command)")
    if sub == "on":
        if owner.resolve_owner(tenant_id, channel=channel, uid=uid):
            return "Your personal space already exists."
        if arg.lower() != "yes":
            return CONSENT_TEXT
        created = owner.create_bridge_owner(tenant_id, channel=channel, uid=uid, display=display, chat_type="private")
        if chat_key:
            with contextlib.suppress(Exception):
                from . import migrate_user_model  # noqa: PLC0415
                migrate_user_model.migrate(PersonalStore.open(tenant_id, created.owner_id, created.keys),
                                           channel=channel, chat_key=chat_key)
        return ("Your personal space is ready. It has no passphrase and no recovery code: if this computer loses "
                "its key, the space is gone. It stays a chat-only space; a space you can also open in the console "
                "has to be created there first and then linked to this chat.")
    if sub == "link":
        owner.redeem_pair_code(tenant_id, arg, channel=channel, uid=uid, display=display, chat_type="private",
                               chat_key=chat_key)
        return "Almost done: confirm the link in the console under Knowledge Graph, Only for me."
    store = _open(tenant_id, channel, uid)
    if store is None:
        return "You have no personal space yet. Send /personal on to create one."
    if sub == "status":
        counts: dict[str, int] = {}
        for e in store.entries():
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        pending = len(store.list(review="pending"))
        return (f"Entries: {sum(counts.values())} ({', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or 'none'})."
                f" Waiting for review: {pending}. Automatic learning: {'on' if store.meta.get('auto_capture') else 'off'}.")
    if sub == "remember":
        if not arg:
            return "Tell me what to remember: /me remember <text>"
        e = store.put(model.new_entity("context", arg[:model.MAX_TITLE], origin="user"), manual=True)
        return f"Remembered as {e.get('display_id')}."
    if sub == "idea":
        if not arg:
            return "Describe the idea: /me idea <text>"
        e = store.put(model.new_entity("idea", arg[:model.MAX_TITLE], origin="user"), manual=True)
        return f"Saved as {e.get('display_id')}. Send /personal unfold {e.get('display_id')} to shape it step by step."
    if sub == "unfold":
        e = _find(store, arg)
        if e is None or e["type"] != "idea":
            return "No such idea."
        r = decoder.unfold(store, e["uid"], generate=generate)
        state = "done" if r.gate_ok else "needs work: " + "; ".join(r.failures)
        # Objections are advice ranked by the critic, not issues to count (it always names three; ADR-2329 item 5).
        return (f"{e.get('display_id')}: stage {r.stage} {state}. Open issues: {len(r.failures) + len(r.duplicates)}; "
                f"critic remarks: {len(r.objections)}, the first one matters most. Review it in the console.")
    if sub == "forget":
        e = _find(store, arg)
        if e is None:
            return "No such entry."
        fp = f"{e.get('key')} {e['title']}" if e.get("key") else e["title"]
        store.forget(e["uid"], fingerprint=fp)
        capture.note_owner_action(store, e, "forget")
        from . import consistency  # noqa: PLC0415
        with contextlib.suppress(Exception):   # prune the decoder's pointers to what was forgotten (ADR-2329)
            consistency.run_sweep(store)
        return f"Forgot {e.get('display_id') or 'the entry'}."
    if sub == "auto":
        on = arg.lower() == "on"
        if arg.lower() not in ("on", "off"):
            return "Use /personal auto on or /personal auto off."
        store.update_meta(lambda m: m.__setitem__("auto_capture", on))
        return f"Automatic learning is {'on' if on else 'off'}."
    if sub == "unlink":
        info = owner.owner_info(tenant_id, store.owner_id)
        if not info["has_passphrase"] and len(store.meta.get("bindings", [])) <= 1:
            return ("This chat is the only way into your space. Unlinking it would lock you out; to remove the space, "
                    f"send /personal erase {ERASE_PHRASE}")
        owner.unbind(tenant_id, store, channel, uid)
        session.drop_owner(tenant_id, store.owner_id)
        return "This chat is no longer linked to your personal space."
    if sub == "erase":
        if owner.owner_info(tenant_id, store.owner_id)["has_passphrase"]:
            return "Your space has a passphrase: erase it in the console under Knowledge Graph, Only for me, Settings."
        if arg != ERASE_PHRASE:
            return f"This deletes everything for good. To confirm, send: /personal erase {ERASE_PHRASE}"
        n = erasure.erase_owner(tenant_id, store.owner_id)
        return f"Your personal space is erased ({n} entries)."
    return "Unknown command. Send /personal help."
