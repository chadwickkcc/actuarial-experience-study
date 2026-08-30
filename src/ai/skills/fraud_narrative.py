"""Skill: draft_fraud_narrative — AI narrative over fraud-scan AGGREGATES.

Prompt-artifact pattern (§E.8): runs on any configured model via the provider
abstraction and **blocks, never repairs** when a number fails the deterministic
numeric post-check. The fact pack is assembled app-side
(``ui/fraud_logic.assemble_fraud_facts``) and carries aggregates plus
institutional entity ids (office / hospital / region) ONLY — never a
``policy_id`` or ``claimant_id`` (the PII bright line; guard-tested).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from src.ai.chatbot.traceability import verify_traceability
from src.ai.llm.base import LLMProvider
from src.ai.llm.client import complete
from src.ai.prompts import load_prompt_template

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

    user_msg = json.dumps(fraud_facts, indent=2, default=str)
    messages = [{"role": "user", "content": user_msg}]
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

    # Numbers must trace to the fact pack (run ids excluded from the allowed set
    # so UUID digits can never mask an invented figure).
    allowed = {k: v for k, v in fraud_facts.items()
               if k not in ("fraud_run_id", "study_run_id")}
    trace = verify_traceability(body, result_set=allowed)
    if not trace.passed:
        return {
            "markdown": "", "blocked": True,
            "reason": "Draft blocked — a number could not be traced to the scan "
                      "results (blocked, never repaired).",
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
