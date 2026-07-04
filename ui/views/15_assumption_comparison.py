"""Assumption Comparison — AI Proposals (Phase 3a, Session 17; FR-3A-41..46).

Surfaces the GLM proposal, the GBM challenge, SHAP explainability, and a
read-only TEV what-if on one page. The page proposes, explains, and audits; the
actuary decides. There is **no adopt/apply affordance anywhere on this page**
(FR-3A-44) — adoption happens only in the Stage 2 assumption-set editor, which
records the AI provenance (FR-3A-30). All of the page's own DB queries use
read-only connections (FR-3A-46); the GLM/GBM registration and the TEV what-if
run through the sanctioned engine paths, which manage their own connections.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json as _json

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.config import DB_PATH, CONFIG_DIR
from ui import ai_comparison_logic as logic
from ui import skills_logic as skills
from src.ai.llm.base import LLMProviderError
from src.ai.llm.client import load_llm_config
from src.ai.skills.memo import interpret_ae_and_draft_memo
from src.ai.skills.shap_explain import explain_shap_results
from src.utils.types import DecrementType

st.set_page_config(page_title="Assumption Comparison — AI Proposals", layout="wide")

from ui.config import require_auth
require_auth()
st.title("Assumption Comparison — AI Proposals")
st.markdown(
    "**Read-only.** GLM proposals, the GBM challenge, SHAP explainability, and a "
    "TEV what-if. The AI proposes, explains, and audits — the actuary decides. "
    "No assumption is changed on this page (FR-3A-44); adopt a proposal in "
    "**Stage 2 — Edit Assumptions**, which records the AI provenance."
)

_DECREMENT_LABELS = {
    DecrementType.MORTALITY: "Mortality",
    DecrementType.LAPSE: "Lapse",
    DecrementType.SURRENDER: "Surrender (memo only)",
    DecrementType.CI_INCIDENCE: "CI Incidence",
}
_PRODUCTS = ["TERM", "WL", "UL", "ULSG", "IUL", "VUL", "DA_FIXED", "DA_FIA", "DA_VA"]


@st.cache_data(ttl=60)
def _load_run_ids() -> list[tuple[str, str]]:
    """Return (run_id, label) pairs for completed study runs with A/E results."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.study_run_id, r.run_ts, r.product_codes
            FROM (SELECT DISTINCT study_run_id FROM gold_ae_results) e
            LEFT JOIN gold_study_runs r ON r.run_id = e.study_run_id
            ORDER BY r.run_ts DESC NULLS LAST
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[tuple[str, str]] = []
    for run_id, run_ts, product_codes in rows:
        if run_ts is not None:
            products = ", ".join(_json.loads(product_codes)) if product_codes else "?"
            out.append((run_id, f"{str(run_ts)[:16]} — {products}"))
        else:
            out.append((run_id, run_id))
    return out


# --------------------------------------------------------------------------
# Sidebar selectors (FR-3A-41)
# --------------------------------------------------------------------------
run_pairs = _load_run_ids()
if not run_pairs:
    st.warning("No completed study runs with A/E results. Run a study first.")
    st.stop()

run_ids = [r for r, _ in run_pairs]
run_labels = dict(run_pairs)

with st.sidebar:
    st.header("Selection")
    sel_run = st.selectbox(
        "Study run", options=run_ids, format_func=lambda r: run_labels.get(r, r)
    )
    sel_decrement = st.selectbox(
        "Decrement", options=list(_DECREMENT_LABELS),
        format_func=lambda d: _DECREMENT_LABELS[d],
    )
    sel_product = st.selectbox("Product", options=_PRODUCTS)
    fit_clicked = st.button("Fit AI models", type="primary")

_fit_key = f"ai_fit::{sel_run}::{sel_decrement.value}::{sel_product}"

if fit_clicked:
    with st.spinner("Fitting GLM proposal and GBM challenge (+ bootstrap CIs, SHAP)…"):
        try:
            st.session_state[_fit_key] = logic.fit_models(
                Path(DB_PATH), sel_run, sel_decrement, sel_product
            )
        except Exception as exc:  # noqa: BLE001 — surface fit failures to the user
            st.session_state[_fit_key] = {"error": str(exc)}

result = st.session_state.get(_fit_key)
if result is None:
    st.info("Pick a study run, decrement, and product, then click **Fit AI models**.")
    st.stop()

if "error" in result:
    st.error(f"Fit failed: {result['error']}")
    st.stop()

glm = result.get("glm")
gbm = result.get("gbm")
reasons = result.get("reasons", {})

# Loud-failure "No AI proposal available" state (FR-3A-29)
if glm is None or not getattr(glm, "converged", False) or not getattr(glm, "factors", []):
    reason = " ".join(reasons.values()) or "No AI proposal available."
    st.warning(f"**No AI proposal available** for "
               f"{_DECREMENT_LABELS[sel_decrement]} / {sel_product}. {reason}")
    st.stop()

approved_aset = logic.latest_approved_assumption_set(Path(DB_PATH))

# --------------------------------------------------------------------------
# Comparison table (FR-3A-42)
# --------------------------------------------------------------------------
st.subheader("Factor comparison")
st.caption(
    "Columns are labelled to keep the **proposal** (GLM), the **challenge** "
    "(GBM), and the **approved** basis distinct. The GBM is a reference/challenge "
    "column only — never a proposal."
)
table = logic.build_comparison_table(glm, gbm, approved_aset, sel_decrement)
rename = {
    "ae_derived_factor": "A/E-derived factor",
    "glm_factor": "GLM proposed factor",
    "glm_ci_low": "GLM 95% CI low",
    "glm_ci_high": "GLM 95% CI high",
    "gbm_factor": "GBM reference factor (challenge)",
    "interaction_flag": "Interaction signal",
    "credibility_z": "Credibility Z",
    "expected_events": "Expected events",
    "approved_factor": "Currently-approved factor",
}
display = table.rename(columns=rename)
st.dataframe(display, use_container_width=True)
st.download_button(
    "Download factors (CSV)",
    data=display.to_csv(index=False).encode("utf-8"),
    file_name=f"ai_factors_{sel_decrement.value}_{sel_product}.csv",
    mime="text/csv",
)
if approved_aset is None:
    st.caption("No APPROVED assumption set found — 'Currently-approved factor' shows as blank.")

# --------------------------------------------------------------------------
# TEV impact (what-if) (FR-3A-43)
# --------------------------------------------------------------------------
st.subheader("TEV impact (what-if)")
st.caption(
    "Substitutes the GLM-proposed factors for this decrement-product into an "
    "**in-memory** copy of the approved assumption set and runs the TEV engine. "
    "Logged as a TEV run flagged `what_if_ai_proposal`. **Creates or modifies no "
    "assumption set.** Only the **GLM** proposal is substituted — the GBM is a "
    "challenge/explain model (its interaction-signal flags and SHAP), never adopted "
    "into an assumption set or TEV run (FR-3A-31/43)."
)
if approved_aset is None:
    st.info("A TEV what-if needs an APPROVED assumption set to perturb. None exists yet.")
else:
    if st.button("Run TEV what-if"):
        with st.spinner("Running baseline + what-if TEV projection…"):
            try:
                whatif_aset = logic.build_whatif_assumption_set(
                    approved_aset, sel_decrement, sel_product, glm
                )
                whatif, baseline_total = logic.run_whatif_tev(
                    Path(DB_PATH), approved_aset, whatif_aset
                )
                st.session_state[_fit_key + "::whatif"] = (whatif, baseline_total)
            except Exception as exc:  # noqa: BLE001
                st.session_state[_fit_key + "::whatif"] = {"error": str(exc)}
    wf = st.session_state.get(_fit_key + "::whatif")
    if isinstance(wf, dict) and "error" in wf:
        st.error(f"What-if failed: {wf['error']}")
    elif wf is not None:
        whatif, baseline_total = wf
        c1, c2, c3 = st.columns(3)
        c1.metric("Approved-basis TEV", f"{baseline_total:,.0f}" if baseline_total else "—")
        c2.metric("What-if TEV", f"{whatif.total_tev:,.0f}")
        c3.metric("ΔTEV vs approved",
                  f"{whatif.delta_tev:,.0f}" if whatif.delta_tev is not None else "—")
        per_prod = pd.DataFrame([
            {"product_code": pr.product_code, "tev": pr.tev}
            for pr in whatif.product_results
        ])
        st.dataframe(per_prod, use_container_width=True)

# --------------------------------------------------------------------------
# Diagnostics (FR-3A-23 / FR-3A-32)
# --------------------------------------------------------------------------
with st.expander("Model diagnostics (GLM + GBM)"):
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        st.markdown("**GLM**")
        st.write({
            "deviance": glm.deviance, "dispersion": glm.dispersion,
            "aic": glm.aic, "n_cells": glm.n_cells, "seed": glm.seed,
        })
        diag_path = Path(getattr(glm, "diagnostics_path", "") or "")
        if diag_path and diag_path.exists():
            try:
                st.json(_json.loads(diag_path.read_text()))
            except Exception:  # noqa: BLE001
                st.caption(f"Diagnostics artifact: {diag_path}")
    with dcol2:
        st.markdown("**GBM**")
        if gbm is not None and gbm.factors:
            st.write({
                "cv_metric_name": gbm.cv_metric_name,
                "cv_metric_value": gbm.cv_metric_value,
                "n_cells": gbm.n_cells, "seed": gbm.seed,
                "n_interaction_flags": len(gbm.divergence_flags),
            })
        else:
            st.caption("No GBM challenge produced for this combination.")

# --------------------------------------------------------------------------
# SHAP explainability (FR-3A-38 / FR-3A-40) — read persisted JSON, never recompute
# --------------------------------------------------------------------------
st.subheader("SHAP explainability")
shap_json = logic.load_shap_json(result.get("shap_json_path", ""))
selected_shap_grain = None  # captured for the SHAP narrative Skill below
if shap_json is None:
    st.caption("No persisted SHAP artifact for this combination.")
else:
    grain_options = [fc.grain_key for fc in (gbm.factors if gbm else [])]
    if grain_options:
        idx = st.selectbox(
            "Cell (grain)", options=list(range(len(grain_options))),
            format_func=lambda i: ", ".join(f"{k}={v}" for k, v in grain_options[i].items()),
        )
        selected_shap_grain = grain_options[idx]
        cell = logic.shap_cell_for_grain(shap_json, grain_options[idx])
        if cell is not None:
            contribs = cell.get("contributions", [])
            wf_fig = go.Figure(go.Bar(
                x=[c["shap_value"] for c in contribs],
                y=[c["feature"] for c in contribs],
                orientation="h",
            ))
            wf_fig.update_layout(
                title=(f"SHAP contributions (base {cell.get('base_value', 0):.4f} → "
                       f"prediction {cell.get('prediction', 0):.4f})"),
                xaxis_title="SHAP value (margin space)", height=380,
            )
            st.plotly_chart(wf_fig, use_container_width=True)
            st.caption(
                "The **base value is the model's average prediction (margin space)** "
                "and is the same for every cell of this model; only the per-cell "
                "prediction moves with the feature contributions "
                "(`base + Σ contributions = prediction`)."
            )
        else:
            st.caption("No SHAP cell matches the selected grain.")

    gs = shap_json.get("global_summary", [])
    if gs:
        st.markdown("**Global feature importance (mean |SHAP|)**")
        st.dataframe(pd.DataFrame(gs), use_container_width=True)

# --------------------------------------------------------------------------
# Feature-to-assumption mapping (FR-3A-39)
# --------------------------------------------------------------------------
with st.expander("Feature → assumption mapping"):
    fmap = logic.load_feature_to_assumption().get(sel_decrement.value, {})
    if fmap:
        st.dataframe(
            pd.DataFrame([
                {"feature": k, "actuarial_term": v.get("actuarial_term"),
                 "assumption_dimension": v.get("assumption_dimension")}
                for k, v in fmap.items()
            ]),
            use_container_width=True,
        )
    else:
        st.caption("No feature mapping for this decrement.")

# --------------------------------------------------------------------------
# AI narrative Skills (LIVE — Phase 3b, Session 19) (FR-3B-17..23)
# --------------------------------------------------------------------------
st.subheader("AI narrative Skills")
st.caption(
    "Prompt-artifact Skills run on the selected model. Every output is an **AI "
    "draft** — each number is checked against the study data; an untraceable "
    "number **blocks** the draft (never repaired). Adopt nothing here."
)

_llm_cfg = load_llm_config(CONFIG_DIR / "llm_config.yaml")
_skill_models = skills.available_skill_models(CONFIG_DIR)


def _model_label(model_id: str) -> str:
    for m in _skill_models:
        if m["model_id"] == model_id:
            suffix = "" if m["enabled"] else f" — {m['disabled_reason']}"
            return f"{m['display_name']}{suffix}"
    return model_id


def _render_skill_output(out: dict, file_stem: str) -> None:
    if out.get("blocked"):
        msg = out.get("reason") or "Draft blocked (not repaired)."
        nums = out.get("untraceable_nums") or []
        if nums:
            msg += f" Untraceable: {', '.join(nums)}"
        st.error(msg)
    else:
        st.markdown(out["markdown"])
        st.download_button(
            "Download draft (.md)",
            data=out["markdown"].encode("utf-8"),
            file_name=f"{file_stem}.md",
            mime="text/markdown",
        )
    if out.get("hashes"):
        st.caption(
            "Prompt template hashes: "
            + ", ".join(f"`{k}`={v[:12]}…" for k, v in out["hashes"].items())
        )


if not _skill_models:
    st.info("No models configured in llm_config.yaml.")
else:
    sel_model = st.selectbox(
        "Model", options=[m["model_id"] for m in _skill_models],
        format_func=_model_label,
    )
    sc1, sc2 = st.columns(2)

    if sc1.button("Draft A/E memo"):
        with st.spinner("Drafting memo…"):
            try:
                memo_input = skills.assemble_memo_input(
                    Path(DB_PATH), sel_run, sel_decrement, sel_product, glm=glm, gbm=gbm
                )
                st.session_state[_fit_key + "::memo"] = interpret_ae_and_draft_memo(
                    memo_input, _llm_cfg, sel_model
                )
            except LLMProviderError as exc:
                st.session_state[_fit_key + "::memo"] = {"_provider_error": str(exc)}

    if sc2.button("Explain SHAP results"):
        if shap_json is None or selected_shap_grain is None:
            st.warning("Pick a SHAP cell (grain) above first.")
        else:
            with st.spinner("Explaining SHAP cell…"):
                try:
                    cell_input = skills.assemble_shap_cell_input(shap_json, selected_shap_grain)
                    fmap = skills.feature_map_for_decrement(
                        logic.load_feature_to_assumption(), sel_decrement
                    )
                    st.session_state[_fit_key + "::shap"] = explain_shap_results(
                        cell_input, fmap, _llm_cfg, sel_model
                    )
                except LLMProviderError as exc:
                    st.session_state[_fit_key + "::shap"] = {"_provider_error": str(exc)}

    memo_out = st.session_state.get(_fit_key + "::memo")
    if memo_out is not None:
        st.markdown("#### A/E memo (AI-draft)")
        if "_provider_error" in memo_out:
            st.error(memo_out["_provider_error"])
        else:
            _render_skill_output(memo_out, f"ae_memo_{sel_decrement.value}_{sel_product}")

    shap_out = st.session_state.get(_fit_key + "::shap")
    if shap_out is not None:
        st.markdown("#### SHAP explanation (AI-draft)")
        if "_provider_error" in shap_out:
            st.error(shap_out["_provider_error"])
        else:
            _render_skill_output(shap_out, f"shap_explain_{sel_decrement.value}_{sel_product}")
