"""Evaluation-harness CLI (Session 22; Req §7.11, Tech Spec §E.9).

Run with ``python -m src.ai.eval``. Flags:

    --models m1,m2   restrict to specific configured model ids (default: all
                     configured models whose provider key is present)
    --smoke          live smoke per provider: one routing + one SQL-gen + one
                     commentary call (FR-3B-54), no scoring
    --golden PATH / --adversarial PATH   override the locked eval-set paths

The CLI shows an up-front (approximate) cost estimate and requires interactive
confirmation when it exceeds ``eval.eval_cost_confirm_threshold`` in
``ai_config.yaml`` (NFR-L-04). It prints a per-model comparison table and exits
non-zero if either hard gate (gate integrity, numeric traceability) fails on any
tested model. It **refuses to run inside pytest** (FR-3B-53).

This module performs no SQL; all data access is via ``run_eval`` -> the gated MCP
client. The eval-results write is the static parameterized INSERT in ``runner``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

import yaml

from src.ai.chatbot.mcp_client import InProcessMCPClient
from src.ai.chatbot.pipeline import load_few_shots
from src.ai.chatbot.session import SessionState
from src.ai.eval.runner import EvalMetrics, load_adversarial, load_golden, run_eval
from src.ai.llm.client import available_models, load_llm_config
from src.ai.prompts import PROMPTS_DIR
from src.utils.db_init import DEFAULT_DB_PATH
from src.utils.sql_boundary import load_allowlist

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
_DEFAULT_GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "eval" / "golden_set.yaml"
_DEFAULT_ADVERSARIAL = Path(__file__).resolve().parents[3] / "tests" / "eval" / "adversarial_set.yaml"

# Crude per-question token estimate (input + output across the ~2-3 LLM calls a
# turn makes); the estimate is flagged approximate and only gates the confirmation.
_TOKENS_PER_QUESTION = 1500


def _assert_not_under_pytest() -> None:
    """Refuse to run inside the pytest suite (FR-3B-53)."""
    if "pytest" in sys.modules:
        raise RuntimeError(
            "The evaluation harness is CLI-only and must not run inside pytest "
            "(FR-3B-53). Invoke it with `python -m src.ai.eval`."
        )


def estimate_cost(n_questions: int, models: list[dict]) -> float:
    """Approximate USD cost of an eval run across ``models`` (NFR-L-04).

    Crude and intentionally conservative: assumes ``_TOKENS_PER_QUESTION`` per
    question split evenly across input/output, priced at each model's per-Mtok
    rates. Returns 0.0 when prices are unset.
    """
    total = 0.0
    half = _TOKENS_PER_QUESTION / 2.0
    for model in models:
        price_in = model.get("price_per_mtok_input") or 0.0
        price_out = model.get("price_per_mtok_output") or 0.0
        total += n_questions * (half * price_in + half * price_out) / 1_000_000.0
    return total


def confirm_cost(
    estimate: float, threshold: float, *, input_fn: Callable[[str], str] = input
) -> bool:
    """Return True to proceed. Below ``threshold`` proceed silently; above it,
    prompt interactively and proceed only on an explicit yes (NFR-L-04)."""
    if estimate <= threshold:
        return True
    prompt = (
        f"Estimated eval cost ~${estimate:.2f} exceeds the "
        f"${threshold:.2f} threshold. Proceed? [y/N] "
    )
    return input_fn(prompt).strip().lower() in ("y", "yes")


def select_models(config: dict, requested: Optional[list[str]]) -> list[dict]:
    """Resolve the models to run: the requested ids (if any) else all configured.

    Disabled models (missing provider key) are returned too so the caller can
    report them; ``run_eval`` only succeeds for enabled ones at runtime.
    """
    models = available_models(config)
    if requested:
        wanted = {m.strip() for m in requested}
        models = [m for m in models if m["model_id"] in wanted]
    return models


def format_table(results: list[EvalMetrics]) -> str:
    """Render the per-model comparison table (FR-3B-51 deliverable)."""
    header = (
        f"{'model':<22} {'exec':>6} {'gate':>6} {'refuse':>7} "
        f"{'route':>6} {'trace':>6}"
    )
    lines = [header, "-" * len(header)]
    for m in results:
        lines.append(
            f"{m.model:<22} {m.execution_accuracy:>6.2f} {m.gate_integrity:>6.2f} "
            f"{m.refusal_correctness:>7.2f} {m.intent_routing_acc:>6.2f} "
            f"{m.numeric_traceability:>6.2f}"
        )
    return "\n".join(lines)


def _hard_gates_pass(m: EvalMetrics) -> bool:
    return m.gate_integrity == 1.0 and m.numeric_traceability == 1.0


def run_smoke(
    model_key: str, cfg: dict, mcp_client, allowlist: dict, chatbot_cfg: dict,
    *, provider=None, prompts_dir: Path = PROMPTS_DIR,
) -> list:
    """Live smoke (FR-3B-54): one routing + one SQL-gen + one commentary turn.

    Drives ``handle_turn`` on three representative prompts so a freshly-configured
    provider can be verified end-to-end without a full scoring run.
    """
    from src.ai.chatbot.pipeline import handle_turn

    prompts = [
        "What is the count-based mortality A/E for Term overall?",
        "Show mortality A/E by attained age band for Whole Life.",
        "Draft a short commentary on the Term mortality results.",
    ]
    out = []
    for i, prompt in enumerate(prompts):
        state = SessionState(session_id=f"smoke-{model_key}-{i}", model_key=model_key)
        out.append(handle_turn(
            prompt, state, cfg, mcp_client, allowlist,
            chatbot_cfg=chatbot_cfg, provider=provider, prompts_dir=prompts_dir,
        ))
    return out


def _load_ai_config() -> dict:
    with (_CONFIG_DIR / "ai_config.yaml").open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    _assert_not_under_pytest()

    parser = argparse.ArgumentParser(prog="python -m src.ai.eval")
    parser.add_argument("--models", default=None,
                        help="comma-separated model ids (default: all configured)")
    parser.add_argument("--smoke", action="store_true",
                        help="live smoke per provider (FR-3B-54); no scoring")
    parser.add_argument("--golden", default=str(_DEFAULT_GOLDEN))
    parser.add_argument("--adversarial", default=str(_DEFAULT_ADVERSARIAL))
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)

    llm_cfg = load_llm_config(_CONFIG_DIR / "llm_config.yaml")
    ai_cfg = _load_ai_config()
    chatbot_cfg = ai_cfg.get("chatbot", {}) or {}
    eval_cfg = ai_cfg.get("eval", {}) or {}
    threshold = float(eval_cfg.get("eval_cost_confirm_threshold", 5.0))
    allowlist = load_allowlist(_CONFIG_DIR / "ai_config.yaml")
    few_shots = load_few_shots(_CONFIG_DIR / "chatbot_few_shots.yaml")

    requested = args.models.split(",") if args.models else None
    models = select_models(llm_cfg, requested)
    enabled = [m for m in models if m["enabled"]]
    for m in models:
        if not m["enabled"]:
            print(f"skipping {m['model_id']}: {m['disabled_reason']}")
    if not enabled:
        print("No enabled models (set the provider API key env vars). Nothing to run.")
        return 0

    mcp_client = InProcessMCPClient(
        Path(args.db), allowlist, row_cap=int(chatbot_cfg.get("sql_row_cap", 500))
    )

    if args.smoke:
        for m in enabled:
            print(f"smoke: {m['model_id']}")
            run_smoke(m["model_id"], llm_cfg, mcp_client, allowlist, chatbot_cfg)
        return 0

    n_questions = len(load_golden(args.golden)) + len(load_adversarial(args.adversarial))
    estimate = estimate_cost(n_questions, enabled)
    print(f"Estimated eval cost: ~${estimate:.2f} (approximate; "
          f"{n_questions} questions × {len(enabled)} model(s)).")
    if not confirm_cost(estimate, threshold):
        print("Aborted before any model call.")
        return 0

    results: list[EvalMetrics] = []
    for m in enabled:
        print(f"running eval: {m['model_id']} ...")
        metrics = run_eval(
            m["model_id"], Path(args.golden), Path(args.adversarial),
            llm_cfg, mcp_client, allowlist,
            chatbot_cfg=chatbot_cfg, few_shots=few_shots,
            db_path=Path(args.db), est_cost_usd=estimate / len(enabled),
        )
        results.append(metrics)

    print("\n" + format_table(results))
    failed = [m for m in results if not _hard_gates_pass(m)]
    if failed:
        print("\nHARD GATE FAILURE on: " + ", ".join(m.model for m in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
