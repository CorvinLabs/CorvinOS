# Tutorial: Use Learning Feedback Loops (10 min)

## Goal
Configure a Skill to learn from user feedback and improve over time.

## Steps

1. **Enable Learning for Your Skill**
   - Skill settings → Learning enabled: ✓

2. **Run Tasks & Collect Feedback**
   - Each task execution records a learning event
   - Operator can mark outcome: success / partial / failure

3. **Monitor Convergence**
   - View Vibe Engineering dashboard
   - Check if skill confidence ≥ 0.80 (converged)

4. **Adjust Configuration**
   - Learning loop auto-adjusts thresholds
   - Next task uses improved weights

## Example
Task 1: outcome=success, confidence=0.7
Task 2: outcome=success, confidence=0.75
...
Task 20: mean confidence=0.82 → CONVERGED

## Learn More
See [Learning Loop Explained](../learning/learning-loop-explained.md)
