import { describe, it, expect } from 'vitest';
import { isRunDone, isPipelineDone } from '@/pages/compute';

describe('Agentic Compute "done" state predicates (WA-18)', () => {
  // The flat/l25 run engine (core/compute/corvin_compute/budget.py RUN_STATE_*)
  // never produces a state literally named "complete" — its real terminal
  // states are converged/stalled/budget_exhausted. Every `=== "complete"`
  // check across compute.tsx compared against a value that can never occur,
  // so the top KPI strip, run-card "done" badges, and both Analytics tables
  // always read as empty/zero regardless of how many runs actually succeeded.
  describe('isRunDone', () => {
    it.each(['converged', 'stalled', 'budget_exhausted'])(
      'treats %s as done',
      (state) => {
        expect(isRunDone(state)).toBe(true);
      },
    );

    it.each(['running', 'queued', 'failed', 'aborted', 'complete', null, undefined])(
      'does not treat %s as done',
      (state) => {
        expect(isRunDone(state)).toBe(false);
      },
    );
  });

  describe('isPipelineDone', () => {
    it('treats "converged" as done', () => {
      expect(isPipelineDone('converged')).toBe(true);
    });

    it.each(['running', 'gate_open', 'failed', 'complete', null, undefined])(
      'does not treat %s as done',
      (state) => {
        expect(isPipelineDone(state)).toBe(false);
      },
    );
  });
});
