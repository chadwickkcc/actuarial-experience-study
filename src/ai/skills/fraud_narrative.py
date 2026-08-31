"""Skill: draft_fraud_narrative — AI narrative over fraud-scan AGGREGATES.

Prompt-artifact pattern (§E.8): runs on any configured model via the provider
abstraction and **blocks, never repairs** when a number fails the deterministic
numeric post-check. The fact pack is assembled app-side
(``ui/fraud_logic.assemble_fraud_facts``) and carries aggregates plus
institutional entity ids (office / hospital / region) ONLY — never a
``policy_id`` or ``claimant_id`` (the PII bright line; guard-tested).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.ai.llm.base import LLMProvider
from src.ai.llm.client import complete
from src.ai.prompts import load_prompt_template
from src.ai.skills.facts import (
    FactSlotError,
    cite_facts,
    flatten_facts,
    render_fact_catalogue,
)

_EXCLUDED_FACTS = ("fraud_run_id", "study_run_id")

_TAG = "**AI-DRAFT — requires investigator review**"


def draft_fraud_narrative(
    fraud_facts: dict,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Draft the fraud-scan narrative; verify every number against the facts.

    Args:
        fraud_facts: aggregates-only fact pack (see ui/fraud_logic.py).
        cfg:         parsed llm_config.yaml.
        model_key:   configured model id.
        provider:    injected provider for offline tests.

    Returns:
        {markdown, blocked, reason?, untraceable_nums?, model, hashes}.
    """
    template = load_prompt_template("skills/fraud_narrative.md")
    call_cfg = ((cfg.get("skills") or {}).get("fraud_narrative") or {})
    max_tokens = int(call_cfg.get("max_tokens", 2048))
    temperature = float(call_cfg.get("temperature", 0.0))

    # Figures are cited by key from a flat catalogue, never written by the model
    # (M-12); run ids are withheld so their digits cannot widen the allowed set.
    flat = flatten_facts(fraud_facts, exclude=_EXCLUDED_FACTS)
    messages = [{"role": "user", "content": render_fact_catalogue(flat)}]
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=temperature, system=template.text, provider=provider,
    )
    body = (response.text or "").strip()
    hashes = {template.name: template.sha256}

    if not body:
        return {
            "markdown": "", "blocked": True,
            "reason": "The model returned an empty narrative (not repaired).",
            "model": model_key, "hashes": hashes,
        }

    try:
        body, trace = cite_facts(body, fraud_facts, exclude=_EXCLUDED_FACTS)
    except FactSlotError as exc:
        return {
            "markdown": "", "blocked": True,
            "reason": (f"{exc} — the narrative cited a figure that does not exist "
                       f"in the results, so it was blocked (not repaired)."),
            "untraceable_nums": exc.keys,
            "model": model_key, "hashes": hashes,
        }
    if not trace.passed:
        return {
            "markdown": "", "blocked": True,
            "reason": "Draft blocked — a figure was typed rather than cited (every number must be a {{fact:<key>}} citation).",
            "untraceable_nums": trace.untraceable_nums,
            "model": model_key, "hashes": hashes,
        }

    footer = f"\n\n---\n*Drafted by {model_key} on {date.today().isoformat()}.*"
    return {
        "markdown": f"{_TAG}\n\n{body}{footer}",
        "blocked": False,
        "model": model_key,
        "hashes": hashes,
    }
