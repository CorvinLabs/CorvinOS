#!/usr/bin/env python3
"""The additive detector (ADR-2268 Decision 6, T-0220): measured on a labelled DE/EN corpus.

The corpus (``tests/fixtures/sensitive_detector_corpus.jsonl``) is SELF-WRITTEN and synthetic:
documentation IBANs, test cards, example domains. The recall below is therefore a regression
floor on this corpus, not a claim about real traffic. It deliberately includes values the
detector cannot see (spelled-out or spaced digits, a tax id with no context word) and one look-alike
it flags wrongly, labelled ``known_miss`` / ``known_false_positive``. The test pins those sets
EXACTLY, so an improvement or a regression shows up as a changed set instead of a silent number.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import sensitive_detector as sd  # type: ignore  # noqa: E402
from data_classification import DataClassification as C  # type: ignore  # noqa: E402

CORPUS = [json.loads(line) for line in
          (HERE / "tests" / "fixtures" / "sensitive_detector_corpus.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()]
CATEGORIES = ("email", "phone", "iban", "payment_card", "tax_id_de", "ssn_us", "api_key")
KNOWN_CATEGORY_NAMES = set(CATEGORIES)


def _hit(row) -> bool:
    cats = sd.detect(row["text"])
    return row["category"] in cats if row["sensitive"] else bool(cats)


def test_the_corpus_is_balanced_enough_to_mean_something():
    by_cat = defaultdict(int)
    for r in CORPUS:
        if r["sensitive"]:
            by_cat[r["category"]] += 1
    assert set(by_cat) == set(CATEGORIES)
    assert all(n >= 2 for n in by_cat.values()), dict(by_cat)
    assert {r["lang"] for r in CORPUS} == {"de", "en"}
    assert sum(1 for r in CORPUS if not r["sensitive"]) >= 20
    assert len({r["id"] for r in CORPUS}) == len(CORPUS), "duplicate corpus id"


def test_recall_per_category_and_the_exact_set_of_known_misses():
    recall = {}
    missed = set()
    for cat in CATEGORIES:
        pos = [r for r in CORPUS if r["sensitive"] and r["category"] == cat]
        got = [r for r in pos if _hit(r)]
        recall[cat] = (len(got), len(pos))
        missed |= {r["id"] for r in pos if not _hit(r)}
    print("\nrecall per category:", {k: f"{a}/{b}" for k, (a, b) in recall.items()})
    declared = {r["id"] for r in CORPUS if r.get("known_miss")}
    assert missed == declared, (
        f"unexpected misses: {sorted(missed - declared)}; declared but now detected: {sorted(declared - missed)}")
    # Every category finds at least all of its plain (non-obfuscated) positives.
    for cat, (hit, total) in recall.items():
        plain = sum(1 for r in CORPUS if r["sensitive"] and r["category"] == cat and not r.get("known_miss"))
        assert hit == plain, (cat, hit, plain)


def test_false_positives_are_exactly_the_declared_look_alikes():
    flagged = {r["id"] for r in CORPUS if not r["sensitive"] and sd.detect(r["text"])}
    declared = {r["id"] for r in CORPUS if r.get("known_false_positive")}
    assert flagged == declared, (sorted(flagged - declared), sorted(declared - flagged))


def test_detect_names_categories_only_and_never_the_matched_text():
    for r in CORPUS:
        cats = sd.detect(r["text"])
        assert set(cats) <= KNOWN_CATEGORY_NAMES, (r["id"], cats)
        assert cats == tuple(sorted(set(cats)))


def test_credentials_are_secret_and_personal_data_is_confidential():
    assert sd.detected_class("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789") is C.SECRET
    assert sd.detected_class("mail jane.doe@example.com") is C.CONFIDENTIAL
    assert sd.detected_class("a key AKIAIOSFODNN7EXAMPLE and a mail jane.doe@example.com") is C.SECRET
    assert sd.detected_class("nothing to see here") is None


@pytest.mark.parametrize("current", list(C))
@pytest.mark.parametrize("text", ["plain text", "mail jane.doe@example.com",
                                  "key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", ""])
def test_the_detector_can_only_raise(current, text):
    out = sd.raise_class(current, text)
    assert out >= current


def test_raise_class_examples():
    assert sd.raise_class(C.PUBLIC, "mail jane.doe@example.com") is C.CONFIDENTIAL
    assert sd.raise_class(C.INTERNAL, "mail jane.doe@example.com") is C.CONFIDENTIAL
    assert sd.raise_class(C.SECRET, "mail jane.doe@example.com") is C.SECRET
    assert sd.raise_class(C.CONFIDENTIAL, "mail jane.doe@example.com") is C.CONFIDENTIAL
    assert sd.raise_class(C.CONFIDENTIAL, "sk-proj-AbCdEfGh12345678IjKlMnOp") is C.SECRET
    assert sd.raise_class(C.PUBLIC, "harmless") is C.PUBLIC


def test_a_class_marker_in_the_text_cannot_switch_the_detector_off():
    assert sd.raise_class(C.PUBLIC, "[class:public] mail jane.doe@example.com") is C.CONFIDENTIAL


@pytest.mark.parametrize("bad", [None, 5, b"bytes", ["x"]])
def test_a_non_string_is_a_detector_error_not_public(bad):
    with pytest.raises(sd.DetectorError):
        sd.detect(bad)  # type: ignore[arg-type]
    with pytest.raises(sd.DetectorError):
        sd.raise_class(C.PUBLIC, bad)  # type: ignore[arg-type]


def test_an_internal_failure_is_fail_closed(monkeypatch):
    class Boom:
        def search(self, *_a, **_k):
            raise RuntimeError("regex engine failure")
        finditer = search

    monkeypatch.setattr(sd, "_KEY_RES", (Boom(),))
    with pytest.raises(sd.DetectorError):
        sd.detect("any text")
    with pytest.raises(sd.DetectorError):
        sd.raise_class(C.PUBLIC, "any text")


@pytest.mark.parametrize("hostile", [
    "0 " * 100_000, "a@" * 50_000, "9" * 100_000, "DE89 " * 20_000, "+49 " * 30_000,
    "(" * 50_000, "0171/" * 30_000, "1-" * 60_000,
])
def test_hostile_input_finishes_quickly(hostile):
    start = time.monotonic()
    sd.detect(hostile)
    assert time.monotonic() - start < 5.0, "detector is slow on adversarial input (ReDoS?)"
