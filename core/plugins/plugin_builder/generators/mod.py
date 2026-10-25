"""Claude Code mod generator (CONCEPT-0118 phase 1, ADR-2285).

Turns SkillForge skills into a Claude Code *mod*: a plugin whose hooks module runs in the Claude
Code process and adds ``/quiz``, ``/quiz-answer`` and ``/skill-info`` commands. The skill stays the
portable unit; the mod is an adapter generated from its metadata (CONCEPT-0118 §3).

Like every generator here it only EMITS files and never loads them (ADR-0244): ``generate_mod_files``
is pure, ``write_mod`` is the one function that touches the disk.

What the generated mod does (and the invariants the tests pin):

* Adapter-only (CONCEPT-0118 option A): ``session.start`` registers nothing unless ``CORVIN_CHANNEL_ID``
  is set, every command re-checks at its own call site and throws, and the write path probes the
  ``skill_forge`` and ``forge`` MCP servers read-only BEFORE it writes. A mod cannot abort a session
  (a hook that throws is skipped), so the gate is structural, not an exit.
* Grade + audit: one ``skill_grade`` call and one ``mod.command_executed`` audit call per answer.
  Never the answer text. The tenant is set by the audit gateway, not by the mod.
* No ``tool.call`` / ``prompt.submit`` / permission hooks: the mod rewrites and approves nothing.

Card format in a skill body, under a ``## quiz`` heading, one card per line::

    - <id> | <question> | <keyword>, <keyword> | <hint>

All data is embedded as JSON literals, so no card text can break out of the generated JavaScript.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ModSpecError",
    "QuizCard",
    "SkillRecord",
    "parse_quiz_cards",
    "load_store",
    "generate_mod_files",
    "write_mod",
    "main",
]

_MOD_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_CARD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_QUIZ_HEADING_RE = re.compile(r"^##\s+quiz\s*$", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_MIN_KEYWORD_LEN = 3  # a 1-2 letter keyword matches nearly any answer


class ModSpecError(ValueError):
    """The input cannot become a mod. Raised, never papered over."""


@dataclass(frozen=True)
class QuizCard:
    skill: str
    id: str
    question: str
    keywords: tuple[str, ...]
    hint: str


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    type: str
    scope: str


# ── parsing ───────────────────────────────────────────────────────────────────

def parse_quiz_cards(skill_name: str, body_md: str) -> list[QuizCard]:
    """Cards from the ``## quiz`` section of a skill body. No section -> no cards."""
    cards: list[QuizCard] = []
    in_quiz = False
    for lineno, raw in enumerate(body_md.splitlines(), start=1):
        line = raw.strip()
        if _QUIZ_HEADING_RE.match(line):
            in_quiz = True
            continue
        if in_quiz and _NEXT_HEADING_RE.match(line):
            break
        if not in_quiz or not line.startswith("- "):
            continue
        parts = [p.strip() for p in line[2:].split("|")]
        if len(parts) != 4:
            raise ModSpecError(
                f"{skill_name}: quiz line {lineno} must be '<id> | <question> | <keywords> | <hint>'"
            )
        cid, question, kw_raw, hint = parts
        keywords = tuple(k.strip().lower() for k in kw_raw.split(",") if k.strip())
        if not _CARD_ID_RE.match(cid):
            raise ModSpecError(f"{skill_name}: quiz line {lineno}: bad card id {cid!r}")
        if not question or not hint:
            raise ModSpecError(f"{skill_name}: quiz line {lineno}: question and hint are required")
        if not keywords:
            raise ModSpecError(f"{skill_name}: quiz line {lineno}: at least one keyword is required")
        short = [k for k in keywords if len(k) < _MIN_KEYWORD_LEN]
        if short:
            raise ModSpecError(
                f"{skill_name}: quiz line {lineno}: keywords shorter than {_MIN_KEYWORD_LEN} "
                f"characters match almost any answer: {short}"
            )
        cards.append(QuizCard(skill_name, cid, question, keywords, hint))
    ids = [c.id for c in cards]
    if len(ids) != len(set(ids)):
        raise ModSpecError(f"{skill_name}: duplicate card id in the quiz section")
    return cards


def load_store(root: Path) -> list[tuple[SkillRecord, list[QuizCard]]]:
    """Read a SkillForge store (``manifest.json`` + ``skills/<name>/SKILL.md``). Read-only.

    A manifest entry without a ``SKILL.md`` is an error, not a silent skip: a generated mod must
    never claim to cover a skill it could not read.
    """
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ModSpecError(f"no manifest.json under {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModSpecError(f"manifest.json is not valid JSON: {exc.msg}") from exc
    out: list[tuple[SkillRecord, list[QuizCard]]] = []
    for entry in manifest.get("skills", []):
        name = str(entry.get("name", ""))
        if not name:
            raise ModSpecError("manifest entry without a name")
        body_path = root / "skills" / name / "SKILL.md"
        if not body_path.is_file():
            raise ModSpecError(f"{name}: listed in the manifest but {body_path} is missing")
        meta = entry.get("metadata", {})
        record = SkillRecord(
            name=name,
            description=str(meta.get("description", "")),
            type=str(meta.get("type", "")),
            scope=str(meta.get("scope", "")),
        )
        out.append((record, parse_quiz_cards(name, body_path.read_text(encoding="utf-8"))))
    return out


# ── generation (pure) ─────────────────────────────────────────────────────────

def _js(value: object) -> str:
    """A JSON literal is a valid JavaScript literal; ``</`` is split so it cannot end a script."""
    return json.dumps(value, ensure_ascii=False, indent=2).replace("</", "<\\/")


def _correct_answer(card: QuizCard) -> str:
    return " ".join(card.keywords)


def generate_mod_files(
    *,
    mod_name: str,
    version: str,
    description: str,
    cards: list[QuizCard],
    info: bool,
) -> dict[str, str]:
    """The files of one mod as ``{relative path: text}``. Deterministic: same input, same bytes."""
    if not _MOD_NAME_RE.match(mod_name) or len(mod_name) > 64:
        raise ModSpecError(f"mod name {mod_name!r} must be lower-case letters, digits and hyphens")
    if mod_name.startswith("claude-"):
        raise ModSpecError("a plugin name starting with 'claude-' is rejected by claude plugin validate")
    if not _VERSION_RE.match(version):
        raise ModSpecError(f"version {version!r} must look like 1.2.3")
    if not cards and not info:
        raise ModSpecError("nothing to generate: no quiz cards and info is off")

    manifest = {
        "name": mod_name,
        "version": version,
        "description": description,
        "author": {"name": "CorvinOS plugin-builder"},
    }
    hooks = {"description": f"{mod_name} hooks module (generated)", "modules": ["./register.js"]}
    files = {
        ".claude-plugin/plugin.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "hooks/hooks.json": json.dumps(hooks, ensure_ascii=False, indent=2) + "\n",
        "hooks/register.js": _render_register(mod_name, cards, info),
        "README.md": _render_readme(mod_name, cards, info),
    }
    if cards:
        files["tests/gate.test.ts"] = _render_tests(mod_name, cards[0])
    if info:
        files["tests/info.test.ts"] = _TESTS_INFO.replace("__MOD__", json.dumps(mod_name))
    return files


def _render_register(mod_name: str, cards: list[QuizCard], info: bool) -> str:
    card_data = [
        {"skill": c.skill, "id": c.id, "question": c.question, "keywords": list(c.keywords), "hint": c.hint}
        for c in cards
    ]
    parts = [_HEAD.replace("__MOD__", mod_name).replace("__CARDS__", _js(card_data))]
    parts.append(_GATES)
    parts.append("export function register(on) {\n")
    parts.append(_SESSION_START.replace("__COMMANDS__", _register_commands(bool(cards), info)))
    if cards:
        parts.append(_QUIZ_HANDLERS.replace("__MOD__", mod_name))
    if info:
        parts.append(_INFO_HANDLER)
    parts.append("}\n")
    return "".join(parts)


def _register_commands(quiz: bool, info: bool) -> str:
    blocks = []
    if quiz:
        blocks.append(
            "    await $.command.register({ name: 'quiz', description: 'Show the current learning card' })\n"
            "    await $.command.register({\n"
            "      name: 'quiz-answer',\n"
            "      description: 'Answer the current learning card and record a grade',\n"
            "      argumentHint: '<answer>',\n"
            "    })\n"
        )
    if info:
        blocks.append(
            "    await $.command.register({\n"
            "      name: 'skill-info',\n"
            "      description: 'Show type, scope and grades of a SkillForge skill',\n"
            "      argumentHint: '<skill name>',\n"
            "    })\n"
        )
    return "".join(blocks)


def _render_readme(mod_name: str, cards: list[QuizCard], info: bool) -> str:
    skills = sorted({c.skill for c in cards})
    lines = [
        f"# {mod_name}",
        "",
        "Generated by the CorvinOS plugin-builder mod generator (CONCEPT-0118, ADR-2285). Do not edit by hand:",
        "regenerate from the skills instead.",
        "",
        "Commands: " + ", ".join(
            (["`/quiz`", "`/quiz-answer`"] if cards else []) + (["`/skill-info`"] if info else [])
        ),
        "",
        f"Quiz cards: {len(cards)}" + (f" from {', '.join(f'`{s}`' for s in skills)}" if skills else ""),
        "",
        "## Rules",
        "",
        "- Works only in a session the CorvinOS adapter started (`CORVIN_CHANNEL_ID` set). Elsewhere it registers",
        "  nothing and Claude Code prints a skipped-hook line. The marker is an environment variable, not a credential.",
        "- Before any write the `skill_forge` and `forge` MCP servers are probed read-only; if one is missing the",
        "  command throws and nothing is written.",
        "- Writes one grade per answer and one `mod.command_executed` audit event (mod-reported, ADR-2285).",
        "  Never the answer text.",
        "- Hooks only `session.start` and `command.run`. It rewrites and approves nothing.",
        "",
        "Tested against Claude Code 2.1.308. Mod events and methods change between releases.",
        "",
    ]
    return "\n".join(lines)


def _render_tests(mod_name: str, card: QuizCard) -> str:
    return (
        _TESTS.replace("__QUESTION__", json.dumps(card.question, ensure_ascii=False))
        .replace("__SKILL__", json.dumps(card.skill))
        .replace("__CARD_ID__", json.dumps(card.id))
        .replace("__CORRECT__", json.dumps(_correct_answer(card), ensure_ascii=False))
        .replace("__MOD__", json.dumps(mod_name))
    )


# ── writing ───────────────────────────────────────────────────────────────────

def write_mod(files: dict[str, str], out_dir: Path, *, force: bool = False) -> list[Path]:
    """Write ``files`` under ``out_dir``. Refuses a non-empty directory unless ``force``.

    The generator emits; it does not decide where a plugin lives. By repo rule a plugin's source
    belongs in the Corvin-Marketplace repo, so the caller picks ``out_dir``.
    """
    out_dir = out_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise ModSpecError(f"{out_dir} is not empty; pass --force to overwrite generated files")
    written: list[Path] = []
    for rel, text in files.items():
        target = (out_dir / rel).resolve()
        if out_dir not in target.parents:
            raise ModSpecError(f"refusing to write outside the output directory: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


# ── CLI: the real entry point ─────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m plugin_builder.generators.mod_cli",
        description="Generate a Claude Code mod from SkillForge skills (CONCEPT-0118).",
    )
    ap.add_argument("--store", required=True, type=Path, help="SkillForge store root (has manifest.json)")
    ap.add_argument("--out", required=True, type=Path, help="output directory (a plugin; keep it out of CorvinOS)")
    ap.add_argument("--name", default="corvin-skills-mod")
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--skill", action="append", default=[], help="only these skills (repeatable); default all")
    ap.add_argument("--info", action="store_true", help="add /skill-info for every skill in scope")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        loaded = load_store(args.store)
        if args.skill:
            wanted = set(args.skill)
            missing = wanted - {r.name for r, _ in loaded}
            if missing:
                raise ModSpecError(f"not in the manifest: {sorted(missing)}")
            loaded = [(r, c) for r, c in loaded if r.name in wanted]
        cards = [card for _, skill_cards in loaded for card in skill_cards]
        files = generate_mod_files(
            mod_name=args.name,
            version=args.version,
            description=f"Generated from {len(loaded)} SkillForge skill(s): "
            f"{len(cards)} quiz card(s){', skill info' if args.info else ''}",
            cards=cards,
            info=args.info,
        )
        written = write_mod(files, args.out, force=args.force)
    except ModSpecError as exc:
        print(f"mod generator: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"mod": args.name, "skills": len(loaded), "cards": len(cards), "files": len(written)}))
    return 0


# ── templates ─────────────────────────────────────────────────────────────────

_HEAD = """\
// Generated by the CorvinOS plugin-builder mod generator (CONCEPT-0118, ADR-2285). Do not edit by hand.
//
// Adapter-only: this mod works ONLY in a session the CorvinOS adapter started. A mod cannot abort a
// session (a hook that throws is skipped, fail-open), so the gate is structural: outside an adapter
// session nothing is registered, and every command re-checks at its own call site and throws.

const MOD = "__MOD__"

const CARDS = __CARDS__

// Server names as the CorvinOS adapter injects them. A plain `claude` session has neither.
const SKILL_SERVER = 'skill_forge'
const AUDIT_SERVER = 'forge'

const IDX = MOD + ':idx'
const RUNS = MOD + ':runs'

function score(card, answer) {
  const a = String(answer).toLowerCase()
  const hits = card.keywords.filter((k) => a.includes(k)).length
  return hits / card.keywords.length
}

"""

_GATES = """\
// Gate 1: was this session started by the CorvinOS adapter? Throws, never returns a message.
// The adapter sets CORVIN_CHANNEL_ID on every spawn. It is a marker, not a credential.
async function requireAdapter($) {
  // The name must be a literal: `claude plugin validate` lists the variables a mod reads.
  const channel = await $.env.get('CORVIN_CHANNEL_ID')
  if (!channel) {
    throw new Error(MOD + ' refuses to run: this session was not started by the CorvinOS adapter (CORVIN_CHANNEL_ID is unset)')
  }
}

// Gate 2: are the two servers this mod writes through reachable? Read-only probes. Throws.
async function requireServers($) {
  try {
    await $.mcp.call(SKILL_SERVER, 'skill_list', {})
    await $.mcp.call(AUDIT_SERVER, 'forge_list', {})
  } catch (err) {
    throw new Error(MOD + ' refuses to run: the CorvinOS MCP servers "' + SKILL_SERVER + '" and "' + AUDIT_SERVER + '" are not reachable in this session')
  }
}

"""

_SESSION_START = """\
  on('session.start', async ($, e, next) => {
    // No adapter marker: register nothing. The throw is reported by Claude Code as a skipped hook.
    await requireAdapter($)
__COMMANDS__    return next(e)
  })

"""

_QUIZ_HANDLERS = """\
  on('command.run', { command: 'quiz' }, async ($) => {
    await requireAdapter($)
    const idx = (await $.store.get(IDX)) ?? 0
    const card = CARDS[idx % CARDS.length]
    return { text: 'Card ' + (idx % CARDS.length + 1) + '/' + CARDS.length + ' [' + card.skill + ']: ' + card.question + '\\nAnswer with /quiz-answer <text>' }
  })

  on('command.run', { command: 'quiz-answer' }, async ($, e) => {
    // Both gates run BEFORE anything is written. A failure throws; there is no success text to return.
    await requireAdapter($)
    await requireServers($)

    const idx = (await $.store.get(IDX)) ?? 0
    const card = CARDS[idx % CARDS.length]
    const s = score(card, e.args)

    const runs = (await $.store.get(RUNS)) ?? 0
    await $.store.set(RUNS, runs + 1)

    // Grade only: score and the card id. Never the answer text. A failure propagates.
    await $.mcp.call(SKILL_SERVER, 'skill_grade', {
      name: card.skill,
      run_id: 'quiz-' + card.id + '-' + runs,
      score: s,
      notes: 'mod card ' + card.id,
    })

    // ADR-2285: report the action to the audit chain. Mod-reported (attribution, not proof).
    // Metadata only: never the answer text. The gateway sets the tenant, not the mod.
    await $.mcp.call(AUDIT_SERVER, 'audit_write_event', {
      event_type: 'mod.command_executed',
      details: { mod: MOD, command: 'quiz-answer', skill: card.skill, score: s },
    })

    await $.store.set(IDX, idx + 1)

    const verdict = s >= 1 ? 'Correct.' : s > 0 ? 'Partly correct.' : 'Not correct.'
    return { text: verdict + ' ' + card.hint + ' Grade recorded.' }
  })

"""

_INFO_HANDLER = """\
  on('command.run', { command: 'skill-info' }, async ($, e) => {
    await requireAdapter($)
    await requireServers($)
    const wanted = String(e.args).trim()
    if (!wanted) throw new Error(MOD + ': /skill-info needs a skill name')
    const res = await $.mcp.call(SKILL_SERVER, 'skill_list', {})
    const text = res.content.map((c) => c.text ?? '').join('')
    const skills = JSON.parse(text).data.skills
    const skill = skills.find((s) => s.name === wanted)
    if (!skill) throw new Error(MOD + ': no skill named ' + wanted)
    const mean = skill.n_grades > 0 ? skill.mean_score.toFixed(2) : 'none'
    // ADR-2285: reading skill metadata through the CorvinOS servers is an action too. Mod-reported,
    // metadata only (no score: nothing was graded). The gateway sets the tenant.
    await $.mcp.call(AUDIT_SERVER, 'audit_write_event', {
      event_type: 'mod.command_executed',
      details: { mod: MOD, command: 'skill-info', skill: skill.name },
    })
    return { text: skill.name + ': type ' + skill.type + ', scope ' + skill.scope + ', ' + skill.n_grades + ' grade(s), mean ' + mean }
  })

"""

_TESTS = """\
import { expect, mock, test } from 'claude-code/testing'

// Generated with the mod (CONCEPT-0118). Claude Code's own answer to a command nobody handled:
// a hook that throws is skipped (fail-open), so this text coming back proves the mod did NOT answer.
const DEFAULT = 'CLAUDE-CODE-DEFAULT'
const MOD = __MOD__
const SKILL = __SKILL__
const CARD_ID = __CARD_ID__
const QUESTION = __QUESTION__
const CORRECT = __CORRECT__

type Opts = { adapter?: boolean; skillForge?: boolean; forge?: boolean }

function wire(on: any, opts: Opts = {}) {
  const adapter = opts.adapter ?? true
  const skillForge = opts.skillForge ?? true
  const forge = opts.forge ?? true
  mock.env(on, adapter ? { CORVIN_CHANNEL_ID: 'discord:test' } : {})
  const saved = new Map<string, unknown>()
  const writes: string[] = []
  const calls: { raw: string; tool: string }[] = []
  const registered: string[] = []
  on('store.get', ($: any, e: any) => ({ value: saved.get(e.key) }))
  on('store.set', ($: any, e: any) => {
    writes.push(e.key)
    saved.set(e.key, e.value)
    return { value: undefined }
  })
  on('command.register', ($: any, e: any) => {
    registered.push(JSON.stringify(e))
    return { value: undefined }
  })
  on('mcp.call', ($: any, e: any) => {
    const raw = JSON.stringify(e)
    const tool = ['skill_list', 'forge_list', 'skill_grade', 'audit_write_event'].find((t) => raw.includes(t)) ?? '?'
    calls.push({ raw, tool })
    const isForge = tool === 'forge_list' || tool === 'audit_write_event'
    if (isForge && !forge) return { deny: 'forge not connected' }
    if (!isForge && !skillForge) return { deny: 'skill_forge not connected' }
    return { value: { content: [] } }
  })
  on('session.start', () => ({ cwd: '/work' }))
  on('command.run', () => ({ text: DEFAULT }))
  return { saved, writes, calls, registered }
}

const START = { surface: 'terminal', isInteractive: true, cwd: '/work' } as const

test('adapter session: /quiz shows the first card', async ($, on) => {
  const { registered } = wire(on)
  await $.session.start(START)
  expect(registered.length).toBeGreaterThan(0)
  const out = await $.command.run({ command: 'quiz', args: '' })
  expect(out.text).toContain(QUESTION)
  expect(out.text).toContain(SKILL)
})

test('adapter session: a correct answer probes, grades, audits, in that order', async ($, on) => {
  const { calls, saved } = wire(on)
  const out = await $.command.run({ command: 'quiz-answer', args: CORRECT })
  expect(out.text).toContain('Correct.')
  expect(out.text).toContain('Grade recorded.')
  expect(calls.map((c) => c.tool)).toEqual(['skill_list', 'forge_list', 'skill_grade', 'audit_write_event'])
  expect(calls[2].raw).toContain('"score":1')
  expect(calls[2].raw).toContain(SKILL)
  expect(calls[2].raw).toContain(CARD_ID)
  expect(saved.get(MOD + ':idx')).toBe(1)
})

test('adapter session: a wrong answer records score 0', async ($, on) => {
  const { calls } = wire(on)
  const out = await $.command.run({ command: 'quiz-answer', args: 'no idea' })
  expect(out.text).toContain('Not correct.')
  expect(calls.find((c) => c.tool === 'skill_grade')!.raw).toContain('"score":0')
})

test('answer text never reaches the grade or the audit call', async ($, on) => {
  const { calls } = wire(on)
  await $.command.run({ command: 'quiz-answer', args: 'SECRET-ANSWER-TEXT ' + CORRECT })
  for (const c of calls) expect(c.raw).not.toContain('SECRET-ANSWER-TEXT')
  const audit = calls.find((c) => c.tool === 'audit_write_event')!
  expect(audit.raw).toContain('mod.command_executed')
  expect(audit.raw).toContain(MOD)
  expect(audit.raw).toContain(SKILL)
})

test('normal session: nothing is registered, nothing is answered, nothing is written', async ($, on) => {
  const { registered, writes, calls } = wire(on, { adapter: false })
  await $.session.start(START)
  expect(registered.length).toBe(0)
  const quiz = await $.command.run({ command: 'quiz', args: '' })
  const answer = await $.command.run({ command: 'quiz-answer', args: CORRECT })
  expect(quiz.text).toBe(DEFAULT)
  expect(answer.text).toBe(DEFAULT)
  expect(answer.text).not.toContain('Grade recorded')
  expect(writes.length).toBe(0)
  expect(calls.length).toBe(0)
})

test('servers missing: no grade, no audit, no store write, no success text', async ($, on) => {
  const { writes, calls } = wire(on, { skillForge: false, forge: false })
  const out = await $.command.run({ command: 'quiz-answer', args: CORRECT })
  expect(out.text).toBe(DEFAULT)
  expect(out.text).not.toContain('Grade recorded')
  expect(calls.some((c) => c.tool === 'skill_grade')).toBe(false)
  expect(calls.some((c) => c.tool === 'audit_write_event')).toBe(false)
  expect(writes.length).toBe(0)
})

test('only forge missing: nothing is graded either (no half write)', async ($, on) => {
  const { writes, calls } = wire(on, { forge: false })
  const out = await $.command.run({ command: 'quiz-answer', args: CORRECT })
  expect(out.text).toBe(DEFAULT)
  expect(calls.some((c) => c.tool === 'skill_grade')).toBe(false)
  expect(writes.length).toBe(0)
})
"""

_TESTS_INFO = """\
import { expect, mock, test } from 'claude-code/testing'

// Generated with the mod (CONCEPT-0118). A hook that throws is skipped (fail-open); the stub below stands
// for Claude Code's own answer to a command nobody handled, so getting it back proves the mod did NOT answer.
const DEFAULT = 'CLAUDE-CODE-DEFAULT'
const MOD = __MOD__

type Opts = { adapter?: boolean; skillForge?: boolean; forge?: boolean; skills?: any[] }

const SKILLS = [
  { name: 'assistant.graded', type: 'domain', scope: 'user', n_grades: 2, mean_score: 0.65 },
  { name: 'assistant.ungraded', type: 'learned-experience', scope: 'user', n_grades: 0, mean_score: 0 },
]

function wire(on: any, opts: Opts = {}) {
  const adapter = opts.adapter ?? true
  const skillForge = opts.skillForge ?? true
  const forge = opts.forge ?? true
  const skills = opts.skills ?? SKILLS
  mock.env(on, adapter ? { CORVIN_CHANNEL_ID: 'discord:test' } : {})
  const calls: { raw: string; tool: string }[] = []
  const registered: string[] = []
  on('command.register', ($: any, e: any) => {
    registered.push(JSON.stringify(e))
    return { value: undefined }
  })
  on('mcp.call', ($: any, e: any) => {
    const raw = JSON.stringify(e)
    const tool = ['skill_list', 'forge_list', 'skill_grade', 'audit_write_event'].find((t) => raw.includes(t)) ?? '?'
    calls.push({ raw, tool })
    const isForge = tool === 'forge_list' || tool === 'audit_write_event'
    if (isForge && !forge) return { deny: 'forge not connected' }
    if (!isForge && !skillForge) return { deny: 'skill_forge not connected' }
    if (tool === 'skill_list') {
      return { value: { content: [{ type: 'text', text: JSON.stringify({ ok: true, data: { ok: true, skills } }) }] } }
    }
    return { value: { content: [] } }
  })
  on('session.start', () => ({ cwd: '/work' }))
  on('command.run', () => ({ text: DEFAULT }))
  return { calls, registered }
}

const START = { surface: 'terminal', isInteractive: true, cwd: '/work' } as const

test('adapter session: /skill-info is registered', async ($, on) => {
  const { registered } = wire(on)
  await $.session.start(START)
  expect(registered.some((r) => r.includes('skill-info'))).toBe(true)
})

test('adapter session: a graded skill is described, then audited, nothing is graded', async ($, on) => {
  const { calls } = wire(on)
  const out = await $.command.run({ command: 'skill-info', args: 'assistant.graded' })
  expect(out.text).toContain('assistant.graded')
  expect(out.text).toContain('type domain')
  expect(out.text).toContain('scope user')
  expect(out.text).toContain('2 grade(s)')
  expect(out.text).toContain('mean 0.65')
  expect(calls.map((c) => c.tool)).toEqual(['skill_list', 'forge_list', 'skill_list', 'audit_write_event'])
  const audit = calls.find((c) => c.tool === 'audit_write_event')!
  expect(audit.raw).toContain('mod.command_executed')
  expect(audit.raw).toContain('skill-info')
  expect(audit.raw).toContain(MOD)
  expect(calls.some((c) => c.tool === 'skill_grade')).toBe(false)
})

test('adapter session: an ungraded skill says mean none', async ($, on) => {
  wire(on)
  const out = await $.command.run({ command: 'skill-info', args: 'assistant.ungraded' })
  expect(out.text).toContain('0 grade(s)')
  expect(out.text).toContain('mean none')
})

test('adapter session: every skill the store lists is found (200 synthetic skills)', async ($, on) => {
  const many = Array.from({ length: 200 }, (_, i) => ({
    name: 'assistant.s' + i, type: 'domain', scope: 'user', n_grades: i % 3, mean_score: 0.5,
  }))
  wire(on, { skills: many })
  for (const i of [0, 1, 99, 199]) {
    const out = await $.command.run({ command: 'skill-info', args: 'assistant.s' + i })
    expect(out.text).toContain('assistant.s' + i + ':')
  }
})

test('adapter session: an unknown skill or no name is not answered and not audited', async ($, on) => {
  const { calls } = wire(on)
  const unknown = await $.command.run({ command: 'skill-info', args: 'assistant.nope' })
  const empty = await $.command.run({ command: 'skill-info', args: '  ' })
  expect(unknown.text).toBe(DEFAULT)
  expect(empty.text).toBe(DEFAULT)
  expect(calls.some((c) => c.tool === 'audit_write_event')).toBe(false)
})

test('normal session: nothing is registered, answered or called', async ($, on) => {
  const { registered, calls } = wire(on, { adapter: false })
  await $.session.start(START)
  expect(registered.length).toBe(0)
  const out = await $.command.run({ command: 'skill-info', args: 'assistant.graded' })
  expect(out.text).toBe(DEFAULT)
  expect(calls.length).toBe(0)
})

test('servers missing: no answer and no audit event', async ($, on) => {
  const { calls } = wire(on, { skillForge: false, forge: false })
  const out = await $.command.run({ command: 'skill-info', args: 'assistant.graded' })
  expect(out.text).toBe(DEFAULT)
  expect(calls.some((c) => c.tool === 'audit_write_event')).toBe(false)
})

test('only forge missing: no answer and no audit event', async ($, on) => {
  const { calls } = wire(on, { forge: false })
  const out = await $.command.run({ command: 'skill-info', args: 'assistant.graded' })
  expect(out.text).toBe(DEFAULT)
  expect(calls.some((c) => c.tool === 'audit_write_event')).toBe(false)
})
"""

