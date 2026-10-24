"""WP-01 / W-9 — reverse last-N reader for seam-linked audit chains.

``last_chain_records`` returns the newest records of a chain and its seam-linked
history, merged by record ts, with the caps applied AFTER the merge. It is for
last-N views only; aggregates (totals) must keep using the forward readers.

Fixtures are raw JSON lines with explicit ``ts`` values, so the ordering under
test is the one written, not the wall clock. Every scenario runs in a fresh
interpreter with its own sandboxed ``CORVIN_HOME`` / ``XDG_CONFIG_HOME`` /
``HOME`` / ``CORVIN_AUDIT_ANCHOR_KEY`` (an absolute path): ``security_events``
caches the anchor key per process, and the tests must never touch a live install.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FORGE = REPO / "corvin_operator" / "forge"
CONSOLE = REPO / "core" / "console"

HELPERS = textwrap.dedent("""
    import hashlib, json, sys
    from pathlib import Path
    from forge import security_events as se

    SEAM = "audit.chain_supersedes"

    def rec(ts, marker):
        return {"event_type": "tool.created", "ts": ts,
                "details": {"tool": marker}}

    def write(path, recs):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\\n")

    def seam(canon, hist, ts):
        key = hashlib.sha256(str(hist.resolve()).encode("utf-8")).hexdigest()[:32]
        write(canon, [{"event_type": SEAM, "ts": ts,
                       "details": {"superseded_key": key}}])

    def build(canon, canon_recs, hist_specs):
        # hist_specs: [(file name, records)], each linked from the canonical
        # chain by a seam. Seams go first with ts 0, so the canonical file stays
        # ts-monotone and the seams are the oldest lines (they only reach a
        # view with a large cap, and markers() filters them out anyway).
        for name, recs in hist_specs:
            hist = canon.parent / "recovered" / name
            write(hist, recs)
            seam(canon, hist, 0)
        write(canon, canon_recs)
        return canon

    def markers(recs):
        return [r["details"]["tool"] for r in recs
                if r.get("event_type") == "tool.created"]
""")


def _run(tmp_path: Path, body: str, *, helpers: bool = True) -> dict:
    script = (HELPERS if helpers else "") + textwrap.dedent(body)
    home = tmp_path / "home"
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["HOME"] = str(tmp_path / "userhome")
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(tmp_path / "keys" / "audit_anchor.key")
    env["CORVIN_TENANT_ID"] = "_default"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(FORGE), str(REPO), str(CONSOLE), env.get("PYTHONPATH", "")]
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env, capture_output=True, text=True, timeout=180, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# Shared layout: canonical ts 100/200/300, one history file with ts 150/250.
PRELUDE = """
    root = Path(sys.argv[1])
    canon = root / "home" / "global" / "forge" / "audit.jsonl"
"""


def test_order_across_files_is_ts_descending(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    build(canon,
          [rec(100, "c100"), rec(200, "c200"), rec(300, "c300")],
          [("audit.legacy.jsonl", [rec(150, "h150"), rec(250, "h250")])])
    out = {"order": markers(se.last_chain_records(canon, max_records=100))}
    print(json.dumps(out))
    """)
    assert out["order"] == ["c300", "h250", "c200", "h150", "c100"]


def test_history_file_newer_than_canonical_tail_is_included(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    # The legacy file keeps receiving appends: its newest record (ts 600) is
    # newer than anything in the canonical file.
    build(canon,
          [rec(100, "c100"), rec(200, "c200"), rec(300, "c300")],
          [("audit.legacy.jsonl", [rec(150, "h150"), rec(500, "h500"), rec(600, "h600")])])
    out = {"top2": markers(se.last_chain_records(canon, max_records=2)),
           "all": markers(se.last_chain_records(canon, max_records=100))}
    print(json.dumps(out))
    """)
    assert out["top2"] == ["h600", "h500"]
    assert out["all"] == ["h600", "h500", "c300", "c200", "h150", "c100"]


def test_cap_is_applied_after_the_merge(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    build(canon,
          [rec(100, "c100"), rec(200, "c200"), rec(300, "c300")],
          [("audit.a.jsonl", [rec(150, "a150"), rec(250, "a250")]),
           ("audit.b.jsonl", [rec(180, "b180"), rec(900, "b900")])])
    out = {}
    for n in (1, 2, 3, 4):
        out[str(n)] = markers(se.last_chain_records(canon, max_records=n))
    # byte cap: a prefix of the merged order, never a skip-ahead
    full = se.last_chain_records(canon, max_records=100)
    one = len(json.dumps(full[0]).encode("utf-8")) + 1
    out["bytes"] = markers(se.last_chain_records(canon, max_records=100, max_bytes=one * 2))
    out["zero"] = se.last_chain_records(canon, max_records=0)
    print(json.dumps(out))
    """)
    assert out["1"] == ["b900"]
    assert out["2"] == ["b900", "c300"]
    assert out["3"] == ["b900", "c300", "a250"]
    assert out["4"] == ["b900", "c300", "a250", "c200"]
    assert out["bytes"] == ["b900", "c300"]
    assert out["zero"] == []


def test_torn_and_corrupt_lines_are_skipped(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    build(canon,
          [rec(100, "c100"), rec(200, "c200")],
          [("audit.legacy.jsonl", [rec(150, "h150")])])
    # a corrupt line in the middle of the history file, and a torn final line
    # without a newline in the canonical file (a write still in progress)
    hist = canon.parent / "recovered" / "audit.legacy.jsonl"
    with hist.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\\n")
        fh.write(json.dumps(rec(160, "h160")) + "\\n")
    with canon.open("a", encoding="utf-8") as fh:
        fh.write('{"torn": ')
    out = {"all": markers(se.last_chain_records(canon, max_records=100))}
    print(json.dumps(out))
    """)
    assert out["all"] == ["c200", "h160", "h150", "c100"]


def test_empty_and_missing_files_are_fine(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    build(canon, [rec(100, "c100")], [("audit.empty.jsonl", [])])
    out = {"empty_hist": markers(se.last_chain_records(canon, max_records=10)),
           "missing_canon": se.last_chain_records(root / "nope" / "audit.jsonl",
                                                  max_records=10),
           "empty_canon": None}
    empty = root / "empty" / "audit.jsonl"
    empty.parent.mkdir(parents=True)
    empty.touch()
    out["empty_canon"] = se.last_chain_records(empty, max_records=10)
    print(json.dumps(out))
    """)
    assert out["empty_hist"] == ["c100"]
    assert out["missing_canon"] == []
    assert out["empty_canon"] == []


def test_multi_chunk_files_and_a_line_spanning_chunks(tmp_path):
    # Files far larger than the 64 KiB read chunk, interleaved ts across the
    # canonical file and a history file, and one line of ~200 KiB that must be
    # reassembled across several chunks. Compared with a forward full read.
    out = _run(tmp_path, PRELUDE + """
    pad = "x" * 200
    canon_recs = [rec(2 * i + 1, f"c{2 * i + 1}") for i in range(2000)]
    canon_recs[1800]["details"]["pad"] = "y" * (200 * 1024)  # ts 3601, inside the window
    hist_recs = [rec(2 * i, f"h{2 * i}") for i in range(2000)]
    for r in hist_recs[::2]:
        r["details"]["pad"] = pad
    build(canon, canon_recs, [("audit.legacy.jsonl", hist_recs)])
    newest = se.last_chain_records(canon, max_records=1500, max_bytes=None)
    forward = [r for r in se.iter_chain_records(canon) if r.get("event_type") == "tool.created"]
    # The forward read is file-by-file (history, then canonical), so the
    # reference order is an explicit sort on ts, newest first.
    expect = sorted(forward, key=lambda r: r["ts"], reverse=True)[:1500]
    out = {"markers": markers(newest),
           "expect": markers(expect),
           "same_records": newest == expect,
           "big_intact": sum(1 for r in newest if len(r["details"].get("pad", "")) == 200 * 1024),
           "total": len(forward)}
    print(json.dumps(out))
    """)
    assert out["total"] == 4000
    assert out["markers"] == out["expect"]
    assert out["same_records"] is True  # whole records, not just markers, match the forward read
    assert out["big_intact"] == 1
    assert out["markers"][0] == "c3999" and out["markers"][-1] == "h2500"


def test_forwarder_matches_core_for_a_fixture_chain(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    from core.paths import chain_history as ch
    build(canon,
          [rec(100, "c100"), rec(200, "c200"), rec(300, "c300")],
          [("audit.legacy.jsonl", [rec(150, "h150"), rec(500, "h500")]),
           ("audit.other.jsonl", [rec(50, "o50")])])
    # a second chain in the same directory must not leak into the first
    other_canon = root / "home" / "global" / "audit.jsonl"
    build(other_canon, [rec(10, "t10"), rec(20, "t20")], [])
    out = {}
    for n in (1, 3, 10):
        core_v = se.last_chain_records(canon, max_records=n)
        fwd_v = ch.last_chain_records(canon, max_records=n)
        out[str(n)] = [core_v == fwd_v, markers(fwd_v)]
    out["other"] = markers(ch.last_chain_records(other_canon, max_records=10))
    print(json.dumps(out))
    """)
    assert out["1"] == [True, ["h500"]]
    assert out["3"] == [True, ["h500", "c300", "c200"]]
    assert out["10"] == [True, ["h500", "c300", "c200", "h150", "c100", "o50"]]
    assert out["other"] == ["t20", "t10"]


def test_forwarder_degraded_path_without_forge(tmp_path):
    # forge made unimportable: ``sys.modules["forge"] = None`` makes every
    # ``from forge import ...`` raise ImportError, so the forwarder must fall
    # back to the canonical file alone and still honour both caps.
    out = _run(tmp_path, """
    import hashlib, json, sys
    sys.modules["forge"] = None
    from pathlib import Path
    from core.paths import chain_history as ch

    root = Path(sys.argv[1])
    canon = root / "home" / "global" / "forge" / "audit.jsonl"
    hist = canon.parent / "recovered" / "audit.legacy.jsonl"
    hist.parent.mkdir(parents=True)

    def rec(ts, m):
        return {"event_type": "tool.created", "ts": ts, "details": {"tool": m}}

    hist.write_text(json.dumps(rec(900, "h900")) + "\\n")
    key = hashlib.sha256(str(hist.resolve()).encode("utf-8")).hexdigest()[:32]
    with canon.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_type": "audit.chain_supersedes", "ts": 0,
                             "details": {"superseded_key": key}}) + "\\n")
        for i in range(1, 6):
            fh.write(json.dumps(rec(i * 100, f"c{i * 100}")) + "\\n")
            if i == 3:
                fh.write("not json at all\\n")
        fh.write('{"torn": ')

    def markers(recs):
        return [r["details"]["tool"] for r in recs if r.get("event_type") == "tool.created"]

    import logging
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    logging.getLogger().addHandler(Grab())

    one = len(json.dumps(rec(500, "c500")).encode("utf-8")) + 1
    out = {
        "degraded": ch._security_events() is None,
        "bytes_one": markers(ch.last_chain_records(canon, max_records=10, max_bytes=1)),
        "scan_tail": markers(ch.last_chain_records(canon, max_records=10,
                                                   max_scan_bytes=one * 2 + 10)),
        "n3": markers(ch.last_chain_records(canon, max_records=3)),
        "bytes": markers(ch.last_chain_records(canon, max_records=10, max_bytes=2 * one)),
        "bytes_tight": markers(ch.last_chain_records(canon, max_records=10,
                                                     max_bytes=2 * one - 1)),
        "no_byte_cap": markers(ch.last_chain_records(canon, max_records=10, max_bytes=None)),
        "zero": ch.last_chain_records(canon, max_records=0),
        "missing": ch.last_chain_records(root / "nope" / "audit.jsonl", max_records=3),
    }
    out["warnings"] = [m for m in seen if "degraded" in m]
    print(json.dumps(out))
    """, helpers=False)
    # one warning per process, however many calls took the degraded path
    assert len(out["warnings"]) == 1, out["warnings"]
    # a record larger than max_bytes is still returned: [] only means "nothing"
    assert out["bytes_one"] == ["c500"]
    # max_scan_bytes bounds the read from the end: the torn tail (9 bytes) plus
    # two whole lines fit; the line cut at the boundary is dropped, not parsed
    assert out["scan_tail"] == ["c500", "c400"]
    assert out["degraded"] is True
    assert out["n3"] == ["c500", "c400", "c300"]
    assert out["bytes"] == ["c500", "c400"]        # same byte accounting as the core
    assert out["bytes_tight"] == ["c500"]          # a prefix, never a skip-ahead
    assert out["no_byte_cap"] == ["c500", "c400", "c300", "c200", "c100"]  # history ignored
    assert out["zero"] == [] and out["missing"] == []


# ── review round 2: out-of-order records within a file ─────────────────────
# write_event stamps ts before it takes the chain lock, so file order is not
# strictly ts order; a record may also carry no ts. Each file stream must stay
# newest-first by its merge key, or heapq.merge stalls it behind the others.

def _untimed(marker):
    return '{"event_type": "tool.created", "details": {"tool": "%s"}}' % marker


def test_untimed_record_in_the_middle_does_not_stall_its_file(tmp_path):
    # reviewer reproduction 1: canonical 100,200,300,<no ts>,400,500 + history 10,20,30
    out = _run(tmp_path, PRELUDE + """
    build(canon, [], [("audit.legacy.jsonl", [rec(10, "h10"), rec(20, "h20"), rec(30, "h30")])])
    with canon.open("a", encoding="utf-8") as fh:
        for r in (rec(100, "c100"), rec(200, "c200"), rec(300, "c300")):
            fh.write(json.dumps(r) + "\\n")
        fh.write(%r + "\\n")
        for r in (rec(400, "c400"), rec(500, "c500")):
            fh.write(json.dumps(r) + "\\n")
    out = {"top5": markers(se.last_chain_records(canon, max_records=5)),
           "all": markers(se.last_chain_records(canon, max_records=100))}
    print(json.dumps(out))
    """ % _untimed("cNOTS"))
    # the untimed record takes the key of the record read before it (c400)
    assert out["top5"] == ["c500", "c400", "cNOTS", "c300", "c200"]
    assert out["all"] == ["c500", "c400", "cNOTS", "c300", "c200", "c100",
                          "h30", "h20", "h10"]


def test_isolated_low_ts_record_does_not_bury_older_records(tmp_path):
    # reviewer reproduction 2: canonical 100,200,300,5,400 + history 150,250,350
    out = _run(tmp_path, PRELUDE + """
    build(canon,
          [rec(100, "c100"), rec(200, "c200"), rec(300, "c300"), rec(5, "c5"), rec(400, "c400")],
          [("audit.legacy.jsonl", [rec(150, "h150"), rec(250, "h250"), rec(350, "h350")])])
    out = {"top4": markers(se.last_chain_records(canon, max_records=4)),
           "all": markers(se.last_chain_records(canon, max_records=100))}
    print(json.dumps(out))
    """)
    assert out["top4"] == ["c400", "h350", "c300", "h250"]
    assert out["all"] == ["c400", "h350", "c300", "h250", "c200", "h150", "c100", "c5"]


def test_backward_clock_step(tmp_path):
    # The clock steps back by 350 s after c400: the next records carry 50/60/70
    # although they were written later. Within the reorder window the file is
    # emitted in ts order; beyond it the running minimum clamps the key, so the
    # stream stays monotone and nothing is lost or stalled.
    out = _run(tmp_path, PRELUDE + """
    build(canon,
          [rec(100, "c100"), rec(200, "c200"), rec(300, "c300"), rec(400, "c400"),
           rec(50, "c50"), rec(60, "c60"), rec(70, "c70")],
          [("audit.legacy.jsonl", [rec(65, "h65")])])
    out = {"default": markers(se.last_chain_records(canon, max_records=100))}
    se._REORDER_WINDOW = 2
    out["window2"] = markers(se.last_chain_records(canon, max_records=100))
    print(json.dumps(out))
    """)
    assert out["default"] == ["c400", "c300", "c200", "c100", "c70", "h65", "c60", "c50"]
    # window 2: c70 leads; c400..c100 are clamped to key 70 (running minimum),
    # still in file order; h65, then the sunk c60/c50
    assert out["window2"] == ["c70", "c400", "c300", "c200", "c100", "h65", "c60", "c50"]


def test_local_inversions_merge_to_the_exact_global_order(tmp_path):
    # Three files, unique ts, each with seeded local swaps (displacement <= 5
    # lines, inside the reorder window): the merge must equal a global sort.
    out = _run(tmp_path, PRELUDE + """
    import random
    rnd = random.Random(20261024)
    def jittered(prefix, offset):
        recs = [rec(offset + 3 * i, f"{prefix}{offset + 3 * i}") for i in range(2000)]
        for _ in range(600):
            i = rnd.randrange(len(recs) - 5)
            j = i + rnd.randrange(1, 6)
            recs[i], recs[j] = recs[j], recs[i]
        return recs
    canon_recs = jittered("c", 1000)
    a, b = jittered("a", 1001), jittered("b", 1002)
    build(canon, canon_recs, [("audit.a.jsonl", a), ("audit.b.jsonl", b)])
    got = markers(se.last_chain_records(canon, max_records=10000, max_bytes=None))
    want = markers(sorted(canon_recs + a + b, key=lambda r: r["ts"], reverse=True))
    top = markers(se.last_chain_records(canon, max_records=50))
    out = {"equal": got == want, "n": len(got), "top_equal": top == want[:50]}
    print(json.dumps(out))
    """)
    assert out == {"equal": True, "n": 6000, "top_equal": True}


def test_byte_cap_never_returns_empty_when_a_record_exists(tmp_path):
    out = _run(tmp_path, PRELUDE + """
    build(canon, [rec(100, "c100"), rec(200, "c200")], [])
    out = {"one_byte": markers(se.last_chain_records(canon, max_records=10, max_bytes=1)),
           "zero_bytes": markers(se.last_chain_records(canon, max_records=10, max_bytes=0))}
    print(json.dumps(out))
    """)
    assert out["one_byte"] == ["c200"]
    assert out["zero_bytes"] == ["c200"]


def test_max_scan_bytes_bounds_a_needle_filtered_read(tmp_path):
    # Needle-filtered lines are not counted by max_records, so without a scan
    # limit a rare needle reads the whole file. max_scan_bytes bounds it.
    out = _run(tmp_path, PRELUDE + """
    old = {"event_type": "rare.event", "ts": 1, "details": {"tool": "rare"}}
    build(canon, [old] + [rec(10 + i, f"c{i}") for i in range(5000)], [])
    size = canon.stat().st_size
    needle = ('"rare.event"',)
    out = {"unbounded": [r["event_type"] for r in se.last_chain_records(
               canon, max_records=1, needles=needle)],
           "bounded": se.last_chain_records(canon, max_records=1, needles=needle,
                                            max_scan_bytes=64 * 1024),
           "bounded_full": [r["event_type"] for r in se.last_chain_records(
               canon, max_records=1, needles=needle, max_scan_bytes=size)],
           "size": size}
    print(json.dumps(out))
    """)
    assert out["size"] > 64 * 1024
    assert out["unbounded"] == ["rare.event"]
    assert out["bounded"] == []
    assert out["bounded_full"] == ["rare.event"]


def test_short_read_after_the_file_shrank_stops_cleanly(tmp_path):
    # A file truncated under the reader returns fewer bytes than requested.
    # The reader must stop, not join a short block to the carried tail and
    # yield a line that never existed.
    out = _run(tmp_path, PRELUDE + """
    lines = [json.dumps(rec(i, "c%06d" % i) | {"pad": "x" * 200}) for i in range(3000)]
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text("".join(l + "\\n" for l in lines))
    it = se._iter_lines_newest_first(canon)
    first = next(it)
    with canon.open("r+b") as fh:
        fh.truncate(100 * 1024)
    rest = list(it)
    # A glued line can even be valid JSON (two same-shaped records cut at the
    # right byte), so the check is membership in what was actually written.
    written = {l.encode("utf-8") for l in lines}
    bad = [raw[:80].decode("utf-8", "replace") for raw in [first] + rest
           if raw.strip() and raw not in written]
    out = {"bad": bad, "yielded": 1 + len(rest)}
    print(json.dumps(out))
    """)
    assert out["bad"] == [], out
