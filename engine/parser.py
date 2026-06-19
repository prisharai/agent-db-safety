"""SQL -> AST parsing via ``pglast`` (libpg_query, the real Postgres parser).

HOT PATH. Every agent statement is parsed here while the agent waits, so this
must be fast and cached (CLAUDE.md sec. 4). Never classify by string matching --
always operate on the AST (sec. 6). Parse failures fail closed for writes.

Built in Day 2. Stub only for now.
"""
