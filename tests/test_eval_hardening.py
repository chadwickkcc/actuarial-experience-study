"""Post-build hardening tests for the eval harness (Session 22 audit).

Adds the coverage that the per-component tests leave open:
 * the ``gold_ai_eval_results`` INSERT column alignment is locked (a silent
   off-by-one would corrupt persisted metrics) — mirrors the Session-21 audit;
 * the harness scores the **real locked adversarial set** end-to-end with the
   hard gate intact;
 * ``results_match`` holds on real MCP-materialised rows when the generated query
   is column- and row-reordered but equivalent;
 * the CLI ``run_smoke`` wiring drives three turns offline.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

from src.ai.chatbot.mcp_client import InProcessMCPClient
from src.ai.eval.__main__ import run_smoke
from src.ai.eval.runner import (
    EvalMetrics,
    _EVAL_COLUMNS,
    _INSERT_EVAL_SQL,
    load_adversarial,
    persist_eval_metrics,
    run_eval,
)
from src.utils.db_init import init_database
from src.utils.types import LLMResponse
from tests.chatbot_helpers import (
    ScriptedProvider,
    StubMCP,
    allowlist,
    chatbot_cfg,
    llm_cfg,
    routing_reply,
    sqlgen_reply,
)

_MODEL = "claude-sonnet-4-6"
_ADVERSARIAL = Path("tests/eval/adversarial_set.yaml")


class _Provider:
    name = "scripted"

    def __init__(self, plan):
        self._plan = plan

    def complete(self, messages, model, max_tokens, temperature=0.0, system=None):
        system = system or ""
        plan = self._plan.get(messages[-1]["content"], {"intent": "OUT_OF_SCOPE"})
        if "Intent router" in system:
            text = routing_reply(plan.get("intent", "OUT_OF_SCOPE"))
        elif "SQL generation" in system:
            text = sqlgen_reply(plan.get("sql", ""), plan.get("template", ""))
        else:
            text = ""
        return LLMResponse(text=text, input_tokens=10, output_tokens=20,
                           provider=self.name, model=model, latency_ms=0.0, stop_reason="end_turn")


def _write_yaml(path, key, items):
    import yaml
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({key: items}, fh, sort_keys=False)


# --------------------------------------------------------------------------- #
# 1. gold_ai_eval_results INSERT column alignment (data-corruption guard)      #
# --------------------------------------------------------------------------- #

def test_eval_insert_column_list_matches_columns_in_order():
    match = re.search(r"gold_ai_eval_results \((.*?)\) VALUES", _INSERT_EVAL_SQL, re.DOTALL)
    cols = [c.strip() for c in match.group(1).split(",")]
    assert cols == _EVAL_COLUMNS


def test_eval_full_row_roundtrips_every_column_in_alignment(tmp_path):
    """Distinct value per column → a swap between any two columns is caught."""
    db = tmp_path / "eval.duckdb"
    init_database(str(db))
    metrics = EvalMetrics(
        model="MODEL_X", execution_accuracy=0.11, gate_integrity=0.22,
        refusal_correctness=0.33, intent_routing_acc=0.44, numeric_traceability=0.55,
        per_question=[{"id": "Z", "ok": True}],
    )
    rid = persist_eval_metrics(
        metrics, n_golden=7, n_adversarial=9, actual_cost_usd=4.56,
        prompt_template_hashes={"routing.md": "abc"}, est_cost_usd=1.23, db_path=db,
    )
    con = duckdb.connect(str(db))
    try:
        row = con.execute(
            "SELECT eval_run_id, model_string, prompt_template_hashes, tool_schema_version, "
            "execution_accuracy, gate_integrity, refusal_correctness, intent_routing_acc, "
            "numeric_traceability, n_golden, n_adversarial, est_cost_usd, actual_cost_usd, "
            "per_question FROM gold_ai_eval_results"
        ).fetchone()
    finally:
        con.close()
    assert row[0] == rid
    assert row[1] == "MODEL_X"
    assert json.loads(row[2]) == {"routing.md": "abc"}
    assert row[3] == "2.0"                       # TOOL_SCHEMA_VERSION (widened 2026-06-27)
    assert (row[4], row[5], row[6], row[7], row[8]) == (0.11, 0.22, 0.33, 0.44, 0.55)
    assert row[9] == 7 and row[10] == 9
    assert row[11] == 1.23 and row[12] == 4.56
    assert json.loads(row[13]) == [{"id": "Z", "ok": True}]


# --------------------------------------------------------------------------- #
# 2. The real locked adversarial set, end-to-end, hard gate intact            #
# --------------------------------------------------------------------------- #

def test_run_eval_over_locked_adversarial_set_holds_hard_gate(tmp_path):
    adversarial = load_adversarial(_ADVERSARIAL)
    plan = {}
    for entry in adversarial:
        if entry["expect"] == "refusal":
            plan[entry["question"]] = {"intent": "OUT_OF_SCOPE"}
        else:  # gate_reject — route to the data path but emit disallowed SQL
            plan[entry["question"]] = {
                "intent": "FACTUAL_LOOKUP", "sql": "DROP TABLE gold_ae_results", "template": "x",
            }
    empty_golden = tmp_path / "g.yaml"
    _write_yaml(empty_golden, "golden", [])

    metrics = run_eval(
        _MODEL, empty_golden, _ADVERSARIAL, llm_cfg(), StubMCP(), allowlist(),
        provider=_Provider(plan), chatbot_cfg=chatbot_cfg(), persist=False,
    )
    assert metrics.gate_integrity == 1.0          # hard gate — nothing disallowed executed
    assert metrics.refusal_correctness == 1.0     # every refusal-expect probe refused
    adv = [p for p in metrics.per_question if p["kind"] == "adversarial"]
    assert len(adv) == len(adversarial)
    assert all(p["expect_ok"] for p in adv)
    assert all(not p["gate_violation"] for p in adv)


# --------------------------------------------------------------------------- #
# 3. results_match on real MCP rows: reordered-but-equivalent generated query  #
# --------------------------------------------------------------------------- #

def test_execution_match_on_reordered_equivalent_query(synthetic_db, tmp_path):
    db = synthetic_db.db_path
    q = "Term deaths split by gender?"
    ref = ("SELECT gender, SUM(actual_deaths_count) AS deaths FROM gold_ae_results "
           "WHERE product_code = 'TERM' GROUP BY gender ORDER BY gender LIMIT 10")
    # Same result, columns swapped + rows in the opposite order.
    gen = ("SELECT SUM(actual_deaths_count) AS deaths, gender FROM gold_ae_results "
           "WHERE product_code = 'TERM' GROUP BY gender ORDER BY gender DESC LIMIT 10")
    plan = {q: {"intent": "EXPLORATORY", "sql": gen, "template": "First-row deaths: {{col:deaths[0]}}."}}

    golden = tmp_path / "g.yaml"
    adv = tmp_path / "a.yaml"
    _write_yaml(golden, "golden", [
        {"id": "G1", "question": q, "intent": "EXPLORATORY", "sql": ref,
         "expected_result": {"columns": ["gender", "deaths"], "row_count": 2, "value_check": True}},
    ])
    _write_yaml(adv, "adversarial", [])

    client = InProcessMCPClient(db, allowlist(), row_cap=500)
    metrics = run_eval(
        _MODEL, golden, adv, llm_cfg(), client, allowlist(),
        provider=_Provider(plan), chatbot_cfg=chatbot_cfg(), persist=False,
    )
    # value_check true → full sorted-multiset compare on reordered real rows.
    assert metrics.execution_accuracy == 1.0
    assert metrics.intent_routing_acc == 1.0


# --------------------------------------------------------------------------- #
# 4. CLI smoke wiring (offline)                                                #
# --------------------------------------------------------------------------- #

def test_run_smoke_offline_wiring():
    provider = ScriptedProvider(
        routing_reply("FACTUAL_LOOKUP"),
        sqlgen_reply("SELECT SUM(exposure_count) AS e FROM gold_ae_results", "Value {{col:e}}."),
    )
    stub = StubMCP(ae={"columns": ["e"], "rows": [[10.0]], "row_count": 1})
    results = run_smoke(_MODEL, llm_cfg(), stub, allowlist(), chatbot_cfg(), provider=provider)
    assert len(results) == 3
    assert all(r.session_id.startswith("smoke-") for r in results)
