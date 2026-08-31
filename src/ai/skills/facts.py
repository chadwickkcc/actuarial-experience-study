"""Fact-reference citation for the generate-then-verify Skills (M-12).

The Skills draft prose over an app-assembled fact pack and are then checked by
``verify_traceability``. That check answers "does this number appear *somewhere*
in the supporting data?" — never "is this the value of the thing the sentence
claims". With a fact pack of ~900 distinct values and A/E ratios clustered in
[0, 2] at two decimals, the allowed set is dense enough that a fabricated ratio
almost always collides with an unrelated product's figure: the adversarial review
measured **88% of random two-decimal values passing** (M-12). Since these Skills
block unconditionally and export as signed deliverables, that is the weakest
guarantee in the AI layer.

This module makes the guarantee structural rather than statistical, using the
pattern the chatbot's data path already proves: **the model never writes a
figure**. It cites one by key —

    Whole Life mortality experience came in at {{fact:overall.WL.MORTALITY.ae}}

— and the application substitutes the value from the pack. A key that does not
exist raises ``FactSlotError``: a mis-citation fails loudly instead of rendering
a plausible wrong number.

After substitution the usual post-check still runs, but against a **narrow**
allowed set:

  * the values the application itself injected (a citation is data by construction), plus
  * the numbers appearing in the fact pack's **keys** — years, age bands, illness
    codes and the like.

Keys are labels, values are claims. Naming a label the data carries is legitimate
prose ("in 2023", "the 45-54 band"); stating a figure requires a citation. This
is what shrinks the fabrication surface from every value in the pack to
essentially nothing: measured against the shipped commentary pack, fabricated
two-decimal ratios fall from **88.9% accepted to 1.1%**.

**Scope.** Used by the four generate-then-verify surfaces that export as signed
deliverables: the A/E memo, the management commentary, the fraud narrative and
the chat commentary. Deliberately NOT used by two others, where set membership is
already a narrow check: ``explain_shap_results`` verifies against a single SHAP
cell (a dozen or so values), and the chatbot's exploratory synthesis verifies
against the evidence it just fetched for that same question. Neither carries the
dense cross-product pack that made the check meaningless.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.utils.types import TraceabilityResult

#: ``{{fact:some.dotted.key}}`` — the only way a figure may enter a draft.
SLOT_RE = re.compile(r"\{\{fact:([^{}\s]+)\}\}")

_NUMBER_IN_KEY_RE = re.compile(r"\d[\d.]*")


class FactSlotError(Exception):
    """Raised when a draft cites a fact key the pack does not contain."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        super().__init__(
            "Draft cited unknown fact key(s): " + ", ".join(sorted(set(keys)))
        )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_fact(value: Any) -> str:
    """Render a fact for the catalogue and for substitution (one rendering only).

    Integers plain, other numbers to at most 4 dp with trailing zeros trimmed —
    the same convention the chatbot's slot filler uses, so a figure reads the
    same however it reached the page.
    """
    if value is None:
        return "N/A"
    if _is_number(value):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return str(value)


def flatten_facts(pack: Any, *, exclude: Iterable[str] = ()) -> dict[str, Any]:
    """Flatten a nested fact pack to ``dotted.key -> leaf value``.

    Lists are indexed (``drivers[0].segment``). Top-level keys in ``exclude`` are
    dropped entirely — ``run_id`` above all, whose UUID digit-runs would widen the
    allowed set and could mask an invented figure.
    """
    excluded = set(exclude)
    flat: dict[str, Any] = {}

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if not prefix and key in excluded:
                    continue
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                walk(item, f"{prefix}[{i}]")
        else:
            flat[prefix] = node

    walk(pack, "")
    return flat


def render_fact_catalogue(flat: dict[str, Any]) -> str:
    """The pack as a ``key = value`` listing — what the model is shown and cites."""
    return "\n".join(f"{key} = {format_fact(value)}" for key, value in flat.items())


def label_numbers(flat: dict[str, Any]) -> list[str]:
    """The numbers a narrative may name without citing — labels, not claims.

    The line is drawn at what the number *is*, not where it sits:

    * numbers inside fact **keys** (``yoy.MORTALITY.2023.ae``);
    * every **string** value — product codes, classifications, office and
      hospital ids, and the band/period labels that carry digits
      (``"70-74"``, ``"2016-2023"``, ``"duration 6-10"``). A string is a name;
    * a **numeric** value under a field named for a year (``year``,
      ``years_used``, ``study_years``), since a calendar year is a label that
      happens to be stored as an integer.

    Everything else — every ratio, count and amount — is a claim and must be
    cited. That is what keeps the allowed set small enough for the check to
    mean something (M-12).
    """
    found: list[str] = []
    for key, value in flat.items():
        found.extend(_NUMBER_IN_KEY_RE.findall(key))
        if isinstance(value, str):
            found.append(value)
        elif _is_number(value) and _is_year_field(key):
            found.append(str(value))
    return found


def _is_year_field(key: str) -> bool:
    """True when the key's final segment names a year (``...year``/``...years``)."""
    leaf = key.rsplit(".", 1)[-1].split("[", 1)[0]
    return leaf.endswith("year") or leaf.endswith("years")


def resolve_fact_slots(text: str, flat: dict[str, Any]) -> tuple[str, list[Any]]:
    """Substitute every ``{{fact:key}}``; return the text and the injected values.

    Raises ``FactSlotError`` listing every unknown key — all of them, so a caller
    reports one useful message rather than one key at a time.
    """
    missing = [key for key in SLOT_RE.findall(text) if key not in flat]
    if missing:
        raise FactSlotError(missing)

    injected: list[Any] = []

    def substitute(match: re.Match) -> str:
        value = flat[match.group(1)]
        injected.append(value)
        return format_fact(value)

    return SLOT_RE.sub(substitute, text), injected


def cite_facts(
    body: str,
    pack: Any,
    *,
    exclude: Iterable[str] = (),
    user_msg: str = "",
) -> tuple[str, TraceabilityResult]:
    """Resolve a draft's fact citations, then verify anything it typed by hand.

    Returns ``(rendered_text, traceability_result)``. Raises ``FactSlotError`` if
    the draft cites a key the pack does not have.

    The post-check runs against the injected values and the pack's key labels
    only — deliberately *not* the pack's values, which is exactly the set that
    made the old check pass fabricated figures (M-12).
    """
    # Imported lazily: the chatbot package imports this module, so a top-level
    # import here would close a cycle through src.ai.chatbot.__init__.
    from src.ai.chatbot.traceability import verify_traceability

    flat = flatten_facts(pack, exclude=exclude)
    rendered, injected = resolve_fact_slots(body, flat)
    allowed = {"cited": injected, "labels": label_numbers(flat)}
    return rendered, verify_traceability(rendered, result_set=allowed, user_msg=user_msg)
