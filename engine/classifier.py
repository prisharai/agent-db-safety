"""Structural classification of a parsed statement.

HOT PATH. Given an AST, determine: read / write / DDL; tables and columns
touched; presence/absence of a WHERE clause; multi-statement detection;
references to system catalogs (CLAUDE.md sec. 8, Day 2). Pure in-memory work.

Built in Day 2. Stub only for now.
"""
