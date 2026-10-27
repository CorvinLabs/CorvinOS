"""Harder synthetic tasks for the token A/B benchmark (profile "hard"). Stdlib only, no network.

Why they exist: on the 15-task smoke corpus Opus, Sonnet and Haiku all solved every task (pilot of 2026-10-27, success 1.000
on all three), so a non-inferiority result there says nothing. These tasks are built to be harder for a small model: long
tables to aggregate, constraint puzzles with six people, multi-step arithmetic with distractors, chains of text rules, and
code tasks with real edge cases. Every task keeps a deterministic oracle (exact answer or hidden unit tests).

Every generator takes (rng, i) and returns the task fields except task_id, category and label (added by gen_corpus.build).
The reference functions (REFS) are plain Python and are unit-tested by hand-computed cases in test_token_ab_bench.py.
"""
import datetime
import heapq
import itertools
import json
import random  # noqa: F401  (rng objects come from the caller)

ANSWER_RULE = "Finish with a final line of the exact form `ANSWER: <value>` and nothing after it."
NAMES = ["Alma", "Boris", "Chen", "Dara", "Emil", "Fiona", "Goran", "Hana", "Ivo", "Jana", "Kofi", "Lena", "Mika", "Nora",
         "Omar", "Pia", "Quinn", "Rosa", "Sven", "Tara", "Uwe", "Vera", "Wim", "Xenia", "Yara", "Zeno"]
DEPTS = ["Sales", "Legal", "Ops", "Research", "Support"]
CITIES = ["Lyon", "Porto", "Graz", "Turin", "Gent", "Bern", "Riga", "Split"]
WORDS = ["amber", "bridge", "candle", "dragon", "engine", "forest", "garden", "harbor", "island", "jungle", "kettle", "lantern",
         "meadow", "needle", "orchard", "pepper", "quartz", "river", "saddle", "timber", "umbrella", "valley", "window", "yellow"]


# ------------------------------------------------------------------------------------------------ table_agg

def gen_table_agg(rng, i):
    n = rng.randrange(60, 81)
    ids = rng.sample(range(100, 1000), n)
    salaries = rng.sample(range(30000, 120000, 100), n)  # distinct, so a "highest" is never a tie
    rows = [(f"E{ids[k]}", rng.choice(DEPTS), rng.choice(CITIES), rng.randrange(2005, 2026), salaries[k],
             rng.choice(["active", "active", "leave", "left"])) for k in range(n)]
    table = "id,dept,city,hire_year,salary,status\n" + "\n".join(",".join(str(c) for c in r) for r in rows)
    t = i % 3
    if t == 0:
        d, y = rng.choice(DEPTS), rng.randrange(2010, 2022)
        q = f"What is the total salary of all rows with dept = {d}, hire_year >= {y} and status = active?"
        ans = str(sum(r[4] for r in rows if r[1] == d and r[3] >= y and r[5] == "active"))
        kind, rx = "int", r"^\d+$"
    elif t == 1:
        d, s = rng.choice(DEPTS), rng.randrange(40, 90) * 1000
        per_city = {}
        for r in rows:
            if r[1] == d and r[4] > s:
                per_city[r[2]] = per_city.get(r[2], 0) + 1
        q = f"In how many distinct cities are there at least 2 rows with dept = {d} and salary > {s}?"
        ans, kind, rx = str(sum(1 for v in per_city.values() if v >= 2)), "int", r"^\d+$"
    else:
        city = rng.choice([r[2] for r in rows if r[5] == "active"])
        best = max((r for r in rows if r[2] == city and r[5] == "active"), key=lambda r: r[4])
        q = f"Among the rows with status = active and city = {city}, which id has the highest salary?"
        ans, kind, rx = best[0], "text", r"^E\d+$"
    return dict(prompt=f"Here is a table with {n} rows:\n{table}\n\n{q} {ANSWER_RULE}", answer_kind=kind, expected=ans, format_regex=rx)


# ------------------------------------------------------------------------------------------------ logic6

def _holds6(order, cons):
    pos = {p: k for k, p in enumerate(order)}
    for kind, a, b in cons:
        if kind == "left" and not pos[a] < pos[b]:
            return False
        if kind == "adjacent" and abs(pos[a] - pos[b]) != 1:
            return False
        if kind == "at" and pos[a] != b:
            return False
        if kind == "notend" and pos[a] in (0, len(order) - 1):
            return False
        if kind == "between" and not (min(pos[b[0]], pos[b[1]]) < pos[a] < max(pos[b[0]], pos[b[1]])):
            return False
    return True


def _text6(c):
    kind, a, b = c
    if kind == "left":
        return f"{a} stands somewhere to the left of {b}."
    if kind == "adjacent":
        return f"{a} stands directly next to {b}."
    if kind == "at":
        return f"{a} stands at position {b + 1} (counting from 1 at the left)."
    if kind == "between":
        return f"{a} stands somewhere between {b[0]} and {b[1]}."
    return f"{a} does not stand at either end."


def gen_logic6(rng, i):
    for _ in range(3000):
        people = rng.sample(NAMES, 6)
        truth = people[:]
        rng.shuffle(truth)
        pool = []
        for a, b in itertools.permutations(people, 2):
            pool += [("left", a, b), ("adjacent", a, b)]
        for a in people:
            pool.append(("notend", a, None))
            for p in range(6):
                pool.append(("at", a, p))
        for a in people:
            for b, c in itertools.combinations([x for x in people if x != a], 2):
                pool.append(("between", a, (b, c)))
        pool = [c for c in pool if _holds6(truth, [c])]
        rng.shuffle(pool)
        perms = list(itertools.permutations(people))
        cons, alive = [], perms
        for c in pool:
            nxt = [p for p in alive if _holds6(p, [c])]
            if len(nxt) == len(alive):
                continue  # adds nothing: skip so the statement list stays short
            cons.append(c)
            alive = nxt
            if len(alive) == 1:
                k = rng.randrange(6)
                text = " ".join(_text6(x) for x in cons)
                return dict(prompt=(f"Six people stand in a row: {', '.join(sorted(people))}. {text} Who stands at position {k + 1}? {ANSWER_RULE}"),
                            answer_kind="text", expected=truth[k], format_regex=r"^[A-Za-z]+$")
            if len(cons) > 16:
                break
    raise RuntimeError("logic6 generator exhausted")


# ------------------------------------------------------------------------------------------------ calc_multi

def gen_calc_multi(rng, i):
    t = i % 3
    if t == 0:
        n, c = rng.randrange(20, 60), rng.randrange(1, 16) * 20
        s = rng.randrange(5, n - 4)
        m, d, f = rng.choice([10, 20, 25, 50]), rng.choice([10, 20, 25, 50]), rng.randrange(50, 400)
        profit = s * c * (100 + m) // 100 + (n - s) * c * (100 - d) // 100 - n * c - f
        return dict(prompt=(f"A shop buys {n} items at {c} euros each. It sells {s} of them at a {m} percent markup over the cost price and "
                            f"the remaining ones at {d} percent below the cost price. A flat fee of {f} euros is paid once. "
                            f"What is the profit in euros (negative if it is a loss)? {ANSWER_RULE}"),
                    answer_kind="int", expected=str(profit), format_regex=r"^-?\d+$")
    if t == 1:
        return _tank(rng, rng.randrange(60, 140), rng.randrange(10, 40), rng.randrange(5, 30), rng.randrange(7, 15))
    hh, mm = rng.randrange(0, 24), rng.randrange(0, 60)
    legs = [(rng.randrange(20, 300), rng.randrange(0, 45)) for _ in range(rng.randrange(2, 5))]  # (ride minutes, wait minutes)
    total = hh * 60 + mm + sum(r + w for r, w in legs)
    legs_txt = "; ".join(f"ride {r} minutes, then wait {w} minutes" for r, w in legs)
    return dict(prompt=(f"A journey starts at {hh:02d}:{mm:02d}. It has these legs in order: {legs_txt}. At what clock time (24 hour, HH:MM, "
                        f"midnight wraps to 00:00) does the journey end? {ANSWER_RULE}"),
                answer_kind="text", expected=f"{(total // 60) % 24:02d}:{total % 60:02d}", format_regex=r"^\d{2}:\d{2}$")


def _tank(rng, cap, a, b, h):
    level0 = rng.randrange(0, min(cap, 40) + 1)
    level = level0
    for hour in range(1, h + 1):
        level = min(cap, level + a) if hour % 2 == 1 else max(0, level - b)
    return dict(prompt=(f"A tank holds at most {cap} liters. In hours 1, 3, 5, ... a pump adds {a} liters (the tank never holds more than {cap}); "
                        f"in hours 2, 4, 6, ... a drain removes {b} liters (the tank never holds less than 0). At the start the tank holds "
                        f"{level0} liters. How many liters are in the tank after hour {h}? {ANSWER_RULE}"),
                answer_kind="int", expected=str(level), format_regex=r"^\d+$")


# ------------------------------------------------------------------------------------------------ text_rules

RULES = [
    ("remove every word shorter than {k} letters", lambda ws, k: [w for w in ws if len(w) >= k]),
    ("keep only the words that contain the letter '{c}'", lambda ws, c: [w for w in ws if c in w]),
    ("sort the words by length ascending, ties alphabetically", lambda ws, _: sorted(ws, key=lambda w: (len(w), w))),
    ("sort the words alphabetically in descending order", lambda ws, _: sorted(ws, reverse=True)),
    ("keep every second word, starting with the first", lambda ws, _: ws[::2]),
    ("drop the last word", lambda ws, _: ws[:-1]),
    ("reverse the letters of every word", lambda ws, _: [w[::-1] for w in ws]),
    ("reverse the order of the words", lambda ws, _: ws[::-1]),
]


def gen_text_rules(rng, i):
    for _ in range(500):
        words = rng.sample(WORDS, rng.randrange(8, 13))
        chosen = rng.sample(range(len(RULES)), rng.randrange(3, 6))
        cur, steps = list(words), []
        for ix in chosen:
            desc, fn = RULES[ix]
            param = rng.randrange(4, 7) if "{k}" in desc else rng.choice("aeiouyr") if "{c}" in desc else None
            cur = fn(cur, param)
            steps.append(desc.format(k=param, c=param))
        if not cur:
            continue
        sep, upper = rng.choice(["-", "+", "_"]), rng.choice([True, False])
        out = sep.join(cur)
        out = out.upper() if upper else out
        rules = "\n".join(f"{n + 1}) {s}" for n, s in enumerate(steps)) + f"\n{len(steps) + 1}) join the words with '{sep}'" + (
            f"\n{len(steps) + 2}) convert the result to upper case" if upper else "")
        return dict(prompt=f"Start with these words in this order: {', '.join(words)}.\nApply these rules strictly in order:\n{rules}\nWhat is the final string? {ANSWER_RULE}",
                    answer_kind="text", expected=out, format_regex=r"^\S+$")
    raise RuntimeError("text_rules generator exhausted")


# ------------------------------------------------------------------------------------------------ code_hard (reference implementations)

def merge_intervals(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def int_to_roman(n):
    out = ""
    for v, s in _ROMAN:
        while n >= v:
            out, n = out + s, n - v
    return out


def roman_to_int(s):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for k, ch in enumerate(s):
        v = vals[ch]
        total += -v if k + 1 < len(s) and vals[s[k + 1]] > v else v
    return total


def eval_expr(s):
    return eval(s, {"__builtins__": {}}, {})  # reference only; inputs are generated from digits, + - * ( ) and spaces


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lcs_length(a, b):
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def count_islands(grid):
    seen, n = set(), 0
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch == "1" and (r, c) not in seen:
                n += 1
                stack = [(r, c)]
                while stack:
                    y, x = stack.pop()
                    if (y, x) in seen or not (0 <= y < len(grid) and 0 <= x < len(grid[y])) or grid[y][x] != "1":
                        continue
                    seen.add((y, x))
                    stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    return n


def topo_lex(n, edges):
    indeg, adj = [0] * n, [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
        indeg[v] += 1
    heap = [k for k in range(n) if indeg[k] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        u = heapq.heappop(heap)
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, v)
    return order if len(order) == n else []


def shortest_path(n, edges, s, t):
    adj = [[] for _ in range(n)]
    for u, v, w in edges:
        adj[u].append((v, w))
        adj[v].append((u, w))
    dist = [None] * n
    heap = [(0, s)]
    while heap:
        d, u = heapq.heappop(heap)
        if dist[u] is not None:
            continue
        dist[u] = d
        for v, w in adj[u]:
            if dist[v] is None:
                heapq.heappush(heap, (d + w, v))
    return -1 if dist[t] is None else dist[t]


def first_bracket_error(s):
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    for k, ch in enumerate(s):
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return k
    return len(s) if stack else -1


def rotate_matrix(m):
    return [list(r) for r in zip(*m[::-1])]


def spiral_order(m):
    out, top, bottom, left, right = [], 0, len(m) - 1, 0, (len(m[0]) - 1 if m else -1)
    while top <= bottom and left <= right:
        out += [m[top][c] for c in range(left, right + 1)]
        out += [m[r][right] for r in range(top + 1, bottom + 1)]
        if top < bottom and left < right:
            out += [m[bottom][c] for c in range(right - 1, left - 1, -1)]
            out += [m[r][left] for r in range(bottom - 1, top, -1)]
        top, bottom, left, right = top + 1, bottom - 1, left + 1, right - 1
    return out


def add_business_days(start_iso, n):
    d, counted = datetime.date.fromisoformat(start_iso), 0
    while counted < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            counted += 1
    return d.isoformat()


def _expr(rng, depth=0):
    if depth >= 2 or rng.random() < 0.35:
        return str(rng.randrange(0, 21))
    a, b = _expr(rng, depth + 1), _expr(rng, depth + 1)
    e = f"{a} {rng.choice('+-*')} {b}"
    return f"({e})" if rng.random() < 0.5 else e


def _graph(rng, weighted):
    n = rng.randrange(2, 7)
    es = []
    for _ in range(rng.randrange(0, 2 * n + 1)):
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v:
            es.append([u, v, rng.randrange(1, 10)] if weighted else [u, v])
    return n, es


def _in_merge(rng):
    out = []
    for _ in range(rng.randrange(0, 9)):
        a, b = rng.randrange(0, 30), rng.randrange(0, 30)
        out.append([min(a, b), max(a, b)])
    return [out]


def _in_grid(rng):
    w = rng.randrange(1, 7)
    return [["".join(rng.choice("0011") for _ in range(w)) for _ in range(rng.randrange(1, 7))]]


def _in_topo(rng):
    n = rng.randrange(1, 7)
    es = [[u, v] for u in range(n) for v in range(n) if u < v and rng.random() < 0.3]
    if rng.random() < 0.25 and n >= 2:
        es.append([n - 1, 0])  # may close a cycle
    return [n, es]


def _in_sp(rng):
    n, es = _graph(rng, True)
    return [n, es, rng.randrange(n), rng.randrange(n)]


def _in_bracket(rng):
    return ["".join(rng.choice("()[]{}ab") for _ in range(rng.randrange(0, 11)))]


def _in_matrix(rng):
    r, c = rng.randrange(1, 5), rng.randrange(1, 5)
    return [[[rng.randrange(0, 10) for _ in range(c)] for _ in range(r)]]


def _in_biz(rng):
    d = datetime.date(2026, 1, 1) + datetime.timedelta(days=rng.randrange(0, 700))
    return [d.isoformat(), rng.randrange(0, 31)]


CODE_HARD = [
    ("f(intervals)", "the closed intervals [a, b] merged: sort them, merge intervals that overlap or touch (next start <= current end), return a sorted list of [a, b] lists",
     merge_intervals, _in_merge),
    ("f(s)", "the integer value of a valid Roman numeral string (1 to 3999)", roman_to_int, lambda rng: [int_to_roman(rng.randrange(1, 4000))]),
    ("f(s)", "the integer value of an expression made of non-negative integers, + - *, parentheses and spaces, with the usual precedence (no division, no unary minus)",
     eval_expr, lambda rng: [_expr(rng)]),
    ("f(a, b)", "the Levenshtein edit distance between strings a and b (insert, delete, substitute, each cost 1)", edit_distance,
     lambda rng: ["".join(rng.choice("abc") for _ in range(rng.randrange(0, 8))), "".join(rng.choice("abc") for _ in range(rng.randrange(0, 8)))]),
    ("f(a, b)", "the length of the longest common subsequence of strings a and b", lcs_length,
     lambda rng: ["".join(rng.choice("abc") for _ in range(rng.randrange(0, 9))), "".join(rng.choice("abc") for _ in range(rng.randrange(0, 9)))]),
    ("f(grid)", "the number of islands in a grid given as a list of equally long strings of '0' and '1' (an island is a group of '1' cells connected up, down, left or right)",
     count_islands, _in_grid),
    ("f(n, edges)", "the lexicographically smallest topological order (a list) of the nodes 0..n-1 for the directed edges [u, v] meaning u before v, or an empty list if the graph has a cycle",
     topo_lex, _in_topo),
    ("f(n, edges, s, t)", "the length of the shortest path from node s to node t in an undirected graph with nodes 0..n-1 and weighted edges [u, v, w], or -1 if t is unreachable",
     shortest_path, _in_sp),
    ("f(s)", "-1 if the brackets ()[]{} in the string are balanced (other characters are ignored); otherwise the index of the first closing bracket that has no matching opener of the same kind, or the length of the string if the only problem is brackets that are never closed",
     first_bracket_error, _in_bracket),
    ("f(m)", "the matrix m (a list of equally long lists) rotated by 90 degrees clockwise, as a new list of lists", rotate_matrix, _in_matrix),
    ("f(m)", "the elements of the matrix m (a list of equally long lists) in clockwise spiral order starting at the top-left, as a flat list", spiral_order, _in_matrix),
    ("f(start, n)", "the ISO date (YYYY-MM-DD) reached by moving forward day by day from the ISO date start until n weekdays (Monday to Friday) have been counted; n = 0 returns start unchanged",
     add_business_days, _in_biz),
]


def gen_code_hard(rng, i):
    sig, desc, ref, make = CODE_HARD[i % len(CODE_HARD)]
    tests = []
    for _ in range(24):
        args = make(rng)
        tests.append([args, json.loads(json.dumps(ref(*args)))])
    ex = "\n".join(f"  f({', '.join(json.dumps(a) for a in args)}) == {json.dumps(exp)}" for args, exp in tests[:3])
    prompt = (f"Write a Python function `{sig}` that returns {desc}. Examples:\n{ex}\n"
              "Reply with one ```python code block that defines only the function (and helpers if needed) and nothing else.")
    return dict(prompt=prompt, answer_kind="code", expected="", tests=tests, visible_n=3,
                reference_code=f"# reference: hard_tasks.{ref.__name__}")


HARD_CATEGORIES = ["table_agg", "logic6", "calc_multi", "text_rules", "code_hard"]
HARD_LABEL = {"table_agg": "mechanical", "text_rules": "mechanical", "logic6": "reasoning", "calc_multi": "reasoning", "code_hard": "reasoning"}
HARD_GENS = {"table_agg": gen_table_agg, "logic6": gen_logic6, "calc_multi": gen_calc_multi, "text_rules": gen_text_rules, "code_hard": gen_code_hard}
