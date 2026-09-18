#!/usr/bin/env node
/**
 * Recover a file from the Claude Code session transcripts by replaying every
 * Write/Edit the agent performed on it.
 *
 * Why this exists: uncommitted work in a single file can vanish for reasons git
 * cannot help with -- an antivirus verdict deleting the file (measured
 * 2026-09-18 on install.ps1), an errant `git checkout --`, a truncating write.
 * The transcripts are a write-ahead log of the file: a Write carries the full
 * content, an Edit carries old_string/new_string. Replaying them rebuilds it,
 * comments and all.
 *
 * Usage:
 *   node scripts/recover_file_from_transcript.js --list install.ps1
 *   node scripts/recover_file_from_transcript.js install.ps1 -o /tmp/out.ps1
 *
 *   --list          only print the operations found (timestamp, kind, size)
 *   -o <path>       where to write the rebuilt file (default: <name>.recovered)
 *   --project <dir> transcript directory; default is the one matching $PWD
 *
 * Replay starts at the LAST full Write and applies every later Edit. Hunks that
 * do not match are REPORTED, never skipped silently: one unmatched hunk is a
 * one-line fix, a hidden one ships as a bug. Always verify the result with the
 * file's own language (parse it) and with any invariant the file states about
 * itself before trusting it.
 *
 * See docs/concepts/CONCEPT-0004-recover-a-lost-file-by-replaying-the-transcript.md
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

function projectDirForCwd() {
  // Claude Code slugifies the project path: C:\Users\x\Documents\Repo ->
  // C--Users-x-Documents-Repo (drive colon and separators become dashes).
  const slug = process.cwd().replace(/:/g, '-').replace(/[\\/]/g, '-');
  return path.join(os.homedir(), '.claude', 'projects', slug);
}

function parseArgs(argv) {
  const opts = { list: false, target: null, out: null, project: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--list') opts.list = true;
    else if (a === '-o' || a === '--out') opts.out = argv[++i];
    else if (a === '--project') opts.project = argv[++i];
    else if (!opts.target) opts.target = a;
  }
  return opts;
}

function collectOps(projectDir, targetName) {
  const wanted = targetName.replace(/\\/g, '/').toLowerCase();
  const ops = [];
  const files = fs.readdirSync(projectDir).filter((f) => f.endsWith('.jsonl'));
  for (const f of files) {
    const text = fs.readFileSync(path.join(projectDir, f), 'utf8');
    for (const line of text.split('\n')) {
      // Cheap prefilter: these files run to tens of MB and JSON.parse on every
      // line is the slow part.
      if (!line.trim() || line.indexOf(targetName) === -1) continue;
      let obj;
      try { obj = JSON.parse(line); } catch (e) { continue; }
      const content = obj && obj.message && obj.message.content;
      if (!Array.isArray(content)) continue;
      for (const block of content) {
        if (block.type !== 'tool_use') continue;
        if (block.name !== 'Write' && block.name !== 'Edit') continue;
        const fp = String((block.input || {}).file_path || '').replace(/\\/g, '/').toLowerCase();
        if (!fp.endsWith(wanted)) continue;
        ops.push({ ts: obj.timestamp || '', kind: block.name, input: block.input, session: f.slice(0, 8) });
      }
    }
  }
  ops.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  return ops;
}

function replay(ops) {
  let start = -1;
  for (let i = ops.length - 1; i >= 0; i--) if (ops[i].kind === 'Write') { start = i; break; }
  if (start === -1) return { text: null, applied: 0, failed: [], base: null };

  let text = ops[start].input.content;
  const failed = [];
  let applied = 0;
  for (let i = start + 1; i < ops.length; i++) {
    const op = ops[i];
    if (op.kind === 'Write') { text = op.input.content; applied++; continue; }
    const oldS = op.input.old_string;
    const newS = op.input.new_string;
    if (typeof oldS !== 'string' || typeof newS !== 'string') { failed.push([op.ts, 'malformed edit', '']); continue; }
    const hits = text.split(oldS).length - 1;
    const head = oldS.split('\n')[0].slice(0, 70);
    if (hits === 0) { failed.push([op.ts, 'no match', head]); continue; }
    if (hits > 1 && !op.input.replace_all) { failed.push([op.ts, 'ambiguous x' + hits, head]); continue; }
    text = op.input.replace_all ? text.split(oldS).join(newS) : text.replace(oldS, newS);
    applied++;
  }
  return { text, applied, failed, base: ops[start] };
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (!opts.target) {
    console.error('usage: recover_file_from_transcript.js [--list] <file name or suffix> [-o out]');
    process.exit(2);
  }
  const projectDir = opts.project || projectDirForCwd();
  if (!fs.existsSync(projectDir)) {
    console.error('no transcript directory: ' + projectDir);
    process.exit(2);
  }

  const ops = collectOps(projectDir, opts.target);
  if (ops.length === 0) {
    console.error('no Write/Edit operations found for ' + opts.target + ' in ' + projectDir);
    process.exit(1);
  }

  for (const op of ops) {
    const size = op.kind === 'Write'
      ? (op.input.content || '').split('\n').length + ' lines'
      : (op.input.new_string || '').length + ' chars';
    console.log([op.ts, op.kind.padEnd(5), size, op.session].join('  '));
  }
  console.log('-- ' + ops.length + ' operation(s)');
  if (opts.list) return;

  const result = replay(ops);
  if (result.text === null) {
    console.error('no full Write found -- only Edits. There is no trustworthy base to replay onto;');
    console.error('re-author from the last commit instead (see CONCEPT-0004).');
    process.exit(1);
  }
  console.log('base Write: ' + result.base.ts + ' (' + result.base.input.content.split('\n').length + ' lines)');
  console.log('applied ' + result.applied + ' of ' + (ops.length - ops.indexOf(result.base) - 1) + ' later operation(s)');
  if (result.failed.length) {
    console.log('UNMATCHED HUNKS -- apply these by hand and re-verify:');
    for (const f of result.failed) console.log('  ' + f.join(' | '));
  }
  const out = opts.out || (path.basename(opts.target) + '.recovered');
  fs.writeFileSync(out, result.text, 'utf8');
  console.log('wrote ' + out + ' (' + result.text.split('\n').length + ' lines)');
  console.log('NOW: parse/compile it, check the invariants it states about itself, then commit.');
}

main();
