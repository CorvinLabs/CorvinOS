"""Manual twin probe (not part of the test suite): the same plan with and without ONE seeded flaw, neutral titles, the real
critic prompt of core/personal_kb/decoder.py on a real model. Synthetic plans only; needs the ``claude`` CLI (32 short calls).

The judging is done afterwards by a BLIND judge (shuffled runs, no variant label), never by keywords: the first probe
scored matches by keywords on plans whose titles named their flaw and was withdrawn after an independent audit.
The stored result, ``critic_probe_twins_results.json``, holds the pairs, the 32 critic answers and the judge's verdicts.
"""
import json, subprocess, sys, tempfile, concurrent.futures as cf
from pathlib import Path
_REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(_REPO), str(_REPO / "corvin_operator" / "forge"), str(_REPO / "corvin_operator" / "bridges" / "shared")]
from core.personal_kb import decoder
MODEL = "claude-sonnet-5-5"
POL = {"decoder_task_cap": 8, "decoder_template_variant": "t1"}

def T(*rows):  # (title, dod, depends_on)
    return {"tasks": [{"title": a, "dod": b, "depends_on": c} for a, b, c in rows]}

PAIRS = [
 {"title": "Balcony herb garden", "ctx": {"goal": "Grow basil, mint and chives on the balcony", "boundaries": ["Total spend under 60 euros", "Pots must fit the rail"], "success": "Three herbs alive after two weeks"},
  "sound": T(("Measure the rail and note the sun hours", "Sizes and hours written down", []), ("Choose basil, mint and chives and price pots, soil and seedlings; total under 60 euros", "Shopping list with a total below 60 euros", [0]),
             ("Buy the listed items", "Items at home, receipts total under 60 euros", [1]), ("Plant the three herbs in rail-fit pots", "Three herbs planted", [2]), ("Water daily and check after two weeks", "All three alive on day 14", [3])),
  "flawed": T(("Measure the rail and note the sun hours", "Sizes and hours written down", []), ("Choose basil, mint and chives and buy a 250 euro greenhouse shelf with pots, soil and seedlings", "Items at home", [0]),
              ("Plant the three herbs in rail-fit pots", "Three herbs planted", [1]), ("Water daily and check after two weeks", "All three alive on day 14", [2])),
  "flaw": "Cost conflict: the plan buys a 250 euro greenhouse shelf although the boundary says total spend under 60 euros."},
 {"title": "Spanish for the trip", "ctx": {"goal": "Order food and ask directions in Spanish", "boundaries": ["At most 4 hours of study a week"], "success": "A mock restaurant order and a mock directions question without notes by the trip date"},
  "sound": T(("Pick one course and book a weekly one-hour tutor slot", "Course chosen, 4 slots booked", []), ("Learn 100 restaurant and direction phrases", "100 phrases recalled at 80 percent in a quiz", [0]),
             ("Do two practice talks with the tutor", "Two talks done", [1]), ("Do a mock restaurant order out loud", "Order completed without notes", [2]), ("Do a mock directions question out loud", "Question asked and answer understood without notes", [2])),
  "flawed": T(("Pick one course and book a weekly one-hour tutor slot", "Course chosen, 4 slots booked", []), ("Learn 100 restaurant and direction phrases", "100 phrases recalled at 80 percent in a quiz", [0]),
              ("Do two practice talks with the tutor", "Two talks done", [1]), ("Do a mock restaurant order out loud", "Order completed without notes", [2])),
  "flaw": "Missing coverage: the goal and the success criterion include asking directions, but no task practises or tests asking directions."},
 {"title": "Family photo backup", "ctx": {"goal": "Two verified copies of all family photos, one stored off-site", "boundaries": ["Budget 120 euros", "No cloud services"], "success": "Both copies verified; one copy is at a relative's home"},
  "sound": T(("Count the photos and total size", "Number and GB written down", []), ("Buy two external drives large enough, total under 120 euros", "Two drives received, receipt under 120 euros", [0]),
             ("Copy the library to both drives and verify checksums", "Checksum report with zero errors for both", [1]), ("Hand one drive to a relative and note where it is", "Drive delivered, location noted", [2])),
  "flawed": T(("Count the photos and total size", "Number and GB written down", []), ("Buy one external drive large enough, under 120 euros", "Drive received", [0]),
              ("Copy the library to the drive and verify checksums", "Checksum report with zero errors", [1]), ("Hand the drive to a relative and note where it is", "Drive delivered", [2])),
  "flaw": "Missing copy: the goal needs two verified copies but the plan buys one drive and makes one copy."},
 {"title": "Monthly reading club", "ctx": {"goal": "A reading club that meets once a month", "boundaries": ["At most 8 people", "Meetings at home"], "success": "Three meetings held in three months"},
  "sound": T(("Ask five friends and get at least three yes", "Three or more confirmed", []), ("Pick the first book by vote", "Book chosen", [0]), ("Fix the date and place of meeting one", "Date in calendars, home address agreed", [1]),
             ("Hold meetings one, two and three, one per month", "Three meetings held with at least three people each", [2])),
  "flawed": T(("Ask five friends and get at least three yes", "Three or more confirmed", []), ("Pick the first book by vote", "Book chosen", [0]), ("Fix the date and place of meeting one", "Date in calendars, home address agreed", [1]),
              ("Hold the first meeting", "First meeting held with at least three people", [2])),
  "flaw": "Missing coverage: the success criterion needs three meetings in three months, but the plan stops after the first meeting."},
 {"title": "Shared flat shopping list", "ctx": {"goal": "One shopping list both flatmates use", "boundaries": ["Free app only", "No new accounts"], "success": "Both add and tick items for two weeks"},
  "sound": T(("Pick a list app both phones already have", "App named, both confirm it is installed", []), ("Create the list with five shared categories", "List with five categories exists", [0]),
             ("Both add at least three items", "Six or more items from two people", [1]), ("Both tick items off for two weeks", "Tick history from both on day 14", [2])),
  "flawed": T(("Pick a list app both phones already have", "App named, both confirm it is installed", []), ("Create the list with five shared categories", "List with five categories exists", [0]),
              ("Make the list work well", "It works well", [1]), ("Both tick items off for two weeks", "Tick history from both on day 14", [2])),
  "flaw": "Vague definition of done: the task 'Make the list work well' has the definition of done 'It works well', which cannot be checked."},
 {"title": "Marathon training", "ctx": {"goal": "Finish a marathon on 12 October", "boundaries": ["Four runs a week at most", "No run longer than 32 km before the race"], "success": "Race finished on 12 October"},
  "sound": T(("Run a 10 km test and set the pace plan", "Test done, pace plan written", []), ("Build the weekly base to 40 km over six weeks", "Six weeks logged at 40 km", [0]), ("Long runs rising to 32 km over ten weeks", "A 32 km run logged by 28 September", [1]),
             ("Taper for two weeks", "Two taper weeks logged", [2]), ("Run the marathon on 12 October", "Finish recorded", [3])),
  "flawed": T(("Run a 10 km test and set the pace plan", "Test done, pace plan written", []), ("Build the weekly base to 40 km over six weeks", "Six weeks logged at 40 km", [0]), ("Long runs rising to 38 km over ten weeks", "A 38 km run logged by 28 September", [1]),
              ("Taper for two weeks", "Two taper weeks logged", [2]), ("Run the marathon on 12 October", "Finish recorded", [3])),
  "flaw": "Boundary violation: the plan has a 38 km long run although the boundary allows at most 32 km before the race."},
 {"title": "Sourdough starter", "ctx": {"goal": "A starter that raises bread on day ten", "boundaries": ["Flour and water only"], "success": "A loaf risen to double its volume on day ten"},
  "sound": T(("Mix flour and water in a jar", "Jar started on day one", []), ("Feed the starter every day for nine days", "Nine feedings logged", [0]), ("Bake a loaf on day ten", "Loaf risen to double volume", [1])),
  "flawed": T(("Feed the starter every day for nine days", "Nine feedings logged", []), ("Mix flour and water in a jar", "Jar started on day one", [0]), ("Bake a loaf on day ten", "Loaf risen to double volume", [1])),
  "flaw": "Wrong order: the task that feeds the starter for nine days depends on nothing and comes before the task that creates the starter."},
 {"title": "Garage cleanup", "ctx": {"goal": "A garage where the car fits and tools have a place", "boundaries": ["Two weekends", "Nothing thrown away without a check"], "success": "The car parks inside and tools are on the shelf"},
  "sound": T(("Sort everything into keep, give away and throw away", "Three piles, throw-away pile checked", []), ("Take away the give-away and throw-away piles", "Piles gone from the garage", [0]), ("Install shelves for the tools", "Tools on the shelves", [1]), ("Park the car inside", "Car parked inside", [2])),
  "flawed": T(("Sort everything into keep, give away and throw away", "Three piles", []), ("Take away the give-away and throw-away piles", "Piles gone from the garage", [0]), ("Throw away the throw-away pile today without checking", "Pile gone", [0]), ("Install shelves for the tools", "Tools on the shelves", [1]), ("Park the car inside", "Car parked inside", [3])),
  "flaw": "Boundary violation: a task throws things away without checking although the boundary says nothing is thrown away without a check."},
]

def prompt(c, kind):
    subj = c[kind]
    idea = {"title": c["title"], "body": ""}
    return decoder._prompt("adversarial", idea, {"shape": c["ctx"]}, [], POL, subject=subj)

def call(p):
    with tempfile.TemporaryDirectory(prefix="critic2-") as cwd:
        r = subprocess.run(["claude", "-p", "--max-turns", "1", "--tools", "", "--no-session-persistence", "--model", MODEL], input=p, capture_output=True, text=True, timeout=240, cwd=cwd)
    return r.stdout if r.returncode == 0 else ""

def job(a):
    ci, kind, rep = a
    raw = call(prompt(PAIRS[ci], kind))
    out = decoder.engine.extract_json(raw) if raw else None
    objs = [{"cat": o.get("category"), "text": str(o.get("text", ""))} for o in ((out or {}).get("objections", []) if isinstance(out, dict) else []) if isinstance(o, dict)]
    return {"case": ci, "kind": kind, "rep": rep, "ok": raw != "", "objections": objs}

if __name__ == "__main__":
    jobs = [(ci, k, r) for ci in range(len(PAIRS)) for k in ("sound", "flawed") for r in range(2)]
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(job, jobs))
    json.dump({"model": MODEL, "pairs": [{"title": p["title"], "ctx": p["ctx"], "sound": p["sound"], "flawed": p["flawed"], "flaw": p["flaw"]} for p in PAIRS], "runs": res},
              open(Path(__file__).with_name("critic_probe_twins_raw.json"), "w"), indent=1)
    print("done", len(res), "failed", sum(1 for r in res if not r["ok"]))
