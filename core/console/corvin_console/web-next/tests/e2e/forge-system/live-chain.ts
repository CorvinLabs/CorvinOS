/**
 * Proof that the suite never reached the LIVE install's hash-chained audit log.
 *
 * The first version of this check (R4-I-3, 2026-10-08 review) had little teeth:
 * it took its baseline in the LAST spec (so every earlier spec's writes were
 * invisible), skipped inside any git worktree, and passed on a chain that had
 * been rotated or truncated. Now:
 *   - global-setup.ts snapshots the chain BEFORE any spec runs;
 *   - the live chain is found through the MAIN checkout (git common dir), so a
 *     worktree checks the same file;
 *   - the snapshot pins the inode, the size and a hash of the last window, so
 *     rotation, truncation or an in-place rewrite makes the proof FAIL
 *     ("cannot prove") instead of passing;
 *   - the detector is itself tested (08-live-chain-detector.spec.ts) against a
 *     synthetic file it must flag.
 */
import { execFileSync } from 'child_process';
import crypto from 'crypto';
import fs from 'fs';
import os from 'os';
import path from 'path';

export const BASELINE_FILE = path.join(os.tmpdir(), `forge-e2e-live-chain-baseline-${process.env.FORGE_E2E_PORT_A ?? '8799'}.json`);
const WINDOW = 64 * 1024;

/** Strings that only this suite's seeded data or specs write (never the live operator's own work). */
export const LEAK_MARKERS = ['nordwind', 'forge_bundle.', 'xss-kit', 'adv0', 'corvin-forge-e2e', 'depot-hamburg'];

export type Snapshot = { path: string; exists: boolean; ino: number; size: number; windowOffset: number; windowSha: string };

export function liveChainPath(repo: string): string {
  if (process.env.FORGE_E2E_LIVE_CHAIN) return process.env.FORGE_E2E_LIVE_CHAIN;
  let main = repo;
  try {
    const common = execFileSync('git', ['-C', repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'], { encoding: 'utf-8' }).trim();
    if (common.endsWith('.git')) main = path.dirname(common);
  } catch { /* not a git checkout: use the repo itself */ }
  return path.join(main, '.corvin/tenants/_default/global/forge/audit.jsonl');
}

function windowSha(fd: number, size: number): { offset: number; sha: string } {
  const len = Math.min(WINDOW, size);
  const buf = Buffer.alloc(len);
  fs.readSync(fd, buf, 0, len, size - len);
  return { offset: size - len, sha: crypto.createHash('sha256').update(buf).digest('hex') };
}

export function snapshot(file: string): Snapshot {
  if (!fs.existsSync(file)) return { path: file, exists: false, ino: 0, size: 0, windowOffset: 0, windowSha: '' };
  const fd = fs.openSync(file, 'r');
  try {
    const st = fs.fstatSync(fd);
    const w = windowSha(fd, st.size);
    return { path: file, exists: true, ino: st.ino, size: st.size, windowOffset: w.offset, windowSha: w.sha };
  } finally { fs.closeSync(fd); }
}

export type Comparison = { problem: string | null; appended: string };

/** What happened to the file since `base`: appended text, or why that cannot be judged. */
export function compare(base: Snapshot): Comparison {
  if (!base.exists) return { problem: null, appended: '' };
  if (!fs.existsSync(base.path)) return { problem: 'the chain file vanished', appended: '' };
  const fd = fs.openSync(base.path, 'r');
  try {
    const st = fs.fstatSync(fd);
    if (st.ino !== base.ino) return { problem: 'the chain was rotated (new inode) — cannot prove isolation', appended: '' };
    if (st.size < base.size) return { problem: 'the chain was truncated — cannot prove isolation', appended: '' };
    const win = Buffer.alloc(Math.min(WINDOW, base.size));
    fs.readSync(fd, win, 0, win.length, base.windowOffset);
    if (crypto.createHash('sha256').update(win).digest('hex') !== base.windowSha) {
      return { problem: 'existing chain bytes changed in place — cannot prove isolation', appended: '' };
    }
    const buf = Buffer.alloc(st.size - base.size);
    fs.readSync(fd, buf, 0, buf.length, base.size);
    return { problem: null, appended: buf.toString('utf-8') };
  } finally { fs.closeSync(fd); }
}

export function leaks(appended: string): string[] {
  const low = appended.toLowerCase();
  return LEAK_MARKERS.filter((m) => low.includes(m));
}
