"""Intent-mismatch detection (advisory only).

NEVER on the hot path; never load-bearing (CLAUDE.md sec. 4, Day 6). Compares
the agent's stated task to the query's measured blast radius and raises an
advisory flag on large contradictions. Optional LLM "second opinion" is behind
a flag, async/out-of-band, on the risky subset only. Reframe: we don't "know
intent," we *detect contradiction*. Nothing blocks on intent alone (sec. 11).

Built in Day 6. Stub only for now.
"""
