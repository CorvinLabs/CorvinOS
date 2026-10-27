#!/usr/bin/env python3
"""Deterministic corpus generator for the token A/B benchmark (stdlib only, no network).

Every task has a deterministic oracle (exact answer or hidden unit tests), so no LLM judge is involved.
Same seed + same profile => byte-identical JSONL (the SHA-256 is pinned in corpus/MANIFEST.json).

Usage:
    gen_corpus.py [--profile smoke|full] [--out PATH] [--check]
"""
import argparse
import hashlib
import itertools
import json
import random
import sys
from pathlib import Path

SEED = 20261027
PROFILES = {"smoke": 12, "full": 48}  # tasks per category
CATEGORIES = ["extract", "transform", "arith", "logic", "code"]
LABEL = {"extract": "mechanical", "transform": "mechanical", "arith": "reasoning", "logic": "reasoning", "code": "reasoning"}
HERE = Path(__file__).resolve().parent
ANSWER_RULE = "Finish with a final line of the exact form `ANSWER: <value>` and nothing after it."

NAMES = ["Alma", "Boris", "Chen", "Dara", "Emil", "Fiona", "Goran", "Hana", "Ivo", "Jana", "Kofi", "Lena", "Mika", "Nora",
         "Omar", "Pia", "Quinn", "Rosa", "Sven", "Tara", "Uwe", "Vera", "Wim", "Xenia", "Yara", "Zeno"]
DEPTS = ["Sales", "Legal", "Ops", "Research", "Support"]
CITIES = ["Lyon", "Porto", "Graz", "Turin", "Gent", "Bern", "Riga", "Split"]


def gen_extract(rng, i):
    people = rng.sample(NAMES, 8)
    rows = [(n, rng.choice(DEPTS), rng.choice(CITIES), rng.randrange(30, 91) * 1000) for n in people]
    who = rng.choice(people)
    field, col = rng.choice([("city", 2), ("department", 1), ("salary", 3)])
    row = next(r for r in rows if r[0] == who)
    table = "name,department,city,salary\n" + "\n".join(f"{n},{d},{c},{s}" for n, d, c, s in rows)
    prompt = (f"Here is a table:\n{table}\n\nWhat is the {field} of {who}? {ANSWER_RULE}")
    kind = "int" if field == "salary" else "text"
    return dict(prompt=prompt, answer_kind=kind, expected=str(row[col]), format_regex=r"^\d+$" if kind == "int" else r"^\S.*$")


def gen_transform(rng, i):
    sub = ("sort", "date", "count")[i % 3]
    if sub == "sort":
        xs = [rng.randrange(-50, 100) for _ in range(8)]
        return dict(prompt=f"Sort these integers in ascending order: {xs}. {ANSWER_RULE} Give them comma-separated without spaces.",
                    answer_kind="text", expected=",".join(str(x) for x in sorted(xs)), format_regex=r"^-?\d+(,-?\d+)*$")
    if sub == "date":
        y, m, d = rng.randrange(1990, 2031), rng.randrange(1, 13), rng.randrange(1, 29)
        return dict(prompt=f"Convert the ISO date {y:04d}-{m:02d}-{d:02d} to the format DD.MM.YYYY. {ANSWER_RULE}",
                    answer_kind="text", expected=f"{d:02d}.{m:02d}.{y:04d}", format_regex=r"^\d{2}\.\d{2}\.\d{4}$")
    xs = [rng.randrange(0, 100) for _ in range(15)]
    t = rng.randrange(20, 80)
    return dict(prompt=f"How many of these numbers are strictly greater than {t}? Numbers: {xs}. {ANSWER_RULE}",
                answer_kind="int", expected=str(sum(1 for x in xs if x > t)), format_regex=r"^\d+$")


def gen_arith(rng, i):
    t = i % 3
    for _ in range(1000):
        if t == 0:
            b, a, d = rng.randrange(3, 13), rng.randrange(4, 30), rng.randrange(2, 10)
            total = a * b
            c = rng.randrange(1, total)
            rem = total - c
            if rem % d == 0 and rem // d > 0:
                return dict(prompt=(f"A warehouse holds {a} crates with {b} units each. {c} units are shipped out, then the remaining "
                                    f"units are split equally into {d} bins. How many units are in each bin? {ANSWER_RULE}"),
                            answer_kind="int", expected=str(rem // d), format_regex=r"^\d+$")
        elif t == 1:
            p, x, y = rng.randrange(1, 30) * 20, rng.choice([10, 20, 25, 30, 40, 50]), rng.randrange(1, 40)
            val = p * (100 - x)
            if val % 100 == 0:
                return dict(prompt=(f"An item costs {p} euros. It is discounted by {x} percent, then a flat fee of {y} euros is added. "
                                    f"What is the final price in euros? {ANSWER_RULE}"),
                            answer_kind="int", expected=str(val // 100 + y), format_regex=r"^\d+$")
        else:
            s, r_, w = rng.randrange(20, 80), rng.randrange(2, 6), rng.randrange(2, 8)
            return dict(prompt=(f"A tank holds {s} liters. Each hour {r_ * 3} liters flow in and {r_} liters flow out. "
                                f"After {w} hours, how many liters are in the tank? {ANSWER_RULE}"),
                        answer_kind="int", expected=str(s + (r_ * 3 - r_) * w), format_regex=r"^\d+$")
    raise RuntimeError("arith generator exhausted")


def _holds(order, cons):
    pos = {n: k for k, n in enumerate(order)}
    for kind, a, b in cons:
        if kind == "left" and not pos[a] < pos[b]:
            return False
        if kind == "adjacent" and abs(pos[a] - pos[b]) != 1:
            return False
        if kind == "at" and pos[a] != b:
            return False
        if kind == "notend" and pos[a] in (0, len(order) - 1):
            return False
    return True


def _text(c):
    kind, a, b = c
    if kind == "left":
        return f"{a} stands somewhere to the left of {b}."
    if kind == "adjacent":
        return f"{a} stands directly next to {b}."
    if kind == "at":
        return f"{a} stands at position {b + 1} (counting from 1 at the left)."
    return f"{a} does not stand at either end."


def gen_logic(rng, i):
    for _ in range(2000):
        people = rng.sample(NAMES, 4)
        truth = people[:]
        rng.shuffle(truth)
        pool = []
        for a, b in itertools.permutations(people, 2):
            pool.append(("left", a, b))
            pool.append(("adjacent", a, b))
        for a in people:
            pool.append(("notend", a, None))
            for p in range(4):
                pool.append(("at", a, p))
        pool = [c for c in pool if _holds(truth, [c])]
        rng.shuffle(pool)
        cons = []
        for c in pool:
            cons.append(c)
            n = sum(1 for perm in itertools.permutations(people) if _holds(perm, cons))
            if n == 1:
                k = rng.randrange(4)
                text = " ".join(_text(c) for c in cons)
                return dict(prompt=(f"Four people stand in a row: {', '.join(sorted(people))}. {text} Who stands at position {k + 1}? {ANSWER_RULE}"),
                            answer_kind="text", expected=truth[k], format_regex=r"^[A-Za-z]+$")
            if len(cons) > 7:
                break
    raise RuntimeError("logic generator exhausted")


CODE_SPECS = [
    ("the sum of the squares of the even numbers in the list", lambda xs: sum(x * x for x in xs if x % 2 == 0), "ints"),
    ("how many elements are strictly greater than {t}", lambda xs, t: sum(1 for x in xs if x > t), "ints_t"),
    ("the length of the longest run of equal consecutive elements (0 for an empty list)",
     lambda xs: max((len(list(g)) for _, g in itertools.groupby(xs)), default=0), "ints"),
    ("the second largest distinct value, or None if there are fewer than two distinct values",
     lambda xs: (sorted(set(xs))[-2] if len(set(xs)) >= 2 else None), "ints"),
    ("the number of vowels (a, e, i, o, u, case-insensitive) in the string", lambda s: sum(1 for ch in s.lower() if ch in "aeiou"), "str"),
    ("True if the string is a palindrome when ignoring case and non-alphanumeric characters, else False",
     lambda s: (lambda t: t == t[::-1])([ch.lower() for ch in s if ch.isalnum()]), "str"),
    ("the list rotated to the right by {k} positions", lambda xs, k: (xs[-(k % len(xs)):] + xs[:-(k % len(xs))] if xs and k % len(xs) else list(xs)), "ints_k"),
    ("the sum of the decimal digits of all non-negative integers in the list", lambda xs: sum(int(ch) for x in xs for ch in str(abs(x))), "ints"),
    ("True if the parentheses in the string are balanced (only the characters ( and ) matter), else False",
     lambda s: (lambda r: r)(_balanced(s)), "parens"),
    ("the run-length encoding of the string as letter followed by count, for example aaab gives a3b1 (empty string gives empty string)",
     lambda s: "".join(f"{k}{len(list(g))}" for k, g in itertools.groupby(s)), "runs"),
    ("the flattened list of a list of lists (one level only, order preserved)", lambda xss: [x for xs in xss for x in xs], "nested"),
    ("the number of index pairs i<j with xs[i]+xs[j] equal to {t}", lambda xs, t: sum(1 for a in range(len(xs)) for b in range(a + 1, len(xs)) if xs[a] + xs[b] == t), "ints_t"),
]


def _balanced(s):
    depth = 0
    for ch in s:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def _inputs(rng, kind, n):
    out = []
    for _ in range(n):
        if kind in ("ints", "ints_t", "ints_k"):
            xs = [rng.randrange(-9, 30) for _ in range(rng.randrange(0, 9))]
            out.append(xs)
        elif kind == "str":
            out.append("".join(rng.choice("abcAEIOU xyz,.") for _ in range(rng.randrange(0, 12))))
        elif kind == "parens":
            out.append("".join(rng.choice("()ab") for _ in range(rng.randrange(0, 10))))
        elif kind == "runs":
            out.append("".join(rng.choice("aab") for _ in range(rng.randrange(0, 10))))
        else:
            out.append([[rng.randrange(0, 9) for _ in range(rng.randrange(0, 4))] for _ in range(rng.randrange(0, 4))])
    return out


def gen_code(rng, i):
    desc, ref, kind = CODE_SPECS[i % len(CODE_SPECS)]
    t = rng.randrange(1, 15)
    k = rng.randrange(1, 6)
    extra = kind in ("ints_t", "ints_k")
    desc = desc.format(t=t, k=k)
    tests = []
    for x in _inputs(rng, kind, 20):
        args = [x, t] if kind == "ints_t" else [x, k] if kind == "ints_k" else [x]
        tests.append([args, json.loads(json.dumps(ref(*args)))])
    sig = "f(xs, " + ("t" if kind == "ints_t" else "k") + ")" if extra else "f(x)"
    ex = "\n".join(f"  f({', '.join(json.dumps(a) for a in args)}) == {json.dumps(exp)}" for args, exp in tests[:3])
    prompt = (f"Write a Python function `{sig}` that returns {desc}. Examples:\n{ex}\n"
              "Reply with one ```python code block that defines only the function (and helpers if needed) and nothing else.")
    return dict(prompt=prompt, answer_kind="code", expected="", tests=tests, visible_n=3,
                reference_code=f"# reference generated from CODE_SPECS[{i % len(CODE_SPECS)}] (lambda in gen_corpus.py)")


GENS = {"extract": gen_extract, "transform": gen_transform, "arith": gen_arith, "logic": gen_logic, "code": gen_code}


def build(profile):
    n = PROFILES[profile]
    tasks = []
    for cat in CATEGORIES:
        rng = random.Random(f"{SEED}|{profile}|{cat}")
        for i in range(n):
            t = GENS[cat](rng, i)
            t.update(task_id=f"{cat}-{i:03d}", category=cat, label=LABEL[cat])
            tasks.append(t)
    return tasks


def dumps(tasks):
    return "".join(json.dumps(t, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n" for t in tasks)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true", help="compare against corpus/MANIFEST.json instead of writing")
    a = ap.parse_args(argv)
    text = dumps(build(a.profile))
    digest = sha256(text)
    man_path = HERE / "corpus" / "MANIFEST.json"
    if a.check:
        man = json.loads(man_path.read_text(encoding="utf-8"))
        want = man["profiles"][a.profile]["sha256"]
        print(f"{a.profile}: {digest} {'OK' if digest == want else 'MISMATCH (expected ' + want + ')'}")
        return 0 if digest == want else 1
    out = Path(a.out) if a.out else HERE / "corpus" / f"tasks_{a.profile}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    man = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {"seed": SEED, "profiles": {}}
    man["seed"] = SEED
    man["profiles"][a.profile] = {"file": out.name, "tasks": len(text.splitlines()), "per_category": PROFILES[a.profile], "sha256": digest}
    man_path.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{a.profile}: {len(text.splitlines())} tasks, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
