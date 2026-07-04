"""Skill 2 — explain_shap_results (Session 19; Req §7.9, Tech Spec §E.8).

A prompt-artifact Skill (FR-3B-21..23) that turns one persisted SHAP cell
(§D.6) into a 2–3 paragraph plain-English explanation for a Chief Actuary,
running on any configured model via the provider abstraction.

Guardrails (FR-3B-22):
  * Feature names appear **only** in their mapped actuarial language — the Skill
    translates every raw covariate to its ``actuarial_term`` (FR-3A-39) *before*
    building the prompt, so the LLM never sees a raw feature/one-hot name.
  * No causal claims, no recommendations (the prompt instructs this).
  * Every quoted number passes ``verify_traceability`` against the input SHAP
    cell; on failure the explanation is **blocked, not repaired**.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from src.ai.chatbot.traceability import verify_traceability
from src.ai.llm.base import LLMProvider
from src.ai.llm.client import complete
from src.ai.prompts import load_prompt_template

_TEMPLATE_NAME = "skills/shap_explain.md"
_AI_DRAFT_TAG = "AI-DRAFT — requires actuary review and sign-off"


def _translate_cell(shap_cell_json: dict, feature_to_assumption: dict) -> dict:
    """Return an LLM-facing view with raw covariates replaced by actuarial terms.

    Raw feature names are mapped to ``actuarial_term``; an unmapped feature falls
    back to a readable form of its own name so no internal one-hot string leaks.
    """
    def _term(feature: str) -> str:
        mapping = feature_to_assumption.get(feature, {})
        return mapping.get("actuarial_term") or feature.replace("_", " ")

    translated = []
    for c in shap_cell_json.get("contributions", []):
        feature = c.get("feature", "")
        mapping = feature_to_assumption.get(feature, {})
        translated.append({
            "factor": _term(feature),
            "assumption_dimension": mapping.get("assumption_dimension", ""),
            "shap_value": c.get("shap_value"),
            "feature_value": c.get("feature_value"),
        })
    # Translate the grain-key dimension names too, so no raw model feature /
    # one-hot column name reaches the LLM via the segment identifier (FR-3B-22).
    segment = {_term(k): v for k, v in (shap_cell_json.get("grain_key") or {}).items()}
    return {
        "product_code": shap_cell_json.get("product_code"),
        "decrement": shap_cell_json.get("decrement"),
        "segment": segment,
        "base_value": shap_cell_json.get("base_value"),
        "prediction": shap_cell_json.get("prediction"),
        "contributions": translated,
    }


def explain_shap_results(
    shap_cell_json: dict,
    feature_to_assumption: dict,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Explain one SHAP cell in plain actuarial English.

    Args:
        shap_cell_json: the persisted SHAP cell for one grain (§D.6) plus its
            decrement/product context.
        feature_to_assumption: the decrement's feature→actuarial map (FR-3A-39).
        cfg: parsed ``llm_config.yaml`` (carries ``skills.shap_explain`` params).
        model_key: configured model id to run on.
        provider: optional injected provider (tests); forwarded to ``complete``.

    Returns:
        ``{"markdown", "blocked", "model", "hashes"}`` (plus ``reason`` /
        ``untraceable_nums`` when blocked).
    """
    tpl = load_prompt_template(_TEMPLATE_NAME)
    params = (cfg.get("skills", {}) or {}).get("shap_explain", {})
    max_tokens = int(params.get("max_tokens", 800))
    temperature = float(params.get("temperature", 0.0))

    view = _translate_cell(shap_cell_json, feature_to_assumption)
    messages = [{"role": "user", "content": json.dumps(view, sort_keys=True)}]
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=temperature, system=tpl.text, provider=provider,
    )

    body = response.text or ""
    hashes = {tpl.name: tpl.sha256}

    # Empty completion guard (FR-3B-19): an empty body trivially passes
    # traceability and would yield a tag+footer-only explanation. Block loudly.
    if not body.strip():
        return {
            "markdown": "",
            "blocked": True,
            "reason": (
                "The model returned an empty response — no explanation was "
                "generated. Increase the skill max_tokens or try a different model."
            ),
            "untraceable_nums": [],
            "model": response.model,
            "hashes": hashes,
        }

    # Numbers must trace to the original SHAP cell (base/prediction/shap values).
    # Exclude identifier fields (model_id) — a UUID's digit-runs would otherwise
    # widen the allowed set and could mask an invented number.
    traceable_cell = {k: v for k, v in shap_cell_json.items() if k != "model_id"}
    trace = verify_traceability(body, result_set=traceable_cell)
    if not trace.passed:
        return {
            "markdown": "",
            "blocked": True,
            "reason": "Numeric traceability failed — explanation blocked (not repaired).",
            "untraceable_nums": trace.untraceable_nums,
            "model": response.model,
            "hashes": hashes,
        }

    footer = (
        f"\n\n---\n_AI-generated draft · model: {response.model} · "
        f"date: {date.today().isoformat()}_"
    )
    markdown = f"{_AI_DRAFT_TAG}\n\n{body.strip()}{footer}"
    return {
        "markdown": markdown,
        "blocked": False,
        "model": response.model,
        "hashes": hashes,
    }
