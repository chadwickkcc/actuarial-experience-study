"""Real-data spot-check for the two Skills (Session 19; skip-if-absent prod DB).

Assembles a memo input from the production Gold run and a representative SHAP
cell, runs **both** Skills end-to-end through an injected zero-network provider
(MockProvider-equivalent), and confirms the block-not-repair path fires on a
corrupted number. Uses a copy of the production DB (never the real file); the copy
predates the AI tables, so ``init_database`` is run on it first.
"""
from __future__ import annotations

from pathlib import Path

from src.ai.llm.client import load_llm_config
from src.ai.skills.memo import interpret_ae_and_draft_memo
from src.ai.skills.shap_explain import explain_shap_results
from src.utils.types import DecrementType, LLMResponse
from src.utils.db_init import init_database
from ui.config import CONFIG_DIR
from ui import skills_logic as skills
from ui import ai_comparison_logic as logic

class _Stub:
    name = "stub"

    def __init__(self, text):
        self._text = text

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        return LLMResponse(self._text, 5, 10, self.name, model, 0.0, "end_turn")


def _cfg():
    return load_llm_config(CONFIG_DIR / "llm_config.yaml")


def test_memo_skill_end_to_end_on_real_gold(prod_db: Path, prod_run_id: str):
    init_database(str(prod_db))  # add AI tables to the copy (idempotent)
    memo_input = skills.assemble_memo_input(prod_db, prod_run_id, DecrementType.MORTALITY, "WL")
    assert memo_input["product"] == "WL"
    assert memo_input["study_period"] != "N/A"

    # A clean body that quotes only a number present in the assembled input.
    clean = f"The study period was {memo_input['study_period']}."
    out = interpret_ae_and_draft_memo(
        memo_input, _cfg(), "claude-sonnet-4-6", provider=_Stub(clean)
    )
    assert out["blocked"] is False
    assert out["markdown"].startswith("AI-DRAFT")

    # Corrupted body with an invented number → blocked, not repaired.
    out_bad = interpret_ae_and_draft_memo(
        memo_input, _cfg(), "claude-sonnet-4-6",
        provider=_Stub(clean + " Mortality moved 999.99."),
    )
    assert out_bad["blocked"] is True
    assert not out_bad.get("markdown")


def test_commentary_facts_include_proposed_factors(prod_db: Path, prod_run_id: str):
    """Fix 2: the commentary fact pack carries the materialised GLM proposals so the
    chatbot's 'commentary on the proposed assumptions' request can be grounded."""
    init_database(str(prod_db))  # ensure AI tables present (idempotent)
    facts = skills.assemble_commentary_facts(prod_db, prod_run_id)
    assert facts is not None
    wl = next((p for p in facts["by_product"] if p["product"] == "WL"), None)
    assert wl is not None
    mort = wl["decrements"].get("MORTALITY")
    assert mort is not None
    proposed = mort.get("proposed_factors")
    assert proposed, "WL mortality GLM proposals should be in the fact pack"
    one = proposed[0]
    assert {"grain", "proposed_factor", "credibility_z", "low_credibility"} <= set(one)
    # The degenerate sparse cells (e.g. 25-29, Z 0.0) are flagged low-credibility.
    assert any(p["low_credibility"] for p in proposed)


def test_memo_drivers_exclude_zero_credibility_bands(prod_db: Path, prod_run_id: str):
    """Fix 4b: zero-experience bands (A/E 0.0, Z 0.0) are not 'principal drivers'."""
    init_database(str(prod_db))
    memo_input = skills.assemble_memo_input(
        prod_db, prod_run_id, DecrementType.MORTALITY, "TERM"
    )
    seg_z = {s["segment"]: s["credibility_z"] for s in memo_input["ae_by_segment"]}
    # Every reported driver must be a band that actually carries experience.
    for driver in memo_input["top_drivers"]:
        assert seg_z.get(driver, 0) > 0


def test_shap_skill_end_to_end_on_real_feature_map(prod_db: Path):
    fmap = skills.feature_map_for_decrement(
        logic.load_feature_to_assumption(), DecrementType.MORTALITY
    )
    cell = {
        "decrement": "MORTALITY",
        "product_code": "WL",
        "grain_key": {"product": "WL", "duration_band": "6-10"},
        "base_value": 0.0,
        "prediction": 0.07,
        "contributions": [
            {"feature": "duration_band", "shap_value": 0.12, "feature_value": "6-10"},
        ],
    }
    clean = "Base value 0.0 reached prediction 0.07; policy duration added 0.12."
    out = explain_shap_results(cell, fmap, _cfg(), "deepseek-v4-flash", provider=_Stub(clean))
    assert out["blocked"] is False
    assert "duration_band" not in out["markdown"]  # raw name never leaks

    out_bad = explain_shap_results(
        cell, fmap, _cfg(), "deepseek-v4-flash", provider=_Stub(clean + " Also 0.99.")
    )
    assert out_bad["blocked"] is True
