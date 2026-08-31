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

_EXCLUDED_FACTS = ("run_id",)

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

    # Figures are cited by key from a flat catalogue, never written by the model
    # (M-12); run ids are withheld so their digits cannot widen the allowed set.
    flat = flatten_facts(facts, exclude=_EXCLUDED_FACTS)
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
            "reason": "The model returned an empty commentary (not repaired).",
            "model": model_key, "hashes": hashes,
        }

    try:
        body, trace = cite_facts(body, facts, exclude=_EXCLUDED_FACTS)
    except FactSlotError as exc:
        return {
            "markdown": "", "blocked": True,
            "reason": (f"{exc} — the commentary cited a figure that does not exist "
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
