"""AI layer (Phase 3) — strictly additive (Req §7.2; Tech Spec §E).

Architecture contracts enforced by automated tests (see
``tests/test_ai_architecture.py`` and ``tests/conftest.py``):

* FR-3A-06  Package layout: glm/, gbm/, llm/, chatbot/, mcp_server/, skills/,
            eval/. The hardened SQL boundary lives in ``src/utils/`` because
            non-AI code may also adopt it.
* FR-3A-07  One-way import rule: ``src/ai/`` may import from the core engine
            (``src/calculation/``, ``src/tev/``, ``src/utils/``); the core
            engine must never import from ``src/ai/``. Phases 1-2 run
            identically with this layer absent.
* FR-3A-08  Read contract: reads only the Gold layer + version-controlled
            config/reference files; never Silver or Bronze.
* FR-3A-09  Write contract: writes only to ``data/ai_models/`` and the three
            new Gold tables (ai_model_registry, ai_eval_results, ai_audit_log);
            never to assumption sets, study results, or any Phase 1-2 table.
* FR-3A-02  No SQL string interpolation anywhere in this package; the only
            permitted SQL path is ``src.utils.sql_boundary``.
"""
