# quality_assessment

When scoring a Markdown report for quality:

1. Check structural completeness first: title, section headings, data tables, summary.
2. Score each criterion independently — partial credit is not applicable, each criterion is binary.
3. A score >= 70 is a passing grade; below 70 requires revision.
4. Every feedback item must be actionable: say exactly what is missing, not just that something is wrong.
5. Do not penalize for content you were not given (e.g., if no categorical columns exist, absence of that table is not a defect).
6. Return structured output: {score, feedback, passed} — never free text.
