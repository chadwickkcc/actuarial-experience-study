"""Skill 1 — interpret_ae_and_draft_memo (Session 19; Req §7.9, Tech Spec §E.8).

A *prompt-artifact* Skill (FR-3B-17..20): a versioned prompt template
(``config/prompts/skills/memo.md``) invoked through the provider abstraction
(§E.5) so it runs on **any** configured model — Anthropic or DeepSeek. The Skill
never computes, infers, or extrapolates numbers; the prompt instructs this and
the deterministic post-check (``verify_traceability``) enforces it: on any
untraceable number the memo is **blocked, not repaired** (FR-3B-19).

Design notes:
  * The input JSON is **app-assembled** (FR-3B-17), never typed by the user.
  * The LLM produces only the eight named components; the persistent AI-DRAFT tag
    and the generation footer (model · date · run_id) are appended by the Skill
    after the traceability check, so the footer's date/run_id never risk a false
    block (and the eight headers are named, with no leading digits).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from src.ai.chatbot.traceability import verify_traceability
from src.ai.llm.base import LLMProvider
from src.ai.llm.client import complete
from src.ai.prompts import load_prompt_template

_TEMPLATE_NAME = "skills/memo.md"
_AI_DRAFT_TAG = "AI-DRAFT — requires actuary review and sign-off"


def interpret_ae_and_draft_memo(
    memo_input: dict,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Draft an A/E experience-study memo from an app-assembled input JSON.

    Args:
        memo_input: structured input (FR-3B-17) — product, study period, A/E
            ratios by segment, prior assumption, credibility, TEV baseline and
            ΔTEV vs prior, top drivers, envelope output (if run), exclusions,
            run_id.
        cfg: parsed ``llm_config.yaml`` (carries the ``skills.memo`` call params).
        model_key: the configured model id to run on.
        provider: optional injected provider (e.g. ``MockProvider`` in tests);
            forwarded to ``complete``.

    Returns:
        On success ``{"markdown", "blocked": False, "model", "hashes"}``; on a
        traceability failure ``{"markdown": "", "blocked": True, "reason",
        "untraceable_nums", "hashes", "model"}``.
    """
    tpl = load_prompt_template(_TEMPLATE_NAME)
    params = (cfg.get("skills", {}) or {}).get("memo", {})
    max_tokens = int(params.get("max_tokens", 2000))
    temperature = float(params.get("temperature", 0.0))

    messages = [{"role": "user", "content": json.dumps(memo_input, sort_keys=True)}]
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=temperature, system=tpl.text, provider=provider,
    )

    body = response.text or ""
    hashes = {tpl.name: tpl.sha256}

    # Empty completion guard: a model can return no content (e.g. a reasoning
    # model that exhausts max_tokens on reasoning and emits empty content). An
    # empty body trivially "passes" traceability (no numbers), which would
    # otherwise yield a tag+footer-only memo. Block loudly instead (FR-3B-19).
    if not body.strip():
        return {
            "markdown": "",
            "blocked": True,
            "reason": (
                "The model returned an empty response — no memo was generated. "
                "Increase the skill max_tokens or try a different model."
            ),
            "untraceable_nums": [],
            "model": response.model,
            "hashes": hashes,
        }

    # Exclude identifier fields (run_id) from the allowed-number set: a UUID's
    # digit-runs would otherwise widen the set and could mask an invented figure.
    # run_id is metadata used only for the footer, never a body metric.
    traceable_input = {k: v for k, v in memo_input.items() if k != "run_id"}
    trace = verify_traceability(body, result_set=traceable_input)
    if not trace.passed:
        return {
            "markdown": "",
            "blocked": True,
            "reason": "Numeric traceability failed — memo blocked (not repaired).",
            "untraceable_nums": trace.untraceable_nums,
            "model": response.model,
            "hashes": hashes,
        }

    footer = (
        f"\n\n---\n_AI-generated draft · model: {response.model} · "
        f"date: {date.today().isoformat()} · run_id: {memo_input.get('run_id', 'N/A')}_"
    )
    markdown = f"{_AI_DRAFT_TAG}\n\n{body.strip()}{footer}"
    return {
        "markdown": markdown,
        "blocked": False,
        "model": response.model,
        "hashes": hashes,
    }
