"""Fraud-detection rule engine (demo refresh P5).

Rule-based indicators + a weighted composite score over claim-level data,
mirroring the ``src/data_quality/`` pattern: pure rule functions in
``rules.py``, orchestration + persistence in ``runner.py``, every threshold
in ``config/fraud_config.yaml``.

Ordinary application code OUTSIDE ``src/ai/``: reads Silver claim data via
parameterized read-only queries (the standard engine path, NOT the AI
``sql_boundary``); writes only the three ``gold_fraud_*`` tables. The LLM
narrative over the scan results lives in ``src/ai/skills/fraud_narrative.py``
and receives AGGREGATES ONLY — never ``policy_id`` or ``claimant_id``.
"""

from src.fraud.rules import ALL_RULES, FraudRuleResult, build_claims_frame
from src.fraud.runner import FraudRunResult, load_fraud_config, run_fraud_scan

__all__ = [
    "ALL_RULES",
    "FraudRuleResult",
    "FraudRunResult",
    "build_claims_frame",
    "load_fraud_config",
    "run_fraud_scan",
]
