"""Guarded chatbot pipeline (Session 20/21; Req §7.10, Tech Spec v2.0.1 §E.7).

The conversational pipeline that lets an actuary interrogate Gold study results in
natural language **without** the LLM ever gaining write access, ungoverned data
access, or the ability to invent a number:

    user message
      → classify_intent          (one lightweight LLM call; logged first)
      → [factual / exploratory]  → generate SQL + answer template (one LLM call)
      → [commentary]             → RAG grounding + commentary draft (Session 21)
      → validate_sql             (gates 1-4 via the hardened boundary)
      → execute_via_mcp          (read-only, server re-enforces gates 1-5)
      → fill_numeric_slots       (numbers filled programmatically, never by LLM)
      → assemble_response        (+ exposure & credibility context; AI-draft banner)
      → verify_traceability      (mandatory; block-not-repair)
      → [commentary] optional faithfulness judge (flag, never block)
      → ChatTurnResult           (+ a per-turn gold_ai_audit_log row, Session 21)

Data access is **exclusively** through the MCP client (FR-3B-25); every LLM call
goes through the provider abstraction with the user-selected model (FR-3B-26).
Rejected SQL is never silently rewritten (FR-3B-31). The numeric post-check is
the existing, unmodified Session-19 ``verify_traceability`` (FR-3B-34).

§E.7 reconciliation (owner-confirmed): the SQL-generation step returns a JSON
``{"sql", "answer_template"}`` in one call, so ``generate_sql(...) -> str`` (the
§E.7 signature) is the thin wrapper returning ``plan["sql"]``. The answer template
uses the fixed placeholder grammar and is filled by ``fill_numeric_slots``. The
Session-21 commentary route reuses the same JSON contract + slot/traceability
regime (FR-3B-37) on top of RAG grounding (``commentary.md``); ``handle_turn`` is a
thin audit-emitting wrapper over ``_run_turn`` (FR-3B-47), keeping the pipeline
itself DB-free (the audit sink performs the only write).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

import sqlglot
import yaml
from sqlglot import exp

from src.ai.chatbot.context import assemble_rag_context, trim_history
from src.ai.chatbot.mcp_client import MCPClient
from src.ai.mcp_server.server import QUERYABLE_TABLES as _QUERYABLE_TABLES
from src.ai.chatbot.session import SessionState, call_cost, model_prices, record_call
from src.ai.chatbot.traceability import verify_traceability
from src.ai.llm.base import LLMProvider, LLMProviderError
from src.ai.llm.client import complete
from src.ai.prompts import load_prompt_template
from src.calculation.ae_engine import compute_credibility_z
from src.utils.sql_boundary import validate_select
from src.utils.types import (
    ChatTurnResult,
    IntentLabel,
    LLMResponse,
    SQLGateOutcome,
    SQLValidationResult,
)

# Versioned prompt templates (config/prompts/), hashed for the audit log.
_ROUTING_TEMPLATE = "routing.md"
_SQLGEN_TEMPLATE = "sql_generation.md"
_COMMENTARY_TEMPLATE = "commentary.md"
_FAITHFULNESS_TEMPLATE = "faithfulness_judge.md"
_SYNTH_PLAN_TEMPLATE = "synthesis_plan.md"        # round 3 Phase D
_SYNTH_ANSWER_TEMPLATE = "synthesis_answer.md"    # round 3 Phase D

# Per-call token shaping (NOT guardrail thresholds). These are the defaults used
# when ``chatbot.max_tokens`` is absent from ai_config.yaml; the config overrides
# them (NFR-CF-10). Sized with headroom for reasoning models (e.g. DeepSeek V4),
# whose hidden reasoning tokens count against this budget — too small a cap
# returns an empty completion that the provider rightly surfaces as an error.
_ROUTING_MAX_TOKENS = 1024
_SQLGEN_MAX_TOKENS = 1536
_COMMENTARY_MAX_TOKENS = 2048
_FAITHFULNESS_MAX_TOKENS = 64

#: Maps the ``chatbot.max_tokens`` config keys to their default cap.
_SYNTHESIS_MAX_TOKENS = 4096
_DEFAULT_MAX_TOKENS = {
    "routing": _ROUTING_MAX_TOKENS,
    "sql_generation": _SQLGEN_MAX_TOKENS,
    "commentary": _COMMENTARY_MAX_TOKENS,
    "faithfulness": _FAITHFULNESS_MAX_TOKENS,
    "synthesis": _SYNTHESIS_MAX_TOKENS,
}


def _call_max_tokens(chatbot_cfg: Optional[dict], key: str) -> int:
    """Per-call output token cap from ``chatbot.max_tokens.<key>`` (or default)."""
    block = (chatbot_cfg or {}).get("max_tokens", {}) or {}
    value = block.get(key)
    return int(value) if value is not None else int(_DEFAULT_MAX_TOKENS[key])

# Persistent AI-draft banner on commentary (FR-3B-38) — survives export because it
# is part of response_text. Number-free, applied before the traceability check.
_AI_DRAFT_BANNER = "**AI-drafted — pending actuary review.**\n\n"
_FAITHFULNESS_WARNING = (
    "\n\n_Low faithfulness — review carefully (judge score {score}/5)._"
)
# Analyst-mode (opt-in) advisory when a number fails the traceability post-check
# but is rendered anyway (flag-not-block). Number-free except the echoed tokens,
# which are by definition already in the text.
_UNVERIFIED_WARNING = (
    "\n\n⚠ **Analyst mode** — this answer may contain unverified figures the tool "
    "could not trace to the database; review carefully: {nums}."
)

# Defaults mirror config/ai_config.yaml §F.1 (used when chatbot_cfg omits a key).
_DEFAULT_TOKEN_BUDGET = 1_000_000
_DEFAULT_WARN_FRACTION = 0.8
_DEFAULT_MAX_TURNS = 30
_DEFAULT_TOKEN_WINDOW = 16_000
_DEFAULT_ROW_CAP = 500

# Number-free templated messages (so they never affect numeric traceability).
_REFUSAL_TEXT = (
    "I can only answer questions about the loaded experience-study results "
    "(A/E ratios, exposure, credibility, and TEV figures). I can't access personal "
    "data, answer unrelated questions, or change assumptions or any data."
)
_BUDGET_STOP_TEXT = (
    "This session has reached its token budget. Please start a fresh session to "
    "continue — no answer was degraded or truncated to fit."
)
_MAX_TURNS_TEXT = (
    "This conversation has reached its turn limit. Please start a fresh session to "
    "continue."
)
_BUDGET_WARNING_PREFIX = (
    "Heads up: this session is approaching its token budget; consider starting a "
    "fresh session soon.\n\n"
)
_SAFE_FAILURE_TEXT = (
    "I couldn't answer that safely from the available results. Please try "
    "rephrasing, or ask for a specific figure or breakdown."
)
_LLM_ERROR_TEXT = (
    "The model's reply was interrupted before it finished (often a token-limit "
    "truncation on a reasoning model). Please try again, or narrow the scope of "
    "the question."
)
# Actionable hints for non-security blocks. Security/guardrail blocks (SQL-gate
# rejections, numeric-traceability) deliberately keep the generic _SAFE_FAILURE_TEXT
# so they never hint at how to get around a gate.
_SLOT_FILL_HINT_TEXT = (
    "I couldn't fit that into a single answer — it looks like a multi-row result. "
    "Ask for it as a table (e.g. \"... as a table\"), request a specific figure or "
    "breakdown, or turn on Deep analysis for multi-part questions."
)
_NO_DATA_HINT_TEXT = (
    "I couldn't find matching data for that. Try naming a specific product or "
    "decrement (mortality, lapse, surrender, CI), or rephrasing the question."
)
_COMMENTARY_FAIL_HINT_TEXT = (
    "I couldn't draft that commentary from the available results. Try naming the "
    "product and decrement, or ask for a specific figure or breakdown."
)
_CREDIBILITY_AGG_HINT_TEXT = (
    "Credibility (and standard error) can't be averaged or summed across cells — "
    "those are per-cell statistics. Ask for the overall A/E (e.g. \"overall UL "
    "lapse A/E\") and I'll report its credibility automatically."
)


class SlotFillError(Exception):
    """Raised when an answer template has an unresolved or malformed slot.

    The turn is blocked (not rendered with a gap) — the same block-not-repair
    discipline as the numeric post-check (FR-3B-33).
    """


# --------------------------------------------------------------------------- #
# Stage 1 — intent routing (FR-3B-27/28)                                       #
# --------------------------------------------------------------------------- #

_INTENT_RE = re.compile(r"INTENT:\s*([A-Z_]+)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE)


def _parse_intent(text: str) -> tuple[IntentLabel, str]:
    """Parse the router's strict two-line output; default to OUT_OF_SCOPE."""
    intent = IntentLabel.OUT_OF_SCOPE
    match = _INTENT_RE.search(text or "")
    if match:
        token = match.group(1).strip().upper()
        try:
            intent = IntentLabel(token)
        except ValueError:
            intent = IntentLabel.OUT_OF_SCOPE
    reason_match = _REASON_RE.search(text or "")
    reason = reason_match.group(1).strip() if reason_match else ""
    return intent, reason


def _intent_parsed(text: str) -> bool:
    """True iff the reply carries a valid INTENT token, so no re-route is needed.

    The bare default in ``_parse_intent`` is OUT_OF_SCOPE — but that same value is
    returned both for a genuine refusal AND for an unparseable/empty reply (a real
    risk for reasoning models that exhaust the routing token cap). This predicate
    lets ``_route`` tell the two apart and re-ask once before defaulting, so a
    legitimate data question is not silently refused (Doc-1/Doc-2 over-refusals).
    """
    match = _INTENT_RE.search(text or "")
    if not match:
        return False
    try:
        IntentLabel(match.group(1).strip().upper())
        return True
    except ValueError:
        return False


#: A stricter re-ask used once when the first routing reply is unparseable.
_ROUTE_RETRY_NUDGE = (
    "Your previous reply was not in the required format. Reply now with EXACTLY "
    "two lines and nothing else: an INTENT: line (one of FACTUAL_LOOKUP, "
    "EXPLORATORY, COMMENTARY_GENERATION, OUT_OF_SCOPE) and a REASON: line."
)


def _merge_responses(first: LLMResponse, second: LLMResponse) -> LLMResponse:
    """Combine two routing calls into one response: keep the retry's text/model,
    but sum tokens and latency so the re-route path is fully cost-accounted."""
    return LLMResponse(
        text=second.text,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        provider=second.provider,
        model=second.model,
        latency_ms=first.latency_ms + second.latency_ms,
        stop_reason=second.stop_reason,
    )


#: How many of the most-recent prior turns to show the router for context, so a
#: brief continuation ("why?", "try", "I thought WL was covered") is classified
#: against the conversation rather than in isolation (FR-3B-27/39).
_ROUTING_HISTORY_TURNS = 4


def _route(
    user_msg: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider],
    prompts_dir: Optional[Path],
    max_tokens: int = _ROUTING_MAX_TOKENS,
    history: Optional[list[dict]] = None,
    study_digest: Optional[dict] = None,
) -> tuple[IntentLabel, str, LLMResponse]:
    """One routing LLM call → (intent, reason, response).

    Recent prior turns (``history``) precede the new message so the router can
    classify a follow-up using conversation context; the new message is always
    the final ``user`` turn and the one being classified. The study digest (when
    supplied) lets the router see what the study covers so a coverage follow-up
    ("I thought WL was covered?") is classified as a data question.
    """
    tpl = (
        load_prompt_template(_ROUTING_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_ROUTING_TEMPLATE)
    )
    system = tpl.text + _render_digest(study_digest)
    messages = [
        {"role": h.get("role", "user"), "content": h.get("content", "")}
        for h in (history or [])[-_ROUTING_HISTORY_TURNS:]
    ]
    messages.append({"role": "user", "content": user_msg})
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    # Re-ask once if the reply is unparseable (empty / token-capped), so a valid
    # data question is not silently defaulted to OUT_OF_SCOPE (Doc-1/Doc-2 fix).
    if not _intent_parsed(response.text):
        try:
            retry = complete(
                cfg, model_key,
                messages + [{"role": "user", "content": _ROUTE_RETRY_NUDGE}],
                max_tokens, temperature=0.0, system=system, provider=provider,
            )
            response = _merge_responses(response, retry)
        except LLMProviderError:
            # Keep the first response; the safe OUT_OF_SCOPE default still applies.
            pass
    intent, reason = _parse_intent(response.text)
    return intent, reason, response


def classify_intent(
    user_msg: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
    prompts_dir: Optional[Path] = None,
    history: Optional[list[dict]] = None,
) -> tuple[IntentLabel, str]:
    """Classify a user message into one ``IntentLabel`` with a stated reason."""
    intent, reason, _ = _route(
        user_msg, cfg, model_key, provider=provider, prompts_dir=prompts_dir,
        history=history,
    )
    return intent, reason


# --------------------------------------------------------------------------- #
# Stage 2 — SQL + answer-template generation (FR-3B-29/33)                      #
# --------------------------------------------------------------------------- #

def load_few_shots(path: Path) -> list[dict]:
    """Load the curated Q->SQL few-shot pairs from ``chatbot_few_shots.yaml``."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    pairs = cfg.get("few_shots", []) or []
    return [{"question": p.get("question", ""), "sql": p.get("sql", "")} for p in pairs]


def _render_few_shots(few_shots: list[dict]) -> str:
    """Render few-shot pairs as grounding text appended to the SQL-gen prompt."""
    if not few_shots:
        return ""
    blocks = ["", "## Worked examples (question, then query)"]
    for pair in few_shots:
        blocks.append("")
        blocks.append("Q: " + str(pair.get("question", "")))
        blocks.append("Query: " + str(pair.get("sql", "")))
    return "\n".join(blocks)


def _parse_plan(text: str) -> Optional[dict]:
    """Extract ``{"sql", "answer_template"}`` from the model's JSON reply."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = re.sub(r"^[a-zA-Z]+\n", "", cleaned, count=1)
    try:
        obj = json.loads(cleaned)
    except (ValueError, TypeError):
        brace = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace is None:
            return None
        try:
            obj = json.loads(brace.group(0))
        except (ValueError, TypeError):
            return None
    if not isinstance(obj, dict):
        return None
    sql = obj.get("sql")
    template = obj.get("answer_template")
    if not isinstance(sql, str) or not isinstance(template, str):
        return None
    return {"sql": sql, "answer_template": template}


def _render_run_scope(run_id: Optional[str]) -> str:
    """A system-prompt suffix that scopes generated SQL to the active run.

    The text deliberately carries no SQL keyword in its literal so the
    no-interpolation guard (FR-3A-02) does not flag this prompt string — this is
    instruction prose, not SQL building (the boundary still validates every query).
    """
    if not run_id:
        return ""
    return (
        "\n\n## Active study run\n"
        "Constrain every query to this run by filtering "
        f"`study_run_id = '{run_id}'`, unless the user explicitly asks to "
        "compare runs."
    )


def _render_digest(digest: Optional[dict]) -> str:
    """A compact, display-rounded "study at a glance" block for the system prompt.

    Built from the same app-assembled fact pack used for commentary (overall A/E
    per product × decrement, study period, products, baseline TEV). Injecting it
    into the routing / SQL-gen / synthesis prompts means the model always knows
    the whole study's shape — it can answer overview, coverage and comparison
    questions directly and write better-grounded SQL — while precise figures are
    still fetched and slot-filled from the database. Every number here is
    pre-computed from the database (display-rounded), so a digest figure the model
    quotes is traceable to the data (the digest is added to the post-check's
    allowed-set on the data paths). Returns ``""`` when no digest is supplied, so
    the seam is inert for the eval harness and tests (which pass no digest).
    """
    if not digest:
        return ""
    lines = ["", "## Study at a glance (pre-computed from the database; figures are exact)"]
    period = digest.get("study_period")
    if period:
        lines.append(f"Study period: {period}.")
    products = digest.get("products") or []
    if products:
        lines.append("Products covered: " + ", ".join(str(p) for p in products) + ".")
    rows: list[str] = []
    for entry in digest.get("by_product", []) or []:
        product = entry.get("product")
        for dec, block in (entry.get("decrements") or {}).items():
            overall = (block or {}).get("overall") or {}
            ae = overall.get("ae_ratio")
            if ae is None:
                continue
            rows.append(
                f"- {product} {str(dec).lower()}: overall A/E {ae} "
                f"({overall.get('actual')} actual / {overall.get('expected')} expected), "
                f"credibility Z {overall.get('credibility_z')}."
            )
    if rows:
        lines.append("Overall A/E by product and decrement (A/E = actual ÷ expected):")
        lines.extend(rows)
    tev = digest.get("tev_baseline")
    if tev is not None:
        lines.append(f"Baseline total embedded value (TEV), all products: {tev}.")
    return "\n".join(lines)


def _generate(
    user_msg: str,
    history: list[dict],
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider],
    few_shots: Optional[list[dict]],
    prompts_dir: Optional[Path],
    max_tokens: int = _SQLGEN_MAX_TOKENS,
    run_id: Optional[str] = None,
    study_digest: Optional[dict] = None,
) -> tuple[Optional[dict], LLMResponse]:
    """One SQL-generation LLM call → (plan|None, response)."""
    tpl = (
        load_prompt_template(_SQLGEN_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_SQLGEN_TEMPLATE)
    )
    system = (
        tpl.text + _render_few_shots(few_shots or [])
        + _render_run_scope(run_id) + _render_digest(study_digest)
    )
    messages = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in history]
    messages.append({"role": "user", "content": user_msg})
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    return _parse_plan(response.text), response


def generate_query_plan(
    user_msg: str,
    history: list[dict],
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
    few_shots: Optional[list[dict]] = None,
    prompts_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Generate ``{"sql", "answer_template"}`` for a factual/exploratory question."""
    plan, _ = _generate(
        user_msg, history, cfg, model_key,
        provider=provider, few_shots=few_shots, prompts_dir=prompts_dir,
    )
    return plan


def generate_sql(
    user_msg: str,
    history: list[dict],
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
    few_shots: Optional[list[dict]] = None,
    prompts_dir: Optional[Path] = None,
) -> str:
    """§E.7 signature: return just the SQL string (thin wrapper over the plan)."""
    plan = generate_query_plan(
        user_msg, history, cfg, model_key,
        provider=provider, few_shots=few_shots, prompts_dir=prompts_dir,
    )
    return plan["sql"] if plan else ""


# --------------------------------------------------------------------------- #
# Stage 2b — RAG-grounded commentary generation (FR-3B-36/37)                   #
# --------------------------------------------------------------------------- #

def _generate_commentary(
    user_msg: str,
    history: list[dict],
    rag_context: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider],
    prompts_dir: Optional[Path],
    max_tokens: int = _COMMENTARY_MAX_TOKENS,
    run_id: Optional[str] = None,
) -> tuple[Optional[dict], LLMResponse]:
    """One commentary LLM call → (plan|None, response).

    Same ``{"sql", "answer_template"}`` JSON contract as the data path, so the
    numbers are filled by ``fill_numeric_slots`` and checked by
    ``verify_traceability`` (FR-3B-37). The RAG grounding (the tool's own report +
    methodology excerpts) is appended to the system prompt to inform the prose.
    """
    tpl = (
        load_prompt_template(_COMMENTARY_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_COMMENTARY_TEMPLATE)
    )
    system = tpl.text + _render_run_scope(run_id)
    if rag_context:
        system = system + "\n\n" + rag_context
    messages = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in history]
    messages.append({"role": "user", "content": user_msg})
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    return _parse_plan(response.text), response


def generate_commentary_plan(
    user_msg: str,
    history: list[dict],
    rag_context: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
    prompts_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Generate ``{"sql", "answer_template"}`` for a grounded commentary draft."""
    plan, _ = _generate_commentary(
        user_msg, history, rag_context, cfg, model_key,
        provider=provider, prompts_dir=prompts_dir,
    )
    return plan


def _generate_commentary_prose(
    user_msg: str,
    history: list[dict],
    facts: dict,
    rag_context: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider],
    prompts_dir: Optional[Path],
    max_tokens: int = _COMMENTARY_MAX_TOKENS,
) -> tuple[str, LLMResponse]:
    """One commentary LLM call → (prose, response) — the round-3 fact-pack route.

    Mirrors the memo Skill: the app-assembled ``facts`` pack (display-rounded) and
    the RAG grounding are appended to the system prompt; the model returns plain
    narrative prose (no SQL, no JSON, no slots). Numbers are then checked verbatim
    against the fact pack + grounding by ``verify_traceability`` (FR-3B-37).
    """
    tpl = (
        load_prompt_template(_COMMENTARY_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_COMMENTARY_TEMPLATE)
    )
    facts_json = json.dumps(facts or {}, ensure_ascii=False, indent=2, default=str)
    system = (
        tpl.text
        + "\n\n## Fact pack — every figure you cite MUST appear here verbatim\n"
        + facts_json
    )
    if rag_context:
        system = system + "\n\n## Grounding context (qualitative claims only)\n" + rag_context
    messages = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in history]
    messages.append({"role": "user", "content": user_msg})
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    return response.text, response


# --------------------------------------------------------------------------- #
# Stage 2c — multi-query synthesis for EXPLORATORY (round 3 Phase D)            #
# --------------------------------------------------------------------------- #

def _parse_query_plan(text: str, max_queries: int) -> list[dict]:
    """Extract a capped list of ``{label, sql}`` queries from the planner's JSON."""
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = re.sub(r"^[a-zA-Z]+\n", "", cleaned, count=1)
    obj = None
    try:
        obj = json.loads(cleaned)
    except (ValueError, TypeError):
        brace = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace is not None:
            try:
                obj = json.loads(brace.group(0))
            except (ValueError, TypeError):
                obj = None
    if isinstance(obj, dict):
        items = obj.get("queries")
    elif isinstance(obj, list):
        items = obj
    else:
        items = None
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        sql = item.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            continue
        label = item.get("label")
        out.append({"label": str(label) if label else f"query {i + 1}", "sql": sql})
        if len(out) >= max_queries:
            break
    return out


def _generate_synthesis_plan(
    user_msg: str, history: list[dict], few_shots: Optional[list[dict]],
    run_id: Optional[str], cfg: dict, model_key: str, *,
    provider: Optional[LLMProvider], prompts_dir: Optional[Path], max_tokens: int,
    study_digest: Optional[dict] = None,
) -> tuple[str, LLMResponse]:
    """One planner LLM call → (raw JSON text, response)."""
    tpl = (
        load_prompt_template(_SYNTH_PLAN_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_SYNTH_PLAN_TEMPLATE)
    )
    system = (
        tpl.text + _render_few_shots(few_shots or [])
        + _render_run_scope(run_id) + _render_digest(study_digest)
    )
    messages = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in history]
    messages.append({"role": "user", "content": user_msg})
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    return response.text, response


def _render_evidence(evidence: list[dict]) -> str:
    """Render gathered query results as labelled JSON for the synthesis prompt."""
    return json.dumps(evidence, ensure_ascii=False, indent=2, default=str)


def _generate_synthesis_answer(
    user_msg: str, history: list[dict], evidence: list[dict],
    cfg: dict, model_key: str, *,
    provider: Optional[LLMProvider], prompts_dir: Optional[Path], max_tokens: int,
    study_digest: Optional[dict] = None,
) -> tuple[str, LLMResponse]:
    """One synthesis LLM call → (prose answer, response)."""
    tpl = (
        load_prompt_template(_SYNTH_ANSWER_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_SYNTH_ANSWER_TEMPLATE)
    )
    system = tpl.text + _render_digest(study_digest) + "\n\n" + _render_evidence(evidence)
    messages = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in history]
    messages.append({"role": "user", "content": user_msg})
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    return response.text, response


# --------------------------------------------------------------------------- #
# Optional faithfulness judge (FR-3B-46) — flag, never block                    #
# --------------------------------------------------------------------------- #

_SCORE_RE = re.compile(r"[1-5]")


def _parse_score(text: str) -> Optional[int]:
    """Parse the judge's strict 1-5 integer; None if absent/malformed."""
    match = _SCORE_RE.search(text or "")
    return int(match.group(0)) if match else None


def _judge(
    draft: str,
    grounding: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider],
    prompts_dir: Optional[Path],
    max_tokens: int = _FAITHFULNESS_MAX_TOKENS,
) -> tuple[Optional[int], LLMResponse]:
    """One faithfulness LLM call → (score|None, response)."""
    tpl = (
        load_prompt_template(_FAITHFULNESS_TEMPLATE, prompts_dir)
        if prompts_dir is not None
        else load_prompt_template(_FAITHFULNESS_TEMPLATE)
    )
    system = tpl.text + "\n\n## Grounding context\n" + (grounding or "(none)")
    messages = [{"role": "user", "content": "## Draft to score\n" + draft}]
    response = complete(
        cfg, model_key, messages, max_tokens,
        temperature=0.0, system=system, provider=provider,
    )
    return _parse_score(response.text), response


def score_faithfulness(
    draft: str,
    grounding: str,
    cfg: dict,
    model_key: str,
    *,
    provider: Optional[LLMProvider] = None,
    prompts_dir: Optional[Path] = None,
) -> Optional[int]:
    """Score a commentary draft 1-5 against its grounding (FR-3B-46).

    Returns ``None`` when the judge reply can't be parsed — the caller treats
    that as "no score" and never blocks on it.
    """
    score, _ = _judge(
        draft, grounding, cfg, model_key, provider=provider, prompts_dir=prompts_dir
    )
    return score


# --------------------------------------------------------------------------- #
# Stage 3 — SQL validation (FR-3B-31, gates 1-4)                               #
# --------------------------------------------------------------------------- #

def validate_sql(
    sql: str, allowlist: dict[str, set[str]], row_cap: int = _DEFAULT_ROW_CAP
) -> SQLValidationResult:
    """Delegate to the hardened boundary (gates 1-4, pure, no DB)."""
    return validate_select(sql, allowlist, row_cap=row_cap)


#: Per-cell statistics that must NEVER be aggregated across cells (FR-1A-24): a
#: rolled-up credibility/SE must be recomputed from the summed claim count, not
#: averaged/summed from per-cell values. The system appends the correct aggregate
#: credibility itself, so generated SQL must never aggregate these columns.
_PER_CELL_STAT_RE = re.compile(r"^(credibility_z|se_ae)", re.IGNORECASE)


def aggregates_per_cell_stat(sql: str) -> bool:
    """True if ``sql`` applies an aggregate (AVG/SUM/MIN/MAX/MEDIAN, not COUNT) to a
    per-cell ``credibility_z*`` / ``se_ae*`` column.

    Deterministic backstop for the FR-1A-24 antipattern: averaging per-cell
    credibility collapses it toward 0 (e.g. ``AVG(credibility_z_lapse) ≈ 0.0015``
    instead of the true ~0.39). The prompt forbids it; this catches a model that
    does it anyway, so a wrong credibility can never reach the user's prose.
    Parse failures return False — the boundary gates already reject unparseable SQL.
    """
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return False
    if tree is None:
        return False
    for agg in tree.find_all(exp.AggFunc):
        if isinstance(agg, exp.Count):  # COUNT(credibility_z) is harmless
            continue
        for col in agg.find_all(exp.Column):
            if _PER_CELL_STAT_RE.match(col.name or ""):
                return True
    return False


# --------------------------------------------------------------------------- #
# Stage 4 — execution via the MCP server (FR-3B-25, server re-enforces gates)   #
# --------------------------------------------------------------------------- #

def _target_tables(sql: str) -> set[str]:
    """Physical tables referenced by ``sql`` (deterministic; no SQL building)."""
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return set()
    return {t.name for t in parsed.find_all(exp.Table)}


_AE_TABLE = "gold_ae_results"
_TEV_TABLE = "gold_tev_results"


def execute_via_mcp(sql: str, mcp_client: MCPClient) -> dict:
    """Route the validated SELECT to the correct gated MCP tool by its table.

    The chatbot opens no DB connection of its own (FR-3B-25); the server
    re-enforces every gate (FR-3B-10). A query must reference exactly one
    *queryable* Gold table (cross-table reads are unroutable here and would be
    rejected server-side too). The A/E and TEV tables keep their dedicated tools;
    every other widened PII-free table is routed to the generic gated
    ``query_results`` tool. Returns the tool's ``{columns, rows, row_count}`` on
    success or its structured error object.
    """
    tables = _target_tables(sql) & set(_QUERYABLE_TABLES)
    if len(tables) != 1:
        return {
            "error": "unroutable",
            "message": "The query did not reference exactly one supported results table.",
        }
    table = next(iter(tables))
    if table == _AE_TABLE:
        return mcp_client.query_ae_results(sql)
    if table == _TEV_TABLE:
        return mcp_client.query_tev_results(sql)
    return mcp_client.query_results(table, sql)


# --------------------------------------------------------------------------- #
# Stage 5 — numeric slot filling (FR-3B-33, fixed grammar)                      #
# --------------------------------------------------------------------------- #

_COL_RE = re.compile(r"\{\{col:([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?\}\}")
_AGG_RE = re.compile(r"\{\{agg:(sum|mean|min|max|count):([A-Za-z_][A-Za-z0-9_]*)\}\}")
_LIST_RE = re.compile(r"\{\{list:([A-Za-z_][A-Za-z0-9_]*)\}\}")
_TABLE_RE = re.compile(
    r"\{\{table:\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}"
)
_LEFTOVER_RE = re.compile(r"\{\{.*?\}\}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_number(value) -> str:
    """Format a numeric value for display (integers plain; else up to 4 dp)."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _format_value(value) -> str:
    if value is None:
        return "N/A"
    if _is_number(value):
        return _format_number(value)
    return str(value)


def _column_index(result_set: dict) -> dict[str, int]:
    return {str(c): i for i, c in enumerate(result_set.get("columns", []))}


def _resolve_slots(template: str, result_set: dict) -> tuple[str, list[float]]:
    """Fill the fixed-grammar slots from ``result_set``; collect injected numbers.

    Raises ``SlotFillError`` on any unknown column, out-of-range row, empty
    aggregate input, or leftover/malformed placeholder. Returns ``(text,
    injected_numbers)`` where ``injected_numbers`` are the numeric values the
    system placed (so the deterministic post-check can treat a system-computed
    aggregate as traceable to the result set).
    """
    col_idx = _column_index(result_set)
    rows = result_set.get("rows", []) or []
    injected: list[float] = []

    def _agg_repl(match: re.Match) -> str:
        fn, column = match.group(1), match.group(2)
        if column not in col_idx:
            raise SlotFillError(f"unknown aggregate column: {column}")
        idx = col_idx[column]
        all_vals = [row[idx] for row in rows]
        numeric = [float(v) for v in all_vals if _is_number(v)]
        if fn == "count":
            value: float = float(len(all_vals))
        elif not numeric:
            raise SlotFillError(f"no numeric data to aggregate in column: {column}")
        elif fn == "sum":
            value = sum(numeric)
        elif fn == "mean":
            value = sum(numeric) / len(numeric)
        elif fn == "min":
            value = min(numeric)
        else:  # "max"
            value = max(numeric)
        injected.append(value)
        return _format_number(value)

    def _col_repl(match: re.Match) -> str:
        column, raw_idx = match.group(1), match.group(2)
        if column not in col_idx:
            raise SlotFillError(f"unknown column: {column}")
        row_index = int(raw_idx) if raw_idx is not None else 0
        if row_index >= len(rows):
            raise SlotFillError(f"row index {row_index} out of range for: {column}")
        value = rows[row_index][col_idx[column]]
        if _is_number(value):
            injected.append(float(value))
        return _format_value(value)

    def _list_repl(match: re.Match) -> str:
        column = match.group(1)
        if column not in col_idx:
            raise SlotFillError(f"unknown list column: {column}")
        idx = col_idx[column]
        seen: list[str] = []
        for row in rows:
            value = row[idx]
            display = _format_value(value)
            if display not in seen:
                seen.append(display)
            if _is_number(value):
                injected.append(float(value))
        if not seen:
            raise SlotFillError(f"no data to list for column: {column}")
        return ", ".join(seen)

    def _table_repl(match: re.Match) -> str:
        columns = [c.strip() for c in match.group(1).split(",")]
        for column in columns:
            if column not in col_idx:
                raise SlotFillError(f"unknown table column: {column}")
        if not rows:
            return "(no matching rows)"
        header = "| " + " | ".join(columns) + " |"
        divider = "| " + " | ".join("---" for _ in columns) + " |"
        body: list[str] = []
        for row in rows:
            cells: list[str] = []
            for column in columns:
                value = row[col_idx[column]]
                if _is_number(value):
                    injected.append(float(value))
                cells.append(_format_value(value))
            body.append("| " + " | ".join(cells) + " |")
        return "\n".join([header, divider, *body])

    filled = _TABLE_RE.sub(_table_repl, template)
    filled = _AGG_RE.sub(_agg_repl, filled)
    filled = _LIST_RE.sub(_list_repl, filled)
    filled = _COL_RE.sub(_col_repl, filled)
    if _LEFTOVER_RE.search(filled):
        raise SlotFillError("unresolved or malformed placeholder remains")
    return filled, injected


def fill_numeric_slots(template: str, result_set: dict) -> str:
    """Fill the fixed-grammar numeric slots (§E.7). Raises ``SlotFillError`` on
    any unresolved/malformed placeholder — the turn is then blocked (FR-3B-33)."""
    filled, _ = _resolve_slots(template, result_set)
    return filled


# --------------------------------------------------------------------------- #
# Stage 6 — response assembly with statistical context (FR-3B-35)              #
# --------------------------------------------------------------------------- #

_CREDIBILITY_COLS = ("credibility_z", "credibility_z_lapse", "credibility_z_ci")
#: Aggregate actual-claim counts, ordered by decrement. Credibility Z is recomputed
#: from the FIRST of these present (FR-1A-24) rather than read from a stored
#: ``credibility_z*`` column — on an aggregate query that stored value is an
#: arbitrary detail cell (the Doc-3 "0.0015 instead of 0.3881" bug).
_ACTUAL_COUNT_COLS = (
    "actual_deaths_count", "actual_lapses", "actual_ci_claims", "actual_surrenders",
)
_EXPECTED_COLS = (
    "expected_deaths_count", "expected_ci_claims", "expected_lapses", "expected_surrenders",
)
_EXPOSURE_COLS = (
    "exposure_count", "lapse_exposure_count", "ci_exposure_count", "surrender_exposure",
)


def assemble_response(
    filled_text: str,
    result_set: dict,
    *,
    credibility_method: str = "LF",
    injected: Optional[list] = None,
) -> str:
    """Append exposure + credibility context to A/E answers when present (FR-3B-35).

    Exposure and expected-event values are read from the single result row (cells
    already in the result set), so they remain traceable. Credibility Z is
    **recomputed from the aggregate actual-claim count** via the credibility module
    (FR-1A-24) — never read from a stored ``credibility_z*`` column, which on an
    aggregate (multi-cell) query is an arbitrary detail cell's value and so
    misrepresents the roll-up (the Doc-3 UL-lapse "0.0015 instead of 0.3881" bug).
    When the result carries no actual-count column, fall back to a stored
    ``credibility_z`` (the answer is then its own single cell, so the stored value
    is correct). ``credibility_method`` honours the run's method ("LF"/"BUHLMANN").

    The context is appended only for a **single-row** result (an aggregate or one
    cell): on a multi-row detail set the first row is an arbitrary cell — often a
    near-zero young-age band — so its context would misrepresent the answer. The
    recomputed Z is appended to ``injected`` (when supplied) so the numeric
    post-check treats it as traceable to the data. Returns ``filled_text``
    unchanged when there is no single-row context to add.
    """
    cols = _column_index(result_set)
    rows = result_set.get("rows", []) or []
    if len(rows) != 1:
        return filled_text
    row0 = rows[0]

    def _first_present(candidates) -> Optional[tuple[str, object]]:
        for name in candidates:
            if name in cols and row0[cols[name]] is not None:
                return name, row0[cols[name]]
        return None

    bits: list[str] = []
    exposure = _first_present(_EXPOSURE_COLS)
    if exposure is not None:
        bits.append("exposure " + _format_value(exposure[1]))
    expected = _first_present(_EXPECTED_COLS)
    if expected is not None:
        bits.append("expected events " + _format_value(expected[1]))

    count = _first_present(_ACTUAL_COUNT_COLS)
    if count is not None and _is_number(count[1]):
        z = compute_credibility_z(float(count[1]), method=credibility_method)
        if injected is not None:
            injected.append(float(z))
        bits.append("credibility Z " + _format_number(z))
    else:
        stored = _first_present(_CREDIBILITY_COLS)
        if stored is not None:
            bits.append("credibility Z " + _format_value(stored[1]))

    if not bits:
        return filled_text
    return filled_text + "\n\n(Statistical context: " + "; ".join(bits) + ".)"


# --------------------------------------------------------------------------- #
# Orchestrator — handle_turn (FR-3B-31/33/34/39/40/42/44/45)                    #
# --------------------------------------------------------------------------- #

def _chatbot_limit(chatbot_cfg: Optional[dict], key: str, default):
    if not chatbot_cfg:
        return default
    value = chatbot_cfg.get(key, default)
    return value if value is not None else default


def _audit(sink: Optional[Callable[[dict], None]], event: dict) -> None:
    if sink is not None:
        sink(event)


def _blocked(
    state: SessionState,
    intent: Optional[IntentLabel],
    reason: str,
    text: str,
    *,
    sql: Optional[str] = None,
    outcome: Optional[SQLGateOutcome] = None,
    traceability=None,
    llm_response: Optional[LLMResponse] = None,
) -> ChatTurnResult:
    state.add_turn("assistant", text, {"blocked": True, "block_reason": reason})
    return ChatTurnResult(
        session_id=state.session_id,
        intent=intent,
        response_text=text,
        sql=sql,
        sql_outcome=outcome,
        result_row_count=None,
        traceability=traceability,
        llm_response=llm_response,
        blocked=True,
        block_reason=reason,
    )


def _apply_traceability(trace, body: str, analyst_mode: bool) -> tuple[bool, str]:
    """Decide how a traceability result affects the answer.

    Returns ``(block, text)``. A clean result never blocks. A failure **blocks**
    in the default (hard) mode (FR-3B-34); in opt-in Analyst mode it instead
    renders ``body`` with a visible "unverified figures" warning (flag-not-block).
    The SQL gates are unaffected — only this numeric post-check is relaxed.
    """
    if trace.passed:
        return False, body
    if not analyst_mode:
        return True, body
    nums = ", ".join(trace.untraceable_nums) or "(see above)"
    return False, body + _UNVERIFIED_WARNING.format(nums=nums)


def _accumulate(ctx: dict, resp: LLMResponse, cfg: dict) -> None:
    """Fold one LLM call's tokens/cost/latency into the per-turn audit context."""
    ctx["input_tokens"] += resp.input_tokens
    ctx["output_tokens"] += resp.output_tokens
    ctx["latency_ms"] += resp.latency_ms
    price_in, price_out = model_prices(cfg, resp.model)
    ctx["est_cost_usd"] += call_cost(resp, price_in, price_out)
    ctx["provider"] = resp.provider
    ctx["model_string"] = resp.model


def _new_audit_ctx() -> dict:
    return {
        "input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0, "latency_ms": 0.0,
        "intent_reason": None, "provider": None, "model_string": None,
        "retrieved_context_ref": None, "faithfulness_score": None,
        "faithfulness_ran": False,
    }


def _run_turn(
    ctx: dict,
    user_msg: str,
    state: SessionState,
    cfg: dict,
    mcp_client: MCPClient,
    allowlist: dict[str, set[str]],
    *,
    chatbot_cfg: Optional[dict],
    few_shots: Optional[list[dict]],
    provider: Optional[LLMProvider],
    audit: Optional[Callable[[dict], None]],
    prompts_dir: Optional[Path],
    rag_run_ids: Optional[list[str]],
    rag_artifact_paths: Optional[dict],
    commentary_facts: Optional[dict],
    analyst_mode: Optional[bool],
    multi_query: Optional[bool],
    study_digest: Optional[dict] = None,
) -> ChatTurnResult:
    """The guarded turn pipeline; populates ``ctx`` for the per-turn audit row."""
    if analyst_mode is None:
        analyst_mode = bool(_chatbot_limit(chatbot_cfg, "analyst_mode_default", False))
    if multi_query is None:
        multi_query = bool(_chatbot_limit(chatbot_cfg, "multi_query_default", False))
    budget = int(_chatbot_limit(chatbot_cfg, "session_token_budget", _DEFAULT_TOKEN_BUDGET))
    warn_fraction = float(_chatbot_limit(chatbot_cfg, "budget_warning_fraction", _DEFAULT_WARN_FRACTION))
    max_turns = int(_chatbot_limit(chatbot_cfg, "max_turns_per_session", _DEFAULT_MAX_TURNS))
    token_window = int(_chatbot_limit(chatbot_cfg, "conversation_token_window", _DEFAULT_TOKEN_WINDOW))
    row_cap = int(_chatbot_limit(chatbot_cfg, "sql_row_cap", _DEFAULT_ROW_CAP))
    routing_max = _call_max_tokens(chatbot_cfg, "routing")
    sqlgen_max = _call_max_tokens(chatbot_cfg, "sql_generation")
    run_id = (rag_run_ids or [None])[0]

    # Budget hard-stop (FR-3B-44): no LLM call, no degradation.
    if state.tokens_used >= budget:
        return _blocked(state, None, "budget_exhausted", _BUDGET_STOP_TEXT)

    # Max-turns prompt (FR-3B-40).
    prior_user_turns = sum(1 for t in state.turns if t.get("role") == "user")
    if prior_user_turns >= max_turns:
        return _blocked(state, None, "max_turns_reached", _MAX_TURNS_TEXT)

    # Budget warning (number-free, so safe to prepend at any stage).
    warning = _BUDGET_WARNING_PREFIX if state.tokens_used >= warn_fraction * budget else ""

    state.add_turn("user", user_msg)
    model_key = state.model_key  # honors a mid-session switch (FR-3B-45)

    # ---- Stage 1: routing (logged BEFORE any data access, FR-3B-27) ----
    try:
        intent, reason, route_resp = _route(
            user_msg, cfg, model_key, provider=provider, prompts_dir=prompts_dir,
            max_tokens=routing_max, history=state.turns[:-1], study_digest=study_digest,
        )
    except LLMProviderError:
        # A provider failure surfaces as a safe message; the session is not crashed
        # or corrupted (FR-3B-05).
        return _blocked(state, None, "llm_error", _SAFE_FAILURE_TEXT)
    _accumulate(ctx, route_resp, cfg)
    record_call(state, route_resp, cfg)
    ctx["intent_reason"] = reason
    _audit(audit, {"event": "intent", "intent": intent.value, "reason": reason,
                   "model": route_resp.model})

    # ---- Refusal / non-data routes (FR-3B-28/42) ----
    if intent is IntentLabel.OUT_OF_SCOPE:
        text = warning + _REFUSAL_TEXT
        state.add_turn("assistant", text, {"intent": intent.value, "refusal": True})
        return ChatTurnResult(
            session_id=state.session_id, intent=intent, response_text=text,
            sql=None, sql_outcome=None, result_row_count=None, traceability=None,
            llm_response=route_resp, blocked=False, block_reason="refusal",
        )

    # ---- Commentary route: RAG-grounded draft (FR-3B-36/37/38) ----
    if intent is IntentLabel.COMMENTARY_GENERATION:
        return _commentary_turn(
            ctx, user_msg, state, cfg, mcp_client, allowlist,
            route_resp=route_resp, intent=intent, warning=warning,
            token_window=token_window, row_cap=row_cap, chatbot_cfg=chatbot_cfg,
            provider=provider, audit=audit, prompts_dir=prompts_dir,
            rag_run_ids=rag_run_ids, rag_artifact_paths=rag_artifact_paths,
            commentary_facts=commentary_facts, analyst_mode=analyst_mode,
        )

    # ---- Multi-query synthesis route (round 3 Phase D; EXPLORATORY + opt-in) ----
    if intent is IntentLabel.EXPLORATORY and multi_query:
        return _synthesis_turn(
            ctx, user_msg, state, cfg, mcp_client, allowlist,
            route_resp=route_resp, intent=intent, warning=warning,
            token_window=token_window, row_cap=row_cap, chatbot_cfg=chatbot_cfg,
            few_shots=few_shots, provider=provider, audit=audit,
            prompts_dir=prompts_dir, run_id=run_id, analyst_mode=analyst_mode,
            study_digest=study_digest,
        )

    # ---- Stage 2: SQL + template generation ----
    history = trim_history(state.turns[:-1], "", token_window)
    try:
        plan, gen_resp = _generate(
            user_msg, history, cfg, model_key,
            provider=provider, few_shots=few_shots, prompts_dir=prompts_dir,
            max_tokens=sqlgen_max, run_id=run_id, study_digest=study_digest,
        )
    except LLMProviderError:
        # A truncated/empty completion (common on reasoning models at a small cap)
        # must surface as a safe, saved message — never a silent UI exception.
        return _blocked(state, intent, "llm_error", warning + _LLM_ERROR_TEXT,
                        llm_response=route_resp)
    _accumulate(ctx, gen_resp, cfg)
    record_call(state, gen_resp, cfg)
    if plan is None or not plan["sql"].strip():
        return _blocked(state, intent, "sql_generation_failed",
                        warning + _SAFE_FAILURE_TEXT, llm_response=route_resp)

    # ---- Stage 3: validation gates 1-4 (never rewrite a rejection) ----
    validation = validate_sql(plan["sql"], allowlist, row_cap=row_cap)
    _audit(audit, {"event": "sql_validation", "outcome": validation.outcome.value,
                   "gate_failed": validation.gate_failed, "sql": plan["sql"]})
    if validation.outcome is not SQLGateOutcome.PASS:
        return _blocked(
            state, intent, validation.gate_failed or validation.outcome.value,
            warning + _SAFE_FAILURE_TEXT,
            sql=plan["sql"], outcome=validation.outcome, llm_response=route_resp,
        )

    # ---- Per-cell-statistic guard (FR-1A-24 deterministic backstop) ----
    # Block a query that averages/sums a per-cell credibility_z*/se_ae* column, so a
    # wrong rolled-up credibility (e.g. AVG -> 0.0015) can never reach the prose.
    # The correct aggregate credibility is appended by assemble_response.
    if aggregates_per_cell_stat(validation.sql):
        return _blocked(
            state, intent, "credibility_aggregate", warning + _CREDIBILITY_AGG_HINT_TEXT,
            sql=validation.sql, outcome=validation.outcome, llm_response=route_resp,
        )

    # ---- Run-scope guard (lower-priority hardening; observability only) ----
    # When a study run is active, every A/E query should be scoped to it. The scope
    # is injected into the SQL-gen prompt (_render_run_scope); here we *observe*
    # whether the model honoured it and emit a non-blocking audit event. We never
    # rewrite the validated SQL — server-side enforcement is the deferred path.
    if run_id and _AE_TABLE in _target_tables(validation.sql):
        _audit(audit, {
            "event": "run_scope", "run_id": run_id,
            "applied": "study_run_id" in validation.sql.lower(),
            "sql": validation.sql,
        })

    # ---- Stage 4: execution via the gated MCP server (gate 5) ----
    result = execute_via_mcp(validation.sql, mcp_client)
    if "error" in result:
        return _blocked(
            state, intent, str(result.get("error")), warning + _SAFE_FAILURE_TEXT,
            sql=validation.sql, outcome=validation.outcome, llm_response=route_resp,
        )

    # ---- Stage 5: numeric slot filling ----
    try:
        filled, injected = _resolve_slots(plan["answer_template"], result)
    except SlotFillError:
        return _blocked(
            state, intent, "slot_fill_failed", warning + _SLOT_FILL_HINT_TEXT,
            sql=validation.sql, outcome=validation.outcome, llm_response=route_resp,
        )

    # ---- Stage 6: assemble + mandatory traceability on the FINAL text ----
    # Credibility Z in the appended context is recomputed from the aggregate count
    # (FR-1A-24) and added to `injected` so it stays traceable. The run's method is
    # carried on the digest when the UI supplies it (default LF, the system default).
    cred_method = str((study_digest or {}).get("credibility_method", "LF"))
    assembled = assemble_response(
        filled, result, credibility_method=cred_method, injected=injected,
    )
    trace = verify_traceability(
        assembled,
        result_set={"cells": result, "computed": injected, "digest": study_digest},
        user_msg=user_msg,
    )
    block_tr, assembled = _apply_traceability(trace, assembled, analyst_mode)
    if block_tr:
        return _blocked(
            state, intent, "numeric_traceability", warning + _SAFE_FAILURE_TEXT,
            sql=validation.sql, outcome=validation.outcome,
            traceability=trace, llm_response=route_resp,
        )

    response_text = warning + assembled
    state.add_turn("assistant", response_text, {"intent": intent.value})
    return ChatTurnResult(
        session_id=state.session_id,
        intent=intent,
        response_text=response_text,
        sql=validation.sql,
        sql_outcome=SQLGateOutcome.PASS,
        result_row_count=int(result.get("row_count", 0)),
        traceability=trace,
        llm_response=route_resp,
        blocked=False,
        block_reason=None,
    )


def _commentary_turn(
    ctx: dict,
    user_msg: str,
    state: SessionState,
    cfg: dict,
    mcp_client: MCPClient,
    allowlist: dict[str, set[str]],
    *,
    route_resp: LLMResponse,
    intent: IntentLabel,
    warning: str,
    token_window: int,
    row_cap: int,
    chatbot_cfg: Optional[dict],
    provider: Optional[LLMProvider],
    audit: Optional[Callable[[dict], None]],
    prompts_dir: Optional[Path],
    rag_run_ids: Optional[list[str]],
    rag_artifact_paths: Optional[dict],
    commentary_facts: Optional[dict] = None,
    analyst_mode: bool = False,
) -> ChatTurnResult:
    """The fact-pack commentary path (round 3; FR-3B-36/37/38/46).

    Generate-then-verify, exactly like the memo Skill: the app-assembled
    ``commentary_facts`` (display-rounded, multi-product/decrement) plus the RAG
    grounding (the tool's *own* reports/methodology) are handed to the model, which
    drafts narrative **prose** (no SQL, no slot-fill). Every number is then checked
    verbatim against the fact pack (``run_id`` excluded) and grounding by
    ``verify_traceability``. This removes the single-query straitjacket that made
    commentary fragile and the wrongly-averaged credibility artifact. Carries the
    persistent AI-draft banner; the SQL gates do not apply (no SQL is generated).
    """
    model_key = state.model_key
    rag_cfg = (chatbot_cfg or {}).get("rag", {}) or {}
    max_chars = rag_cfg.get("max_grounding_chars")
    rag_kwargs = {"max_chars": int(max_chars)} if max_chars else {}
    rag_context = assemble_rag_context(
        rag_run_ids or [], rag_artifact_paths or {}, **rag_kwargs
    )
    ctx["retrieved_context_ref"] = {
        "run_ids": rag_run_ids or [], "artifacts": rag_artifact_paths or {},
        "facts": bool(commentary_facts),
    }

    history = trim_history(state.turns[:-1], "", token_window)
    try:
        drafted, gen_resp = _generate_commentary_prose(
            user_msg, history, commentary_facts or {}, rag_context, cfg, model_key,
            provider=provider, prompts_dir=prompts_dir,
            max_tokens=_call_max_tokens(chatbot_cfg, "commentary"),
        )
    except LLMProviderError:
        return _blocked(state, intent, "llm_error", warning + _LLM_ERROR_TEXT,
                        llm_response=route_resp)
    _accumulate(ctx, gen_resp, cfg)
    record_call(state, gen_resp, cfg)
    if not drafted.strip():
        return _blocked(state, intent, "commentary_generation_failed",
                        warning + _COMMENTARY_FAIL_HINT_TEXT, llm_response=route_resp)

    banner_text = _AI_DRAFT_BANNER + drafted.strip()
    # Numbers must trace to the fact pack (run_id excluded so its UUID digits don't
    # leak into the allowed-set) or to the tool's own grounding context (FR-3B-37).
    facts_for_trace = {k: v for k, v in (commentary_facts or {}).items() if k != "run_id"}
    trace = verify_traceability(
        banner_text,
        result_set={"facts": facts_for_trace, "context": rag_context},
        user_msg=user_msg,
    )
    block_tr, banner_text = _apply_traceability(trace, banner_text, analyst_mode)
    if block_tr:
        return _blocked(
            state, intent, "numeric_traceability", warning + _SAFE_FAILURE_TEXT,
            traceability=trace, llm_response=route_resp,
        )

    final = warning + banner_text
    # Optional faithfulness judge (FR-3B-46): flag, never block.
    if bool(_chatbot_limit(chatbot_cfg, "faithfulness_llm_judge", False)):
        ctx["faithfulness_ran"] = True
        grounding = rag_context
        if commentary_facts:
            grounding = (grounding + "\n\n" if grounding else "") + json.dumps(
                facts_for_trace, ensure_ascii=False, default=str
            )
        try:
            score, judge_resp = _judge(
                banner_text, grounding, cfg, model_key,
                provider=provider, prompts_dir=prompts_dir,
                max_tokens=_call_max_tokens(chatbot_cfg, "faithfulness"),
            )
            _accumulate(ctx, judge_resp, cfg)
            record_call(state, judge_resp, cfg)
        except LLMProviderError:
            score = None
        if score is not None:
            ctx["faithfulness_score"] = score
            threshold = int(_chatbot_limit(chatbot_cfg, "faithfulness_flag_threshold", 3))
            if score <= threshold:
                final = final + _FAITHFULNESS_WARNING.format(score=score)

    state.add_turn("assistant", final, {"intent": intent.value, "commentary": True})
    return ChatTurnResult(
        session_id=state.session_id,
        intent=intent,
        response_text=final,
        sql=None,
        sql_outcome=None,
        result_row_count=None,
        traceability=trace,
        llm_response=route_resp,
        blocked=False,
        block_reason=None,
    )


def _synthesis_turn(
    ctx: dict,
    user_msg: str,
    state: SessionState,
    cfg: dict,
    mcp_client: MCPClient,
    allowlist: dict[str, set[str]],
    *,
    route_resp: LLMResponse,
    intent: IntentLabel,
    warning: str,
    token_window: int,
    row_cap: int,
    chatbot_cfg: Optional[dict],
    few_shots: Optional[list[dict]],
    provider: Optional[LLMProvider],
    audit: Optional[Callable[[dict], None]],
    prompts_dir: Optional[Path],
    run_id: Optional[str],
    analyst_mode: bool,
    study_digest: Optional[dict] = None,
) -> ChatTurnResult:
    """Plan → fetch → synthesise for an EXPLORATORY turn (round 3 Phase D).

    A bounded, gated multi-query loop: the planner returns up to
    ``max_synthesis_queries`` SELECTs; each runs through the existing gates +
    MCP server (so the server re-enforces gates 1-5); the synthesiser then drafts
    a prose answer over the combined evidence, generate-then-verify against it.
    Gate-rejected/erroring queries are skipped and logged — never executed.
    """
    ctx["synthesis_ran"] = True
    model_key = state.model_key
    max_queries = int(_chatbot_limit(chatbot_cfg, "max_synthesis_queries", 4))
    synth_max = _call_max_tokens(chatbot_cfg, "synthesis")
    history = trim_history(state.turns[:-1], "", token_window)

    # ---- Stage 1: plan the evidence queries ----
    try:
        plan_text, plan_resp = _generate_synthesis_plan(
            user_msg, history, few_shots, run_id, cfg, model_key,
            provider=provider, prompts_dir=prompts_dir, max_tokens=synth_max,
            study_digest=study_digest,
        )
    except LLMProviderError:
        return _blocked(state, intent, "llm_error", warning + _LLM_ERROR_TEXT,
                        llm_response=route_resp)
    _accumulate(ctx, plan_resp, cfg)
    record_call(state, plan_resp, cfg)
    queries = _parse_query_plan(plan_text, max_queries)
    if not queries:
        return _blocked(state, intent, "synthesis_plan_failed",
                        warning + _SAFE_FAILURE_TEXT, llm_response=route_resp)

    # ---- Stage 2: fetch each query through the gates + MCP server ----
    evidence: list[dict] = []
    executed_sql: list[str] = []
    for q in queries:
        validation = validate_sql(q["sql"], allowlist, row_cap=row_cap)
        _audit(audit, {"event": "sql_validation", "outcome": validation.outcome.value,
                       "gate_failed": validation.gate_failed, "sql": q["sql"]})
        if validation.outcome is not SQLGateOutcome.PASS:
            continue  # skip a rejected query (logged); never execute disallowed SQL
        if aggregates_per_cell_stat(validation.sql):
            # FR-1A-24: never aggregate per-cell credibility/SE — skip this query
            # (other evidence still answers); the correct aggregate Z is appended.
            _audit(audit, {"event": "sql_validation", "outcome": "credibility_aggregate",
                           "gate_failed": "credibility_aggregate", "sql": q["sql"]})
            continue
        result = execute_via_mcp(validation.sql, mcp_client)
        if "error" in result:
            continue
        evidence.append({
            "label": q["label"],
            "columns": result.get("columns", []),
            "rows": result.get("rows", []),
        })
        executed_sql.append(validation.sql)
    if not evidence:
        return _blocked(state, intent, "synthesis_no_evidence",
                        warning + _NO_DATA_HINT_TEXT, llm_response=route_resp)

    # ---- Stage 3: synthesise the answer over the combined evidence ----
    try:
        answer, ans_resp = _generate_synthesis_answer(
            user_msg, history, evidence, cfg, model_key,
            provider=provider, prompts_dir=prompts_dir, max_tokens=synth_max,
            study_digest=study_digest,
        )
    except LLMProviderError:
        return _blocked(state, intent, "llm_error", warning + _LLM_ERROR_TEXT,
                        llm_response=route_resp)
    _accumulate(ctx, ans_resp, cfg)
    record_call(state, ans_resp, cfg)
    if not answer.strip():
        return _blocked(state, intent, "synthesis_answer_failed",
                        warning + _SAFE_FAILURE_TEXT, llm_response=route_resp)

    answer = answer.strip()
    trace = verify_traceability(
        answer, result_set={"evidence": evidence, "digest": study_digest}, user_msg=user_msg
    )
    block_tr, answer = _apply_traceability(trace, answer, analyst_mode)
    if block_tr:
        return _blocked(
            state, intent, "numeric_traceability", warning + _SAFE_FAILURE_TEXT,
            sql=";\n".join(executed_sql), outcome=SQLGateOutcome.PASS,
            traceability=trace, llm_response=route_resp,
        )

    response_text = warning + answer
    state.add_turn("assistant", response_text, {"intent": intent.value, "synthesis": True})
    return ChatTurnResult(
        session_id=state.session_id,
        intent=intent,
        response_text=response_text,
        sql=";\n".join(executed_sql),
        sql_outcome=SQLGateOutcome.PASS,
        result_row_count=sum(len(e["rows"]) for e in evidence),
        traceability=trace,
        llm_response=route_resp,
        blocked=False,
        block_reason=None,
    )


def _load_template_hash(name: str, prompts_dir: Optional[Path]) -> Optional[str]:
    try:
        tpl = (
            load_prompt_template(name, prompts_dir)
            if prompts_dir is not None
            else load_prompt_template(name)
        )
        return tpl.sha256
    except (FileNotFoundError, ValueError):
        return None


def _prompt_template_hashes(
    intent: Optional[IntentLabel], faithfulness_ran: bool, prompts_dir: Optional[Path],
    synthesis_ran: bool = False,
) -> dict:
    """The prompt templates a turn used, by name -> sha256 (FR-3B-08/41)."""
    hashes: dict[str, str] = {}
    for name in (_ROUTING_TEMPLATE,):
        h = _load_template_hash(name, prompts_dir)
        if h:
            hashes[name] = h
    if synthesis_ran:
        for name in (_SYNTH_PLAN_TEMPLATE, _SYNTH_ANSWER_TEMPLATE):
            h = _load_template_hash(name, prompts_dir)
            if h:
                hashes[name] = h
    elif intent in (IntentLabel.FACTUAL_LOOKUP, IntentLabel.EXPLORATORY):
        h = _load_template_hash(_SQLGEN_TEMPLATE, prompts_dir)
        if h:
            hashes[_SQLGEN_TEMPLATE] = h
    elif intent is IntentLabel.COMMENTARY_GENERATION:
        for name in ([_COMMENTARY_TEMPLATE] + ([_FAITHFULNESS_TEMPLATE] if faithfulness_ran else [])):
            h = _load_template_hash(name, prompts_dir)
            if h:
                hashes[name] = h
    return hashes


def _build_audit_row(
    state: SessionState,
    result: ChatTurnResult,
    ctx: dict,
    user_msg: str,
    prompts_dir: Optional[Path],
) -> dict:
    """Map a finished turn to the §D.3 ``gold_ai_audit_log`` field set (FR-3B-47)."""
    turn_index = max(0, sum(1 for t in state.turns if t.get("role") == "user") - 1)
    sql_outcome = result.sql_outcome.value if result.sql_outcome is not None else None
    gate_detail = (
        result.block_reason
        if (result.sql_outcome is not None and result.sql_outcome is not SQLGateOutcome.PASS)
        else None
    )
    trace_passed = result.traceability.passed if result.traceability is not None else None
    untraceable = (
        result.traceability.untraceable_nums
        if (result.traceability is not None and not result.traceability.passed)
        else None
    )
    return {
        "source": "CHATBOT",
        "session_id": state.session_id,
        "turn_index": turn_index,
        "provider": ctx.get("provider"),
        "model_string": ctx.get("model_string") or state.model_key,
        "intent": result.intent.value if result.intent is not None else None,
        "intent_reason": ctx.get("intent_reason"),
        "prompt_template_hashes": _prompt_template_hashes(
            result.intent, ctx.get("faithfulness_ran", False), prompts_dir,
            ctx.get("synthesis_ran", False),
        ),
        "user_message": user_msg,
        "retrieved_context_ref": ctx.get("retrieved_context_ref"),
        "generated_sql": result.sql,
        "sql_gate_outcome": sql_outcome,
        "sql_gate_detail": gate_detail,
        "result_row_count": result.result_row_count,
        "response_text": result.response_text,
        "traceability_passed": trace_passed,
        "untraceable_nums": untraceable,
        "faithfulness_score": ctx.get("faithfulness_score"),
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "input_tokens": ctx.get("input_tokens", 0),
        "output_tokens": ctx.get("output_tokens", 0),
        "est_cost_usd": ctx.get("est_cost_usd", 0.0),
        "latency_ms": ctx.get("latency_ms", 0.0),
    }


def handle_turn(
    user_msg: str,
    state: SessionState,
    cfg: dict,
    mcp_client: MCPClient,
    allowlist: dict[str, set[str]],
    *,
    chatbot_cfg: Optional[dict] = None,
    few_shots: Optional[list[dict]] = None,
    provider: Optional[LLMProvider] = None,
    audit: Optional[Callable[[dict], None]] = None,
    prompts_dir: Optional[Path] = None,
    rag_run_ids: Optional[list[str]] = None,
    rag_artifact_paths: Optional[dict] = None,
    commentary_facts: Optional[dict] = None,
    analyst_mode: Optional[bool] = None,
    multi_query: Optional[bool] = None,
    study_digest: Optional[dict] = None,
) -> ChatTurnResult:
    """Run one guarded chatbot turn end-to-end, with full per-turn audit logging.

    Args:
        user_msg: the user's message.
        state: the mutable :class:`SessionState` (model_key, history, totals).
        cfg: parsed ``llm_config.yaml`` (drives ``complete`` + pricing).
        mcp_client: the read-only MCP client (the only DB path, FR-3B-25).
        allowlist: the shared Gold table->columns allowlist.
        chatbot_cfg: the ``chatbot`` block of ``ai_config.yaml`` (limits, row cap,
            ``rag`` block, faithfulness flags); defaults applied when omitted.
        few_shots: curated Q->SQL pairs inlined into the SQL-gen prompt.
        provider: optional injected provider (tests inject ``MockProvider``/stub).
        audit: optional sink. The pipeline emits ordered ``intent`` /
            ``sql_validation`` events during the turn and one final ``turn`` event
            carrying the full §D.3 field set (FR-3B-47); the DB sink writes the row.
        prompts_dir: optional override for the prompts root (tests).
        rag_run_ids: study/TEV run id(s) the commentary grounds in (FR-3B-36).
        rag_artifact_paths: resolved grounding artifact paths (from
            ``context.resolve_rag_artifacts``); empty -> ungrounded prose.
        commentary_facts: app-assembled, display-rounded fact pack for the
            commentary route (round 3, Phase C); numbers trace to it.
        analyst_mode: opt-in flag-not-block for the numeric post-check (round 3,
            Phase B); ``None`` falls back to ``chatbot.analyst_mode_default``. SQL
            gates are never relaxed.

    Returns:
        A :class:`ChatTurnResult`.
    """
    ctx = _new_audit_ctx()
    result = _run_turn(
        ctx, user_msg, state, cfg, mcp_client, allowlist,
        chatbot_cfg=chatbot_cfg, few_shots=few_shots, provider=provider,
        audit=audit, prompts_dir=prompts_dir,
        rag_run_ids=rag_run_ids, rag_artifact_paths=rag_artifact_paths,
        commentary_facts=commentary_facts, analyst_mode=analyst_mode,
        multi_query=multi_query, study_digest=study_digest,
    )
    if audit is not None:
        row = _build_audit_row(state, result, ctx, user_msg, prompts_dir)
        _audit(audit, {"event": "turn", **row})
    return result
