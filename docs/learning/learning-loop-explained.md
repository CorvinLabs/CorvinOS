# Learning Loop Explained

## What is a Learning Loop?

A learning loop closes when:
```
Task Execution → Outcome Recorded → Feedback Given → Weights Adjusted → Next Task Uses Improved Weights
```

## The 6D Loss Vector

CorvinOS tracks 6 dimensions of Skill performance:

1. **Confidence** — Does the Skill make correct decisions?
2. **Latency** — How fast is the Skill?
3. **Cost** — What's the token/computation cost?
4. **Preference** — User's style preference (LLM vs deterministic)
5. **Attention** — How much context does the Skill need?
6. **Metric** — Custom business metrics

## How It Works

1. Skill executes (event logged to EventStore)
2. Task completes (outcome: success/partial/failure)
3. User gives feedback (confidence score)
4. Optimizer reads events + feedback
5. Computes new weights (Δloss per dimension)
6. Next execution uses improved weights

## Convergence

Convergence = mean confidence ≥ 0.80 over last 50 events

When converged:
- Skill weights frozen (production ready)
- Manual tuning available
- Next Skill version can evolve independently

## Operator Visibility

Dashboard: Console → Vibe Engineering → Learning Metrics
- Per-skill confidence trend
- Convergence status
- Feedback health
- Weight delta history
