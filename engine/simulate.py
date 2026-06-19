"""Blast-radius simulation (differentiator #1).

OFF the normal request path. Only flagged *risky writes* are simulated, never
reads or routine traffic (CLAUDE.md sec. 4, Day 4). Cheap path: ``EXPLAIN`` (no
execution, no locks) for cost/row estimates. Precise path:
``BEGIN; <stmt>; <capture affected rows>; ROLLBACK`` for flagged writes only,
behind strict guards -- hard ``statement_timeout`` and ``lock_timeout``,
opt-in config, clean abort. Caveats (triggers/sequences/external side-effects
may not roll back) are documented honestly (sec. 11).

Built in Day 4. Stub only for now.
"""
