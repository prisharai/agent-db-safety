"""Phase A transport: MCP server.

Exposes a ``run_query`` tool that agents (Claude Code, Cursor, ...) call as
their database tool. Forwards statements to the engine and returns results.
A thin shim only -- all parse/classify/policy/simulate/undo logic lives in
``engine/`` (CLAUDE.md sec. 5).

Day 1 turns this into a pass-through + shadow-mode server. Stub only for now.
"""
