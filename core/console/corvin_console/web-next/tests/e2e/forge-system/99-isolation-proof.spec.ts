/**
 * 99 — runs LAST (alphabetical, one worker): nothing of the whole suite reached
 * the LIVE install's audit chain. The baseline was pinned by global-setup.ts
 * before the first spec, so every spec's writes are in the window — the old
 * check took it in spec 07 and could not see 01-06 (R4-I-3). Its detector is
 * proven in 98-live-chain-detector.spec.ts.
 *
 * A live operator who runs a forge bundle on this host DURING the run would
 * trip the 'forge_bundle.' marker: re-run in that case, do not weaken the marker.
 */
import fs from 'fs';
import { BASELINE_FILE, compare, leaks, liveChainPath, type Snapshot } from './live-chain';
import { REPO } from './repo-root';
import { expect, test } from './forge-fixtures';

test('nothing of this suite reached the LIVE install\'s audit chain', () => {
  expect(fs.existsSync(BASELINE_FILE), 'global-setup.ts did not run — there is no baseline to compare').toBe(true);
  const base = JSON.parse(fs.readFileSync(BASELINE_FILE, 'utf-8')) as Snapshot;
  expect(base.path).toBe(liveChainPath(REPO));
  test.skip(!base.exists, 'no live chain on this host: nothing to protect');
  const r = compare(base);
  expect(r.problem, r.problem ?? '').toBeNull();
  expect(leaks(r.appended)).toEqual([]);
});
