"""Asynchronous, non-blocking audit log.

A query must NEVER wait on a log write (CLAUDE.md sec. 4). This log is also the
traffic corpus -- a record of real agent-generated SQL paired, where possible,
with the agent's stated task -- which feeds the red/green corpora and
intent-mismatch detection (sec. 10).

Built in Day 1. Stub only for now.
"""
