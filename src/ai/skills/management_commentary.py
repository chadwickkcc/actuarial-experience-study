"""Skill: draft_management_commentary — AI management commentary over the
pre-computed experience analytics (demo refresh P6).

Prompt-artifact pattern (§E.8): runs on any configured model and **blocks,
never repairs** when a number fails the deterministic post-check. Unlike the
chat commentary route, this Skill is explicitly ALLOWED to propose management
actions — the output carries the AI-DRAFT banner and is reviewed like the A/E
memo. Every figure it may quote (YoY movement, driver contributions, trend
slopes, justification metrics, movement legs) is pre-computed by
``src/analysis/commentary`` into the fact pack; the model does no arithmetic.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from src.ai.chatbot.traceability import verify_traceability
from src.ai.llm.base import LLMProvider
from src.ai.llm.client import complete
from src.ai.prompts import load_prompt_template

_TAG = "**AI-DRAFT — requires actuary review and sign-off**"


def draft_management_commentary(
    facts: dict,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Draft the management commentary; verify every number against the facts.

    Args:
        facts:     the run-wide commentary fact pack (v2: yoy/trends/
                   justification/movement keys) from
                   ``ui.skills_logic.assemble_commentary_facts``.
        cfg:       parsed llm_config.yaml.
        model_key: configured model id.
        provider:  injected provider for offline tests.

    Returns:
        {markdown, blocked, reason?, untraceable_nums?, model, hashes}.
    """
    template = load_prompt_template("skills/management_commentary.md")
    call_cfg = ((cfg.get("skills") or {}).get("management_commentary") or {})
    max_tokens = int(call_cfg.get("max_tokens", 4096))
    temperature = float(call_cfg.get("temperature", 0.0))

    user_msg = json.dumps(facts, indent=2, default=str)
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
            "reason": "The model returned an empty commentary (not repaired).",
            "model": model_key, "hashes": hashes,
        }

    # run_id excluded so its UUID digits can never mask an invented figure.
    allowed = {k: v for k, v in facts.items() if k != "run_id"}
    trace = verify_traceability(body, result_set=allowed)
    if not trace.passed:
        return {
            "markdown": "", "blocked": True,
            "reason": "Draft blocked — a number could not be traced to the "
                      "pre-computed analytics (blocked, never repaired).",
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
