"""SQL statements count as a database mention in the big-data decision.

``_DB_RE`` gave every statement verb the same follow-up anchor (a second ``from``), which
only ``SELECT .. FROM`` can satisfy: ``UPDATE .. SET``, ``INSERT INTO .. VALUES`` and
``DELETE FROM .. WHERE`` never matched, so a bulk request phrased as SQL was not routed
as big data. Judged through the public ``is_big_data_task`` (the production caller of the
regex), including the negative controls that keep ordinary prose out.
"""
from __future__ import annotations

import pytest

from corvin_operator.bridges.shared.delegation_policy import is_big_data_task


@pytest.mark.parametrize("prompt", [
    "Summarize and aggregate across all of it: UPDATE orders SET status = 'archived' WHERE created < '2020-01-01'",
    "Aggregate every record: DELETE FROM sessions WHERE expired = 1",
    "Analyze and aggregate: INSERT INTO totals (k, v) VALUES ('a', 1)",
    "Summarize and aggregate: SELECT region, count(*) FROM orders GROUP BY region",
])
def test_a_bulk_request_phrased_as_sql_is_big_data(prompt):
    assert is_big_data_task(prompt) is True


@pytest.mark.parametrize("prompt", [
    "Please update the README",
    "Insert into the document a short summary of the meeting",
    "Delete from my notes everything about the old project",
    "Update me on how the aggregate numbers look",
])
def test_ordinary_prose_with_the_same_verbs_is_not(prompt):
    assert is_big_data_task(prompt) is False
