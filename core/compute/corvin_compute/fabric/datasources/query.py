"""Parameterized SQL query builder for DataSource adapters (ADR-0026 Section D).

safe_to_sql() produces (sql_string, param_list) tuples.
Values are NEVER interpolated — %s / ? / @param placeholders only.
"""
from __future__ import annotations

import re
from typing import Any

from .protocol import FilterExpr, SourceQuery, _VALID_OPS

# Re-export for convenience
__all__ = [
    "SourceQuery",
    "FilterExpr",
    "safe_to_sql",
    "_validate_identifier",
    "_validate_order_by",
]

# ---------------------------------------------------------------------------
# Identifier validation (structural SQL-identifier-injection defence)
# ---------------------------------------------------------------------------
#
# Values are always bound via placeholders (see below), but IDENTIFIERS
# (column names, table names, ORDER BY targets) are structural and cannot be
# parameterized in standard SQL. Any identifier that reaches an f-string MUST
# first pass this strict allowlist: a leading letter/underscore followed by
# letters, digits, underscores, and dots (dots permit schema.table /
# table.column qualification). This rejects whitespace, quotes, semicolons,
# parentheses, comment markers, and every SQL operator — closing the
# identifier-injection vector.
# \Z (end of STRING), not $ (which also matches just before a trailing \n) —
# else `col\n` would pass and leak a newline into the emitted SQL.
_IDENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_.]*\Z")

# ORDER BY additionally allows an optional trailing direction keyword.
_ORDER_BY_RE = re.compile(
    r"\A\s*(?P<col>[A-Za-z_][A-Za-z0-9_.]*)\s*(?P<dir>ASC|DESC)?\s*\Z",
    re.IGNORECASE,
)


def _validate_identifier(name: Any, *, kind: str = "identifier") -> str:
    """Validate a SQL identifier against a strict allowlist.

    Permits letters, digits, underscore, and dots (for schema.table /
    table.column). Raises ValueError on anything else so a malicious column /
    table name can never break out of its identifier position.
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid SQL {kind} {name!r}: must match {_IDENT_RE.pattern} "
            "(letters, digits, underscore, and dots only)."
        )
    return name


def _validate_order_by(order_by: Any, *, kind: str = "order_by") -> str:
    """Validate an ORDER BY clause: a single identifier plus optional ASC/DESC.

    Returns the normalized clause (direction upper-cased). Raises ValueError for
    anything that is not ``<identifier>[ ASC|DESC]``.
    """
    if not isinstance(order_by, str):
        raise ValueError(f"Invalid SQL {kind} {order_by!r}: must be a string.")
    m = _ORDER_BY_RE.match(order_by)
    if not m:
        raise ValueError(
            f"Invalid SQL {kind} {order_by!r}: expected "
            "'<column>' optionally followed by ASC or DESC."
        )
    col = _validate_identifier(m.group("col"), kind=f"{kind} column")
    direction = m.group("dir")
    return f"{col} {direction.upper()}" if direction else col


# ---------------------------------------------------------------------------
# Dialect-specific placeholder and LIKE escape
# ---------------------------------------------------------------------------

_PLACEHOLDER: dict[str, str] = {
    "psycopg2": "%s",
    "mysql": "%s",
    "sqlite": "?",
    "bigquery": "@p",  # BigQuery uses named params; we'll use positional @p0 … @pN
    "snowflake": "%s",
    "redshift": "%s",
}

_VALID_DIALECTS = frozenset(_PLACEHOLDER)


def _ph(dialect: str, idx: int) -> str:
    """Return the right placeholder token for this dialect and position."""
    if dialect == "bigquery":
        return f"@p{idx}"
    return _PLACEHOLDER[dialect]


# ---------------------------------------------------------------------------
# Op → SQL fragment builder
# ---------------------------------------------------------------------------

_SCALAR_OP_SQL: dict[str, str] = {
    "=": "=",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "like": "LIKE",
}


def _op_fragment(
    col: str, op: str, dialect: str, param_idx: int, value: Any,
) -> tuple[str, int, list[Any]]:
    """Return (sql_fragment, next_param_idx, bind_params).

    bind_params is the ordered list of values this fragment consumes. Values
    are NEVER interpolated into the SQL — one placeholder is emitted per bound
    value (structural SQL-injection defence).

    Raises ValueError for unknown op (already validated in FilterExpr, but
    safe_to_sql may receive raw dicts from untrusted callers).
    """
    if op not in _VALID_OPS:
        raise ValueError(
            f"Unknown FilterExpr op {op!r}. Valid ops: {sorted(_VALID_OPS)}"
        )

    # The filter column is an identifier (structural, cannot be parameterized).
    col = _validate_identifier(col, kind="filter column")

    if op == "is_null":
        # is_null: no parameter consumed
        return f"{col} IS NULL", param_idx, []

    if op in ("in", "not_in"):
        # Membership: one placeholder per list element, each bound separately.
        # A non-list scalar is treated as a single-element set.
        if isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = [value]
        keyword = "IN" if op == "in" else "NOT IN"
        if not values:
            # IN () / NOT IN () is invalid SQL. Collapse to a constant predicate:
            #   x IN ()     → never matches  → always-false (1 = 0)
            #   x NOT IN () → matches all    → always-true  (1 = 1)
            const = "1 = 0" if op == "in" else "1 = 1"
            return const, param_idx, []
        placeholders = []
        for _ in values:
            placeholders.append(_ph(dialect, param_idx))
            param_idx += 1
        frag = f"{col} {keyword} ({', '.join(placeholders)})"
        return frag, param_idx, values

    if op in _SCALAR_OP_SQL:
        ph = _ph(dialect, param_idx)
        return f"{col} {_SCALAR_OP_SQL[op]} {ph}", param_idx + 1, [value]

    raise ValueError(f"Unhandled op {op!r}")  # unreachable but satisfies type checkers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def safe_to_sql(
    query: SourceQuery,
    table: str,
    dialect: str,
) -> tuple[str, list[Any]]:
    """Build a parameterized SELECT statement.

    Returns (sql, params) where params is the ordered list of values to bind.
    Values are NEVER formatted into the SQL string — structural SQL-injection defence.

    Args:
        query: The SourceQuery describing columns, filters, ordering, and limit.
        table: Fully-qualified table name (already validated by caller).
        dialect: One of psycopg2 | mysql | sqlite | bigquery | snowflake.

    Raises:
        ValueError: for unknown dialect or unknown op in any FilterExpr.
    """
    if dialect not in _VALID_DIALECTS:
        raise ValueError(
            f"Unknown SQL dialect {dialect!r}. Valid: {sorted(_VALID_DIALECTS)}"
        )

    # Table + column list are identifiers — validate before interpolation.
    table = _validate_identifier(table, kind="table")

    # Column list
    if query.columns:
        col_list = ", ".join(
            _validate_identifier(c, kind="column") for c in query.columns
        )
    else:
        col_list = "*"

    parts: list[str] = [f"SELECT {col_list} FROM {table}"]
    params: list[Any] = []
    param_idx = 0

    # WHERE clause
    where_fragments: list[str] = []
    for fexpr in query.filters:
        frag, param_idx, bind_params = _op_fragment(
            fexpr.col, fexpr.op, dialect, param_idx, fexpr.value,
        )
        where_fragments.append(frag)
        params.extend(bind_params)

    if where_fragments:
        parts.append("WHERE " + " AND ".join(where_fragments))

    # ORDER BY — validated identifier plus optional ASC/DESC.
    if query.order_by:
        parts.append(f"ORDER BY {_validate_order_by(query.order_by)}")

    # LIMIT
    if query.limit is not None:
        lim_ph = _ph(dialect, param_idx)
        parts.append(f"LIMIT {lim_ph}")
        params.append(query.limit)
        param_idx += 1

    sql = " ".join(parts)
    return sql, params
