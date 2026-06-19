"""Transport-agnostic safety engine core.

This package is the load-bearing safety logic: parse -> classify -> policy ->
(simulate?) -> decide, plus undo recording and async audit/intent. It knows
nothing about MCP or the wire protocol; adapters in ``adapters/`` are thin
shims over this engine (CLAUDE.md sec. 5). Keep policy logic *here*, never in an
adapter, so the engine stays pure, independently testable, and portable to Go.
"""
