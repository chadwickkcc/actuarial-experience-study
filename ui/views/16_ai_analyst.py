"""AI Analyst — guarded conversational interface over study results (Session 21).

Hosts the Session-20/21 chatbot pipeline (FR-3B-43): a provider/model dropdown
listing exactly the configured models (greyed when the API key is missing), a
study-run selector that scopes data + RAG grounding, a running token/cost display
with budget warning, conversational Q&A and RAG-grounded commentary (carrying the
persistent "AI-drafted — pending actuary review" banner), and Markdown export.

Every turn is audited to ``gold_ai_audit_log`` (queryable from the Study Run Log
page, NFR-A-07). The page never opens a write connection itself; data access flows
through the in-process MCP client and the only write is the audit sink.
"""
from __future__ import annotations

import uuid

import streamlit as st

from src.ai.chatbot.session import SessionState
from ui import ai_analyst_logic as logic
from ui.config import DB_PATH, require_auth

require_auth()

st.title("🧠 AI Analyst")
st.caption(
    "Ask about the loaded experience-study and TEV results, or request a "
    "narrative commentary. Answers are grounded in this tool's own results and "
    "reports — numbers are filled from the database, never invented. Commentary "
    "is AI-drafted and requires actuary review."
)

if not DB_PATH.exists():
    st.warning("No study database found yet. Run a study first on the Study Setup page.")
    st.stop()

# --- Model + run selection --------------------------------------------------
models = logic.available_analyst_models()
model_ids = [m["model_id"] for m in models]


def _model_label(model_id: str) -> str:
    for m in models:
        if m["model_id"] == model_id:
            suffix = "" if m["enabled"] else f" — {m['disabled_reason']}"
            return f"{m['display_name']}{suffix}"
    return model_id


col_model, col_run = st.columns(2)
with col_model:
    default_idx = (
        model_ids.index(logic.default_model_key())
        if logic.default_model_key() in model_ids
        else 0
    )
    selected_model = st.selectbox(
        "Model", options=model_ids, index=default_idx if model_ids else 0,
        format_func=_model_label,
    )
with col_run:
    runs = logic.list_study_runs(DB_PATH)
    run_options = [r["run_id"] for r in runs]
    run_label = {r["run_id"]: r["label"] for r in runs}
    selected_run = st.selectbox(
        "Study run (scopes data + commentary grounding)",
        options=run_options or [None],
        format_func=lambda rid: run_label.get(rid, "— no runs —"),
    )

if selected_model and not next(
    (m["enabled"] for m in models if m["model_id"] == selected_model), True
):
    st.info(
        f"{_model_label(selected_model)}. Configure the provider's API key to use "
        "this model; other models remain available."
    )

# --- Sidebar: optional faithfulness check (FR-3B-46) ------------------------
with st.sidebar:
    st.subheader("AI Analyst options")
    faithfulness_on = st.toggle(
        "Faithfulness check on commentary",
        value=False,
        help=(
            "When on, AI-drafted commentary is scored 1–5 against the grounding "
            "report/methodology; a low score adds a 'review carefully' note. It "
            "flags, never blocks — the deterministic numeric checks always run."
        ),
    )
    analyst_mode_on = st.toggle(
        "Analyst mode (allow unverified reasoning)",
        value=True,
        help=(
            "ON (default): the model may synthesise/reason/estimate beyond the "
            "fetched figures, and any untraceable number is shown with a "
            "'⚠ unverified figures' warning instead of being blocked — so the "
            "analyst answers freely rather than refusing. OFF: every number must "
            "trace to the database or the answer is blocked (the strict guarantee). "
            "The SQL safety gates and the database-only data path never relax in "
            "either mode."
        ),
    )
    if analyst_mode_on:
        st.caption("⚠ Analyst mode is ON — answers may contain unverified figures.")
    deep_analysis_on = st.toggle(
        "Deep analysis (multi-query reasoning)",
        value=True,
        help=(
            "On (default): exploratory questions are answered by gathering several "
            "breakdowns and synthesising across them — richer, but a little slower "
            "and more tokens. Off: a single query per question. Figures are filled "
            "from the database either way."
        ),
    )

# --- Session state ----------------------------------------------------------
if "analyst_state" not in st.session_state:
    st.session_state.analyst_state = SessionState(
        session_id=str(uuid.uuid4()), model_key=selected_model or "",
    )
state: SessionState = st.session_state.analyst_state
# Honour a mid-session model switch (FR-3B-45) — logged on the next turn.
if selected_model and state.model_key != selected_model:
    state.model_key = selected_model

if st.button("Start a fresh session"):
    st.session_state.analyst_state = SessionState(
        session_id=str(uuid.uuid4()), model_key=selected_model or "",
    )
    st.rerun()

# --- Budget / cost display (FR-3B-43/44) ------------------------------------
cb_cfg = logic.chatbot_config()
budget = int(cb_cfg.get("session_token_budget", 1_000_000))
warn_fraction = float(cb_cfg.get("budget_warning_fraction", 0.8))
m1, m2, m3 = st.columns(3)
m1.metric("Tokens used", f"{state.tokens_used:,}")
m2.metric("Est. cost (USD)", f"${state.cost_estimate:.4f}")
m3.metric("Budget", f"{budget:,}")
if state.tokens_used >= budget:
    st.error("Session token budget reached — start a fresh session to continue.")
elif state.tokens_used >= warn_fraction * budget:
    st.warning("Approaching the session token budget (80%+). Consider a fresh session soon.")

# --- Conversation history ---------------------------------------------------
for turn in state.turns:
    with st.chat_message("user" if turn.get("role") == "user" else "assistant"):
        st.markdown(str(turn.get("content", "")))

# --- Example prompts & commentary affordance --------------------------------
# Commentary is reached by phrasing the request as a narrative ("draft a
# commentary…"); these one-click examples make both the data and commentary paths
# discoverable. A commentary turn returns an AI-drafted narrative carrying the
# persistent "AI-drafted — pending actuary review" banner.
_EXAMPLES = [
    "Provide the overall mortality A/E for Whole Life.",
    "Show the mortality A/E by attained age band for Whole Life.",
    "Which products are covered in this study?",
    "What is the overall lapse A/E for Universal Life?",
    "Draft a short commentary on the Whole Life mortality experience.",
    "Draft a commentary summarising the lapse experience across products.",
]
with st.expander("Example questions & commentary prompts", expanded=not state.turns):
    st.caption(
        "Click to ask. The last two draft a narrative **commentary** (AI-drafted, "
        "pending actuary review); the rest return figures filled from the database."
    )
    ex_cols = st.columns(2)
    for i, example in enumerate(_EXAMPLES):
        if ex_cols[i % 2].button(example, key=f"ai_example_{i}", use_container_width=True):
            st.session_state.analyst_pending = example

# --- New turn ---------------------------------------------------------------
typed = st.chat_input("Ask about A/E, exposure, credibility, TEV — or request commentary")
prompt = typed or st.session_state.pop("analyst_pending", None)
if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                result = logic.run_turn(
                    prompt, state, run_id=selected_run, db_path=DB_PATH,
                    faithfulness=faithfulness_on, analyst_mode=analyst_mode_on,
                    multi_query=deep_analysis_on,
                )
                st.markdown(result.response_text)
            except Exception as exc:  # pragma: no cover - surfaced, never crash
                st.error(f"The assistant could not complete that turn: {exc}")
    st.rerun()

# --- Export (FR-3B-43) ------------------------------------------------------
if state.turns:
    st.download_button(
        "Export conversation (Markdown)",
        data=logic.export_conversation_markdown(state).encode("utf-8"),
        file_name=f"ai_analyst_{state.session_id[:8]}.md",
        mime="text/markdown",
    )
