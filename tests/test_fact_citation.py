"""Fact-reference citation for the generate-then-verify Skills (M-12).

The old guarantee was set membership: "does this number appear *somewhere* in the
supporting data?" — never "is this the value of the thing the sentence claims".
With ~2,500 flattened facts and A/E ratios clustered in [0, 2] at two decimals,
almost any fabricated ratio collided with an unrelated product's figure. These
tests lock the replacement: the model cites a key, the application substitutes
the value, and only labels may be named without citation.
"""

from __future__ import annotations

import random

import pytest

from src.ai.skills.facts import (
    FactSlotError,
    cite_facts,
    flatten_facts,
    label_numbers,
    render_fact_catalogue,
    resolve_fact_slots,
)

_PACK = {
    "run_id": "3f883e90-5808",
    "study_years": [2022, 2023],
    "by_product": [
        {"product": "WL", "decrements": {"MORTALITY": {"overall": {
            "ae_ratio": 0.6561, "actual": 611, "expected": 931.2559,
        }}}},
        {"product": "TERM", "decrements": {"MORTALITY": {"overall": {
            "ae_ratio": 0.9368, "actual": 84, "expected": 89.6712,
        }}}},
    ],
    "top_driver": {"segment": "70-74", "contribution": 0.034},
}

_WL_AE = "by_product[0].decrements.MORTALITY.overall.ae_ratio"


class TestFlattenAndCatalogue:
    def test_nesting_lists_and_exclusions(self):
        flat = flatten_facts(_PACK, exclude=("run_id",))
        assert flat[_WL_AE] == pytest.approx(0.6561)
        assert flat["by_product[1].product"] == "TERM"
        assert flat["study_years[0]"] == 2022
        assert not any(k.startswith("run_id") for k in flat)

    def test_catalogue_is_key_equals_value(self):
        text = render_fact_catalogue(flatten_facts(_PACK, exclude=("run_id",)))
        assert f"{_WL_AE} = 0.6561" in text
        assert "by_product[0].decrements.MORTALITY.overall.actual = 611" in text
        assert "3f883e90" not in text          # the run id is withheld entirely


class TestResolution:
    def test_slots_are_substituted_and_reported(self):
        text, injected = resolve_fact_slots(
            f"A/E was {{{{fact:{_WL_AE}}}}}.", flatten_facts(_PACK)
        )
        assert text == "A/E was 0.6561."
        assert injected == [pytest.approx(0.6561)]

    def test_unknown_key_raises_listing_every_miss(self):
        with pytest.raises(FactSlotError) as exc:
            resolve_fact_slots("{{fact:no.such.key}} and {{fact:also.missing}}",
                               flatten_facts(_PACK))
        assert set(exc.value.keys) == {"no.such.key", "also.missing"}


class TestTheGuarantee:
    def test_a_cited_figure_passes(self):
        text, trace = cite_facts(f"WL mortality A/E was {{{{fact:{_WL_AE}}}}}.",
                                 _PACK, exclude=("run_id",))
        assert trace.passed
        assert "0.6561" in text

    def test_typing_a_figure_blocks_even_when_it_is_correct(self):
        """The value is right, but it was not cited — so it is not verified."""
        _, trace = cite_facts("WL mortality A/E was 0.6561.", _PACK, exclude=("run_id",))
        assert not trace.passed
        assert trace.untraceable_nums == ["0.6561"]

    def test_a_figure_belonging_to_another_product_no_longer_passes(self):
        """The exact M-12 failure: Term's ratio asserted of Whole Life.

        Under set-membership this passed — 0.9368 *is* in the pack. It is simply
        not what the sentence claims, which the old check could not see.
        """
        _, trace = cite_facts("WL mortality A/E was 0.9368.", _PACK, exclude=("run_id",))
        assert not trace.passed

    def test_labels_may_be_named_without_citation(self):
        """Years, bands, codes and other names are labels, not claims."""
        _, trace = cite_facts(
            f"In 2023 the 70-74 band led WL, whose A/E was {{{{fact:{_WL_AE}}}}}.",
            _PACK, exclude=("run_id",),
        )
        assert trace.passed

    def test_run_id_digits_are_not_labels(self):
        _, trace = cite_facts("A stray 3f883e90 style 883 appears.", _PACK,
                              exclude=("run_id",))
        assert not trace.passed

    def test_label_numbers_covers_strings_and_year_fields(self):
        labels = label_numbers(flatten_facts(_PACK, exclude=("run_id",)))
        joined = " ".join(labels)
        assert "70-74" in joined          # a string value is a name
        assert "2023" in joined           # a year field is a name
        assert "611" not in labels        # a count is a claim


def test_fabricated_ratios_are_no_longer_absorbed_by_the_pack(prod_db, prod_run_id):
    """The measured claim behind M-12, on the real fact pack.

    Random two-decimal ratios in [0, 2] — the shape of an invented A/E — passed
    the set-membership check the overwhelming majority of the time. Citation
    leaves only the label numbers, and a ratio almost never collides with one.
    """
    from src.ai.chatbot.traceability import verify_traceability
    from ui.skills_logic import assemble_commentary_facts

    facts = assemble_commentary_facts(prod_db, prod_run_id)
    old_allowed = {k: v for k, v in facts.items() if k != "run_id"}

    rng = random.Random(42)
    trials = 400
    old_pass = new_pass = 0
    for _ in range(trials):
        text = f"Mortality experience came in at {rng.uniform(0, 2):.2f}."
        if verify_traceability(text, result_set=old_allowed).passed:
            old_pass += 1
        if cite_facts(text, facts, exclude=("run_id",))[1].passed:
            new_pass += 1

    assert old_pass / trials > 0.5, "the old check should absorb most fabrications"
    assert new_pass / trials < 0.05, (
        f"citation should leave almost nothing absorbable "
        f"(old {old_pass / trials:.1%}, new {new_pass / trials:.1%})"
    )
