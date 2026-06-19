"""Deterministic, YAML-driven policy engine.

HOT PATH. Pure in-memory rule evaluation -- no I/O (CLAUDE.md sec. 8, Day 3).
Blocks the obviously dangerous, allows the obviously safe; on block returns a
structured, machine-readable rejection (reason code, human explanation,
suggested fix) so the agent can self-correct. Fail-closed for writes,
fail-open for reads (sec. 4).

Built in Day 3. Stub only for now.
"""
