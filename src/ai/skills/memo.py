"""Skill 1 — interpret_ae_and_draft_memo (Session 19; Req §7.9, Tech Spec §E.8).

A *prompt-artifact* Skill (FR-3B-17..20): a versioned prompt template
(``config/prompts/skills/memo.md``) invoked through the provider abstraction
(§E.5) so it runs on **any** configured model — Anthropic or DeepSeek. The Skill
never computes, infers, or extrapolates numbers — since M-12 it does not write
them at all: the model cites each figure as ``{{fact:<key>}}`` and the
application substitutes it from the app-assembled pack. A typed digit or an
unknown key leaves the memo **blocked, not repaired** (FR-3B-19).

Design notes:
  * The input JSON is **app-assembled** (FR-3B-17), never typed by the user.
  * The LLM produces only the seven named components; the persistent AI-DRAFT tag
    and the generation footer (model · date · run_id) are appended by the Skill
    after the traceability check, so the footer's date/run_id never risk a false
    block (and the seven headers are named, with no leading digits).
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

_TEMPLATE_NAME = "skills/memo.md"
_AI_DRAFT_TAG = "AI-DRAFT — requires actuary review and sign-off"

#: Withheld from the catalogue: a UUID's digit-runs would widen the allowed set
#: and could mask an invented figure. run_id is footer metadata, never a metric.
_EXCLUDED_FACTS = ("run_id",)


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
            ratios by segment, prior assumption, credibility, top drivers,
            exclusions,
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

    # The model is shown the pack as a flat `key = value` catalogue and cites
    # figures by key; it never writes one (M-12). `run_id` is withheld entirely.
    flat = flatten_facts(memo_input, exclude=_EXCLUDED_FACTS)
    messages = [{"role": "user", "content": render_fact_catalogue(flat)}]
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

    try:
        body, trace = cite_facts(body, memo_input, exclude=_EXCLUDED_FACTS)
    except FactSlotError as exc:
        return {
            "markdown": "",
            "blocked": True,
            "reason": (
                f"{exc} — the memo cited a figure that does not exist in the study "
                f"results, so it was blocked (not repaired)."
            ),
            "untraceable_nums": exc.keys,
            "model": response.model,
            "hashes": hashes,
        }
    if not trace.passed:
        return {
            "markdown": "",
            "blocked": True,
            "reason": (
                "Numeric traceability failed — memo blocked (not repaired). Every "
                "figure must be cited as {{fact:<key>}}, never typed."
            ),
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
