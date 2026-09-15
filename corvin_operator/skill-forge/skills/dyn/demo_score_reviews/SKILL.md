---
name: demo_score_reviews
description: Heuristic 0..100 for product reviews
---

# trading.score_reviews

Map a free-text review to a 0..100 score using four signals: brevity, sentiment polarity, named-feature mentions, and reviewer history weighting. Combine via simple weighted sum (0.25 each). Worker emits the integer score plus a 1-line explanation.
