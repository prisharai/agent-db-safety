"""Thin transport adapters over the safety engine.

Phase A: an MCP server (``mcp_server.py``). Phase B (stretch): a Postgres
wire-protocol proxy (``wire_proxy/``). Adapters translate a transport into
engine calls and back -- they contain NO policy logic (CLAUDE.md sec. 5).
"""
