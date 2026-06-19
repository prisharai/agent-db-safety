"""Reversibility / instant undo (differentiator #2).

For *allowed writes only* -- never on the read path (CLAUDE.md sec. 4, Day 5).
Records what's needed to reverse a write (before-images / an undo log keyed by a
per-action ID) and exposes ``revert(action_id)``. Every change ties to an agent
identity and its stated task; reverts are themselves audited. Be explicit about
what cannot be perfectly reversed (cascades, external calls, consumed
sequences -- sec. 11).

Built in Day 5. Stub only for now.
"""
