"""Round 3 of the 2026-11-19 review - L44 house rules.

R3-SEC-2: the over-window cap was CHUNK_CHARS * MAX_CHUNKS (192 000) while the
overlapping chunks cover step * (MAX_CHUNKS - 1) + CHUNK_CHARS (162 000), so a
task of 162 001..192 000 chars cleared with its tail never read.

R3-SEC-8: an injected classifier's confidence that is a bool, negative, above
1.0 or non-finite was accepted as a probability.

Every R3-SEC-2 test drives the REAL chunker (``_house_rules_classifier``); only
the provider call below it (``_house_rules_classify_with_chain``) is a stub that
records what it was shown.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

MARK = "ZZTAILPAYLOADZZ"


@pytest.fixture()
def real_chunker(monkeypatch):
    """A repo-policy gate whose classifier is the real chunker; returns
    ``(gate, seen_chunks, events)``."""
    import house_rules as H

    assert str(_REPO) in str(Path(H.__file__).resolve()), H.__file__
    seen: list = []
    events: list = []

    def fake_chain(chunk, rules_block, auth_str, **_kw):
        seen.append(chunk)
        return ("", 0.97, "")

    monkeypatch.setattr(H, "_house_rules_classify_with_chain", fake_chain)
    monkeypatch.setattr(H, "_house_rules_resolve_order", lambda tid=None: "cloud_only")
    H._house_rules_verdict_cache.clear()
    gate = H.HouseRulesGate.from_repo(
        audit_writer=lambda e, s, d: events.append((e, dict(d))),
        classifier=lambda t, r, a: H._house_rules_classifier(t, r, a, tenant_id="_default"))
    yield gate, seen, events
    H._house_rules_verdict_cache.clear()


def _text(total: int) -> str:
    pad = ("lorem ipsum dolor sit amet " * (total // 27 + 1))[: total - len(MARK)]
    return pad + MARK


def test_window_constant_is_what_the_chunks_cover():
    import house_rules as H

    step = H._HOUSE_RULES_CHUNK_CHARS - H._HOUSE_RULES_CHUNK_OVERLAP
    assert H._HOUSE_RULES_CHUNK_STEP == step
    assert H._HOUSE_RULES_WINDOW == step * (H._HOUSE_RULES_MAX_CHUNKS - 1) + H._HOUSE_RULES_CHUNK_CHARS
    assert H._HOUSE_RULES_WINDOW == 162000
    assert H._HOUSE_RULES_WINDOW < H._HOUSE_RULES_CHUNK_CHARS * H._HOUSE_RULES_MAX_CHUNKS


@pytest.mark.parametrize("total", [1, 11999, 12000, 12001, 22000, 22001, 150000,
                                   161999, 162000])
def test_chunks_cover_every_char_up_to_the_window(total):
    import house_rules as H

    text = "".join(chr(0x4E00 + (i % 5000)) for i in range(total))
    chunks = H._house_rules_chunks(text)
    assert 1 <= len(chunks) <= H._HOUSE_RULES_MAX_CHUNKS
    assert all(len(c) <= H._HOUSE_RULES_CHUNK_CHARS for c in chunks)
    # Rebuild the text from the windows: every char is in one of them, in order.
    pos, rebuilt = 0, ""
    for i, c in enumerate(chunks):
        start = i * H._HOUSE_RULES_CHUNK_STEP
        assert text[start:start + len(c)] == c
        rebuilt += c[pos - start:]
        pos = start + len(c)
    assert rebuilt == text
    # ...and the seam overlap is kept.
    for a, b in zip(chunks, chunks[1:]):
        assert a[-H._HOUSE_RULES_CHUNK_OVERLAP:] == b[:H._HOUSE_RULES_CHUNK_OVERLAP][:len(a[-H._HOUSE_RULES_CHUNK_OVERLAP:])] \
            or len(b) < H._HOUSE_RULES_CHUNK_OVERLAP


@pytest.mark.parametrize("total", [150000, 161999, 162000])
def test_marker_in_the_last_char_is_read_when_the_task_is_allowed(real_chunker, total):
    gate, seen, events = real_chunker
    text = _text(total)
    assert len(text) == total and text.endswith(MARK)
    d = gate.classify(text, channel="t", chat_key="c")
    assert d.action == "allow" and d.reason == "classifier_cleared"
    assert any(MARK in c for c in seen), "the tail of an ALLOWED task was never read"
    assert events[-1][0] == "house_rules.allowed"


@pytest.mark.parametrize("total", [162001, 170000, 191980, 191990, 192000, 192001, 400000])
def test_task_over_the_window_escalates_never_clears(real_chunker, total):
    import house_rules as H

    gate, seen, events = real_chunker
    text = _text(total)
    d = gate.classify(text, channel="t", chat_key="c")
    assert not d.allowed
    assert d.action == "escalate" and d.reason == H._REASON_OVER_CLASSIFIER_CAP
    assert events[-1][0] == "house_rules.escalated"
    assert events[-1][1].get("reason") == "prompt_over_classifier_cap"
    assert len(seen) <= H._HOUSE_RULES_MAX_CHUNKS


def test_no_length_exists_that_is_allowed_with_an_unread_tail(real_chunker):
    """Sweep the old gap and both borders: allowed implies the marker was read."""
    import house_rules as H

    gate, seen, _events = real_chunker
    for total in list(range(161990, 162012)) + list(range(162000, 192002, 1873)):
        seen.clear()
        H._house_rules_verdict_cache.clear()
        d = gate.classify(_text(total), channel="t", chat_key="c")
        read = any(MARK in c for c in seen)
        assert (not d.allowed) or read, (total, d.reason)
        assert d.allowed == (total <= H._HOUSE_RULES_WINDOW), (total, d.reason)


def test_violation_in_the_last_window_is_found(monkeypatch):
    import house_rules as H

    def chain(chunk, rules_block, auth_str, **_kw):
        return ("no-malware", 0.99, "hit") if MARK in chunk else ("", 0.97, "")

    monkeypatch.setattr(H, "_house_rules_classify_with_chain", chain)
    monkeypatch.setattr(H, "_house_rules_resolve_order", lambda tid=None: "cloud_only")
    H._house_rules_verdict_cache.clear()
    rid, conf, _ = H._house_rules_classifier(_text(H._HOUSE_RULES_WINDOW), [], {},
                                              tenant_id="_default")
    assert rid == "no-malware" and conf == 0.99
    H._house_rules_verdict_cache.clear()


def test_check_l44_refuses_a_task_in_the_former_gap(real_chunker, monkeypatch, tmp_path):
    import house_rules as H
    import spawn_gates

    gate, _seen, _events = real_chunker
    monkeypatch.setattr(H.HouseRulesGate, "from_repo", classmethod(lambda cls, **_kw: gate))
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    out = spawn_gates.check_l44(_text(175000), "_default", corvin_home=tmp_path)
    assert isinstance(out, str) and "too long to be safety-checked" in out


# ── R3-SEC-8 ────────────────────────────────────────────────────────────────

def _events_gate(conf):
    import house_rules as H

    events: list = []
    gate = H.HouseRulesGate.from_repo(
        audit_writer=lambda e, s, d: events.append((e, dict(d))),
        classifier=lambda task, rules, auth: ("", conf, "stub"))
    return gate, events


@pytest.mark.parametrize("conf", [True, False, -0.01, -1, 1.0001, 2, 97, float("nan"),
                                  float("inf"), float("-inf"), None, "0.9", "high",
                                  [0.9], {"c": 1}])
def test_injected_anomalous_confidence_is_a_classifier_error(conf):
    gate, events = _events_gate(conf)
    d = gate.classify("please summarise the meeting notes", channel="t", chat_key="c")
    assert not d.allowed
    assert d.action == "escalate" and d.reason == "classifier_error"
    assert d.confidence == 0.0
    assert events[-1][0] == "house_rules.escalated"
    assert events[-1][1].get("reason") == "classifier_error"


@pytest.mark.parametrize("conf", [0.0, 0.5, 0.97, 1.0, 1])
def test_injected_in_range_confidence_still_decides(conf):
    gate, events = _events_gate(conf)
    d = gate.classify("please summarise the meeting notes", channel="t", chat_key="c")
    assert d.allowed and events[-1][0] == "house_rules.allowed"
    assert d.reason != "classifier_error"


@pytest.mark.parametrize("conf", [True, -0.2, 1.5])
def test_anomalous_confidence_never_applies_a_named_violation_as_confident(conf):
    """With a rule named the verdict blocks either way; it must be the anomaly
    code, not a 'confident' deny built on a non-probability."""
    import house_rules as H

    gate0, _ = _events_gate(0.9)
    rid = gate0.policy.rules[0].id
    events: list = []
    gate = H.HouseRulesGate.from_repo(
        audit_writer=lambda e, s, d: events.append((e, dict(d))),
        classifier=lambda task, rules, auth: (rid, conf, "stub"))
    d = gate.classify("please summarise the meeting notes", channel="t", chat_key="c")
    assert not d.allowed and d.reason == "classifier_error"


@pytest.mark.parametrize("raw", ['{"violated_rule_id": "", "confidence": true}',
                                 '{"violated_rule_id": "", "confidence": -0.5}',
                                 '{"violated_rule_id": "", "confidence": 1.5}',
                                 '{"violated_rule_id": "", "confidence": 95}'])
def test_parser_rejects_out_of_range_confidence_as_bad_json(raw):
    import json

    import house_rules as H

    with pytest.raises(H._HouseRulesClassifierError) as ei:
        H._house_rules_verdict_confidence(json.loads(raw))
    assert ei.value.cause == "bad_json"


def test_parser_accepts_the_range_borders():
    import house_rules as H

    assert H._house_rules_verdict_confidence({"confidence": 0}) == 0.0
    assert H._house_rules_verdict_confidence({"confidence": 1}) == 1.0
    assert H._house_rules_verdict_confidence({"confidence": 0.42}) == 0.42
