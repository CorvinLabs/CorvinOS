/** Runs once, BEFORE any spec: pin the live audit chain so the isolation proof can compare. */
import fs from 'fs';
import { REPO } from './repo-root';
import { BASELINE_FILE, liveChainPath, snapshot } from './live-chain';

export default async function globalSetup() {
  const snap = snapshot(liveChainPath(REPO));
  fs.writeFileSync(BASELINE_FILE, JSON.stringify(snap));
  console.log(`[forge-e2e] live chain baseline: ${snap.exists ? `${snap.size} bytes, inode ${snap.ino}` : 'no live chain on this host'}`);
}
