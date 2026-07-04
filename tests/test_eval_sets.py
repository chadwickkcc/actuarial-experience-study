"""Eval-set shape + disjointness tests (Session 22; FR-3B-48/49/50, FR-3B-30).

These validate the *structure* of the locked eval sets and prove the golden set
is disjoint from the chatbot few-shots (no question may appear in both). They run
offline and are part of the standard regression gate (the live baseline run is
separate and owner-triggered).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from src.ai.eval.runner import load_adversarial, load_golden
from src.utils.sql_boundary import load_allowlist, validate_select
from src.utils.types import SQLGateOutcome

_GOLDEN = Path("tests/eval/golden_set.yaml")
_ADVERSARIAL = Path("tests/eval/adversarial_set.yaml")
_FEW_SHOTS = Path("config/chatbot_few_shots.yaml")
_AI_CONFIG = Path("config/ai_config.yaml")

_VALID_INTENTS = {"FACTUAL_LOOKUP", "EXPLORATORY", "COMMENTARY_GENERATION", "OUT_OF_SCOPE"}
_VALID_EXPECTS = {"gate_reject", "refusal"}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (for disjointness)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


def test_golden_set_shape_and_count():
    golden = load_golden(_GOLDEN)
    assert 30 <= len(golden) <= 50, f"golden set must hold 30-50 entries, got {len(golden)}"
    ids = [e["id"] for e in golden]
    assert len(ids) == len(set(ids)), "duplicate golden ids"
    for entry in golden:
        assert entry["question"].strip()
        assert entry["intent"] in _VALID_INTENTS
        assert entry["sql"].strip()
        er = entry["expected_result"]
        assert isinstance(er["columns"], list) and er["columns"]
        assert isinstance(er["row_count"], int)
        assert isinstance(er["value_check"], bool)


def test_adversarial_set_shape_and_count():
    adversarial = load_adversarial(_ADVERSARIAL)
    assert 10 <= len(adversarial) <= 15, f"adversarial set must hold 10-15, got {len(adversarial)}"
    ids = [e["id"] for e in adversarial]
    assert len(ids) == len(set(ids)), "duplicate adversarial ids"
    for entry in adversarial:
        assert entry["question"].strip()
        assert entry["expect"] in _VALID_EXPECTS
    # Coverage: both expectation kinds present.
    expects = {e["expect"] for e in adversarial}
    assert expects == _VALID_EXPECTS


def test_golden_covers_all_five_product_families():
    sql_blob = " ".join(e["sql"] for e in load_golden(_GOLDEN))
    assert "TERM" in sql_blob
    assert "WL" in sql_blob
    assert ("UL" in sql_blob) or ("ULSG" in sql_blob)
    assert "VUL" in sql_blob
    assert ("DA_FIXED" in sql_blob) or ("DA_VA" in sql_blob)
    # And the TEV table is exercised (TEV query class).
    assert "gold_tev_results" in sql_blob


def test_golden_set_disjoint_from_few_shots():
    """FR-3B-30/49: no question appears in both the few-shots and the golden set."""
    with _FEW_SHOTS.open("r", encoding="utf-8") as fh:
        few = yaml.safe_load(fh) or {}
    few_questions = {_normalize(p["question"]) for p in few.get("few_shots", [])}
    golden_questions = {_normalize(e["question"]) for e in load_golden(_GOLDEN)}
    overlap = few_questions & golden_questions
    assert overlap == set(), f"golden/few-shot overlap (FR-3B-49): {overlap}"


def test_every_golden_sql_passes_the_validation_gates():
    """Each reference query must be gate-compliant against the shared allowlist."""
    allowlist = load_allowlist(_AI_CONFIG)
    for entry in load_golden(_GOLDEN):
        result = validate_select(entry["sql"], allowlist, row_cap=500)
        assert result.outcome is SQLGateOutcome.PASS, (
            f"{entry['id']} reference SQL not gate-compliant: "
            f"{result.outcome} ({result.gate_failed})"
        )
