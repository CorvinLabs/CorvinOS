# data_analysis

When analyzing tabular data, always:

1. Validate the shape of the input before computing statistics (check for empty data, all-null columns).
2. Distinguish numeric columns from categorical ones — apply appropriate stats to each type.
3. Report count alongside mean/min/max so the reader can assess sample size.
4. Flag outliers when max > 3× mean for any numeric column.
5. Return a structured dict, not free text, so downstream agents can reliably parse the output.
