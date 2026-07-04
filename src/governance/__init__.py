"""Phase 4 — Governance layer (single-org).

Additive application code that gives the tool a real identity foundation,
server-side RBAC, version lineage, a configurable approval chain, hash-chained
audit, and governance reporting (Requirements v4.0 §8 / Tech Spec v3.0 §G-I).

Design constraints:
- Governance lives OUTSIDE ``src/ai/`` and is ordinary application code: it uses
  the standard parameterized ``duckdb.connect()`` write path (NOT the AI
  read-only ``src/utils/sql_boundary``), while still using static ``?``-placeholder
  SQL (never string interpolation).
- Org-specific values come from ``config/governance_config.yaml`` (FR-4-27).
- No new Claude Skills or MCP servers (Requirements §11.3).

Session 23 (this build): identity & access — ``auth``, ``users``, ``rbac``.
"""
