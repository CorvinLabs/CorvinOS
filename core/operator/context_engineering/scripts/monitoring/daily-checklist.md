# Week 6 Daily Measurement Checklist

## Morning Stand-up (9am UTC)
- [ ] Aggregation completed overnight (check logs)
- [ ] All dashboard metrics updated
- [ ] No critical alerts firing
- [ ] Queue files present and healthy
- [ ] Checksum validation OK (no failures)

## Midday Spot-Check (12pm UTC)
- [ ] Sample 10 predictions and verify accuracy
- [ ] Review user profile classifications
- [ ] Check budget/complexity correlation
- [ ] Log observations to measurement journal

## EOD Review (5pm UTC)
- [ ] Aggregate metrics for the day
- [ ] Update day-specific dashboard
- [ ] Note any anomalies
- [ ] Prepare next-day focus areas

## Weekly Review (Friday EOD)
- [ ] Calculate all four track metrics
- [ ] Verify > 0.80 accuracy targets
- [ ] Identify any refinement areas
- [ ] Prepare Week 6 go/no-go summary

## Data Integrity Checks
- [ ] JSONL files not corrupted
- [ ] Checksums validating correctly
- [ ] No duplicate records
- [ ] Timestamps sequential
- [ ] User IDs consistent

## Troubleshooting
- If metric < target:
  1. Check sample size (need N > min)
  2. Verify data collection is enabled
  3. Review logs for errors
  4. Escalate if pattern persists

- If alerts firing:
  1. Don't ignore - investigate immediately
  2. Verify it's not a measurement artifact
  3. Escalate critical alerts (checksum, locks)
