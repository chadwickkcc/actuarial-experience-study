"""The FR-3B-51 result-match rule (Session 22; Tech Spec §E.9).

``results_match`` decides whether a generated query's result set matches the
reference query's, used by the evaluation harness to score execution accuracy.
The rule (when ``value_check`` is true):

    (a) identical set of column names (order-insensitive);
    (b) identical row count;
    (c) sorted-multiset row equality (row + column ordering ignored);
    (d) numeric cells match within a relative tolerance (1e-6), absolute 1e-9
        near zero;
    (e) NULLs match NULLs.

For golden entries flagged ``value_check: false`` (data-dependent results), only
clauses (a) and (b) are applied. A generated query that errored, returned no
result, or violates any applied clause counts as a miss.

Pure Python — this module performs no SQL and touches no database.
"""
from __future__ import annotations

from typing import Optional, Sequence

_REL_TOL = 1e-6
_ABS_TOL = 1e-9


def _is_number(value) -> bool:
    """True for ints/floats but not booleans (DuckDB booleans are not numbers)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cells_match(a, b, rel_tol: float) -> bool:
    """Compare one cell pair: NULLs match NULLs; numerics within tolerance."""
    a_none, b_none = a is None, b is None
    if a_none or b_none:
        return a_none and b_none
    a_num, b_num = _is_number(a), _is_number(b)
    if a_num != b_num:
        return False
    if a_num:
        return abs(float(a) - float(b)) <= max(rel_tol * max(abs(float(a)), abs(float(b))), _ABS_TOL)
    return str(a) == str(b)


def _sort_key(row: Sequence):
    """A stable ordering key for a canonical-ordered row (NULLs first, then
    numbers by rounded value, then strings) so two equal multisets sort alike."""
    key = []
    for cell in row:
        if cell is None:
            key.append((0, 0.0, ""))
        elif _is_number(cell):
            key.append((1, round(float(cell), 9), ""))
        else:
            key.append((2, 0.0, str(cell)))
    return key


def _canonical_rows(rows, cols, order: list[str]) -> list[list]:
    """Reorder each row's cells into ``order`` (by column name) so two result
    sets with the same columns in a different order become directly comparable."""
    index = {str(c): i for i, c in enumerate(cols)}
    return [[row[index[name]] for name in order] for row in rows]


def results_match(
    generated_rows: Optional[Sequence[Sequence]],
    generated_cols: Optional[Sequence[str]],
    reference_rows: Optional[Sequence[Sequence]],
    reference_cols: Optional[Sequence[str]],
    value_check: bool,
    rel_tol: float = _REL_TOL,
) -> bool:
    """Return ``True`` iff the generated result set matches the reference under
    the FR-3B-51 rule. A ``None`` generated/reference set (the query errored or
    returned nothing) is always a miss.
    """
    if generated_rows is None or generated_cols is None:
        return False
    if reference_rows is None or reference_cols is None:
        return False

    gen_cols = [str(c) for c in generated_cols]
    ref_cols = [str(c) for c in reference_cols]

    # (a) identical column-name set (order-insensitive).
    if set(gen_cols) != set(ref_cols):
        return False
    # (b) identical row count.
    if len(generated_rows) != len(reference_rows):
        return False
    if not value_check:
        return True

    # (c)-(e) sorted-multiset equality with numeric tolerance + NULL handling.
    order = sorted(ref_cols)
    gen = sorted(_canonical_rows(generated_rows, gen_cols, order), key=_sort_key)
    ref = sorted(_canonical_rows(reference_rows, ref_cols, order), key=_sort_key)
    for gen_row, ref_row in zip(gen, ref):
        for a, b in zip(gen_row, ref_row):
            if not _cells_match(a, b, rel_tol):
                return False
    return True
