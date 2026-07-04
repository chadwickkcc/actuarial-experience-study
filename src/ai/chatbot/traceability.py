"""Deterministic numeric-traceability post-check (Tech Spec v2.0.1 §E.7).

Pulled forward in Session 19: the two Skills (``interpret_ae_and_draft_memo``,
``explain_shap_results``) need this guard now to enforce block-not-repair
(FR-3B-19/22), and the §E.8 contracts import it from here. Session 20's chatbot
consumes the same module unchanged (FR-3B-34).

Mechanism (FR-3B-34): every numeric token in the rendered text must trace to a
value present in the supporting data (``result_set``) or echoed from the user's
own message. The check is pure, deterministic, and LLM-free — no number reaches
the user that the model invented.

  1. Extract numeric tokens from ``rendered_answer`` via regex (thousands
     separators, ``$``, ``%``, decimals, signs).
  2. Build the allowed-value set by recursively collecting every numeric value
     in ``result_set`` — actual numeric cells *and* numbers embedded in string
     values (so study periods and age/duration bands like ``"45-54"`` contribute
     both endpoints as positive numbers — see ``_NUMBER_RE``) — union numbers
     parsed from ``user_msg``. This recursive
     extraction handles both the Skills' nested ``memo_input`` dict (no
     ``flatten()`` needed) and the chatbot's ``{columns, rows}`` shape.
  3. A token traces if it matches some allowed value after rounding the allowed
     value to the token's display precision (relative tol ``rel_tol``; absolute
     ``1e-9`` near zero).
  4. ``passed`` iff every token traces; ``untraceable_nums`` lists failures.

The check is intentionally strict (no percent/unit rescaling): numbers in
``result_set`` are expected to be carried in the same display form the narrative
uses. Callers (the Skills, the chatbot) pre-format their supporting data
accordingly. This is the safe default — a deviation blocks rather than repairs.
"""
from __future__ import annotations

import re

from src.utils.types import TraceabilityResult

#: Matches a number-like token: optional sign, optional ``$``, digits with
#: optional thousands separators, optional fractional part, optional ``%``.
#: The ``(?<!\d)`` lookbehind means a leading ``-``/``+`` is a sign ONLY when not
#: preceded by a digit — so a hyphen *between* two digits (a range/date such as
#: ``"25-29"`` or ``"2023-12-31"``) is a separator, yielding both endpoints as
#: POSITIVE numbers, not ``[25, -29]``. This keeps a band/date label parsed the
#: same whether written with ``-``, an en-dash, or "to", while a genuine leading
#: negative (``-0.05``, ``-4,480,000``) still parses as negative.
_NUMBER_RE = re.compile(r"(?<!\d)[-+]?\$?\d[\d,]*(?:\.\d+)?%?")


def _parse_token(token: str) -> tuple[float, int] | None:
    """Return ``(value, display_decimals)`` for a numeric token, or ``None``.

    Strips ``$``, ``,``, ``%`` and a leading ``+``; preserves sign and decimal
    point. ``display_decimals`` is the count of fractional digits as written, so
    a higher-precision allowed value can be rounded to the token's precision.
    """
    cleaned = token.replace("$", "").replace(",", "").replace("%", "").lstrip("+")
    if cleaned in ("", "-", "+", "."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    decimals = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
    return value, decimals


def _extract_values(obj) -> list[float]:
    """Recursively collect numeric values from ``obj``.

    Walks dicts (values only) and lists/tuples; for numeric leaves keeps the
    value (excluding ``bool``); for string leaves extracts embedded numbers via
    the token regex. This covers the nested ``memo_input`` dict and the
    ``{columns, rows}`` result shape alike.
    """
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for tok in _NUMBER_RE.findall(obj):
            parsed = _parse_token(tok)
            if parsed is not None:
                out.append(parsed[0])
    elif isinstance(obj, dict):
        for value in obj.values():
            out.extend(_extract_values(value))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            out.extend(_extract_values(item))
    return out


def _traces(value: float, decimals: int, allowed: list[float], rel_tol: float) -> bool:
    """True if ``value`` matches some allowed value rounded to ``decimals``."""
    for cand in allowed:
        rounded = round(cand, decimals)
        tol = max(rel_tol * abs(rounded), 1e-9)
        if abs(value - rounded) <= tol:
            return True
    return False


def verify_traceability(
    rendered_answer: str,
    result_set,
    user_msg: str = "",
    rel_tol: float = 1e-6,
) -> TraceabilityResult:
    """Verify every numeric token in ``rendered_answer`` traces to the data.

    Args:
        rendered_answer: the final text shown to the user.
        result_set: the supporting data — a nested dict (``memo_input`` / SHAP
            cell), a ``{columns, rows}`` mapping, or any nested structure.
        user_msg: the user's own message; numbers echoed from it are allowed.
        rel_tol: relative tolerance for the precision-rounded match.

    Returns:
        ``TraceabilityResult(passed, untraceable_nums)``. ``passed`` is True only
        when every extracted token traces.
    """
    allowed = _extract_values(result_set) + _extract_values(user_msg)

    untraceable: list[str] = []
    for token in _NUMBER_RE.findall(rendered_answer or ""):
        parsed = _parse_token(token)
        if parsed is None:
            continue
        value, decimals = parsed
        if not _traces(value, decimals, allowed, rel_tol):
            untraceable.append(token)

    return TraceabilityResult(passed=not untraceable, untraceable_nums=untraceable)
