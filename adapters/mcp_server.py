"""Phase A transport: MCP server (Day 1 -- pass-through + shadow mode).

Exposes a ``run_query`` tool that agents (Claude Code, Cursor, ...) call as
their database tool. Day 1 is deliberately a *pass-through*: every statement is
forwarded to Postgres unchanged and the result returned unchanged. We **log
every statement and block nothing** (shadow mode, CLAUDE.md sec. 8 Day 1). This
is the baseline we benchmark against later and the traffic corpus we mine
(sec. 10).

Architecture (sec. 5): this adapter stays thin. The pass-through "session" below
is pure forward-and-log with **zero policy** -- there is no policy engine yet.
When Day 3 adds parse/classify/policy in ``engine/``, the decision moves there
and this adapter calls into it; the transport code does not grow policy logic.

Latency posture (sec. 4): the only request-path work here is a pool acquire +
one round trip to Postgres (the unavoidable cost of running the query at all)
plus a single non-blocking ``audit.record`` enqueue. Logging never blocks the
query; see ``engine/audit.py``.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
from mcp.server.fastmcp import Context, FastMCP

from engine.audit import AuditLog
from engine.classifier import classify
from engine.policy import Policy, apply_blast_radius, evaluate
from engine.simulate import is_risky_write, simulate

# --- Config (env-overridable; defaults match docker-compose.yml) -------------
DB_DSN = os.environ.get(
    "AGENT_DB_DSN",
    "postgresql://postgres:postgres@localhost:5433/pagila",
)
AUDIT_LOG_PATH = os.environ.get("AGENT_AUDIT_LOG", "logs/audit.jsonl")
# Policy file loaded once at startup (off the hot path). Defaults to the
# repo's default policy.
POLICY_PATH = os.environ.get(
    "AGENT_POLICY",
    str(Path(__file__).resolve().parent.parent / "policies" / "default.yaml"),
)
# Pool is opened once at startup; per-query cost is just an acquire (cheap when
# a connection is free). Sized small for dev.
POOL_MIN_SIZE = int(os.environ.get("AGENT_POOL_MIN", "1"))
POOL_MAX_SIZE = int(os.environ.get("AGENT_POOL_MAX", "10"))


class ShadowSession:
    """Forward a statement, apply policy, log it, return it.

    Transport-agnostic by construction -- it depends only on an asyncpg pool, an
    :class:`AuditLog`, and an optional :class:`Policy`, not on MCP. That keeps it
    unit-testable without a live MCP client (see the tests).

    With ``policy=None`` it is a pure pass-through (Day 1/2 shadow behaviour).
    With a policy it enforces (Day 3): a blocked statement is rejected *before*
    touching the database, with a structured, machine-readable explanation. In a
    policy whose ``mode == "observe"`` the decision is computed and logged but the
    statement still runs -- a safe way to trial a policy against live traffic.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        audit: AuditLog,
        policy: Policy | None = None,
    ) -> None:
        self._pool = pool
        self._audit = audit
        self._policy = policy

    async def run_query(
        self,
        sql: str,
        *,
        stated_task: str | None = None,
        agent: str | None = None,
        operator_approved: bool = False,
    ) -> dict[str, Any]:
        """Apply policy + (gated) simulation, then execute and return the result.

        Result shape: ``{"status", "rows", "row_count", "error", "blocked",
        "violations", "requires_confirmation", "simulation"}``. When a statement
        is blocked, ``blocked=True`` and ``violations`` explains why (the DB is
        never touched). When a risky write's blast radius crosses the confirm
        threshold, ``requires_confirmation=True`` with the ``simulation`` measured
        and the write is held -- NOT executed.

        ``operator_approved`` is the out-of-band approval seam: it is **not**
        exposed through the MCP tool, so an agent can never set it and cannot
        approve its own write (that would not be human confirmation). Only a
        server-side/operator caller (a real human channel lands in Day 6) can pass
        it. It never overrides a hard block (e.g. blast radius over the limit).
        """
        # Parse + classify on the hot path -- both LRU-cached, pure, ~0.1 ms cold.
        classification = classify(sql)

        # Deterministic policy decision (pure in-memory, no I/O). With no policy,
        # behave as a pass-through (allow everything, rewrite nothing).
        decision = (
            evaluate(sql, classification, self._policy)
            if self._policy is not None
            else None
        )
        # Fail closed: enforce unless the mode is *explicitly* "observe". An
        # unknown/typo'd mode therefore enforces rather than silently passing
        # traffic through (Policy also rejects invalid modes at load time).
        enforce = self._policy is not None and self._policy.mode != "observe"

        # Blast-radius simulation (Day 4) -- OFF the normal path: only a risky
        # write, only when enforcing and enabled. May escalate the decision to
        # blocked (over the block limit) or to requires_confirmation (sec. 4).
        if (
            decision is not None
            and decision.allowed
            and enforce
            and self._policy.simulation.enabled
            and is_risky_write(classification)
        ):
            async with self._pool.acquire() as sim_conn:
                sim = await simulate(
                    sim_conn, sql, classification, self._policy.simulation
                )
            decision = apply_blast_radius(decision, sim, self._policy.simulation)

        # Blocked + enforcing: reject without going near the database.
        if decision is not None and not decision.allowed and enforce:
            self._audit.record(
                {
                    "event": "query",
                    "agent": agent,
                    "stated_task": stated_task,
                    "sql": sql,
                    "blocked": True,
                    "violations": [v.to_dict() for v in decision.violations],
                    "simulation": decision.simulation,
                    "classification": classification.to_dict(),
                }
            )
            return {
                "status": None,
                "rows": [],
                "row_count": 0,
                "error": None,
                "blocked": True,
                "violations": [v.to_dict() for v in decision.violations],
                "requires_confirmation": False,
                "simulation": decision.simulation,
            }

        # Allowed but gated: a risky write whose blast radius needs confirmation.
        # Held until an out-of-band operator approves (not the agent). Never
        # touches the DB otherwise.
        if (
            decision is not None
            and decision.requires_confirmation
            and enforce
            and not operator_approved
        ):
            self._audit.record(
                {
                    "event": "query",
                    "agent": agent,
                    "stated_task": stated_task,
                    "sql": sql,
                    "blocked": False,
                    "requires_confirmation": True,
                    "simulation": decision.simulation,
                    "classification": classification.to_dict(),
                }
            )
            return {
                "status": None,
                "rows": [],
                "row_count": 0,
                "error": None,
                "blocked": False,
                "violations": [],
                "requires_confirmation": True,
                "simulation": decision.simulation,
            }

        # Allowed, or observe-mode: decide what actually runs. Only *enforcing*
        # mode applies a rewrite (e.g. injected LIMIT); observe mode must run the
        # original SQL unchanged so a shadow rollout never alters live results --
        # the decision's would-be effective_sql is still logged below.
        if enforce and decision is not None:
            effective_sql = decision.effective_sql
            rewritten = decision.rewritten
        else:
            effective_sql = sql
            rewritten = False

        started = time.perf_counter()
        status: str | None = None
        rows: list[dict[str, Any]] = []
        error: str | None = None

        try:
            async with self._pool.acquire() as conn:
                # prepare()+fetch() runs the statement once and exposes BOTH the
                # returned rows and the command tag (status) -- see DECISIONS.
                stmt = await conn.prepare(effective_sql)
                records = await stmt.fetch()
                status = stmt.get_statusmsg()
                rows = [dict(r) for r in records]
        except asyncpg.PostgresError as exc:
            # Surface the DB's own error to the agent verbatim; don't editorialize.
            error = f"{type(exc).__name__}: {exc}"

        elapsed_ms = (time.perf_counter() - started) * 1000.0

        # Non-blocking enqueue -- the query does not wait on this (sec. 4).
        self._audit.record(
            {
                "event": "query",
                "agent": agent,
                "stated_task": stated_task,
                "sql": sql,
                "effective_sql": effective_sql if rewritten else None,
                "status": status,
                "rows_returned": len(rows),
                "error": error,
                "duration_ms": round(elapsed_ms, 3),
                "blocked": False,
                "decision": decision.to_dict() if decision is not None else None,
                "classification": classification.to_dict(),
            }
        )

        return {
            "status": status,
            "rows": rows,
            "row_count": len(rows),
            "error": error,
            "blocked": False,
            "violations": [],
            "requires_confirmation": False,
            "simulation": decision.simulation if decision is not None else None,
        }


@dataclass
class AppContext:
    """Resources shared across requests, set up once in the lifespan."""

    session: ShadowSession
    audit: AuditLog
    pool: asyncpg.Pool
    policy: Policy


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Open the pool + audit log and load the policy on startup; close cleanly.

    Policy loading (YAML -> Policy) happens here, once, off the hot path (sec. 4).
    """
    policy = Policy.load(POLICY_PATH)
    pool = await asyncpg.create_pool(
        dsn=DB_DSN, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE
    )
    audit = AuditLog(AUDIT_LOG_PATH)
    await audit.start()
    try:
        yield AppContext(
            session=ShadowSession(pool, audit, policy),
            audit=audit,
            pool=pool,
            policy=policy,
        )
    finally:
        await audit.stop()
        await pool.close()


mcp = FastMCP("agent-db-safety", lifespan=lifespan)


@mcp.tool()
async def run_query(
    sql: str,
    ctx: Context,
    stated_task: str | None = None,
) -> dict[str, Any]:
    """Run a SQL statement against the database and return its result.

    The statement is parsed, classified, and checked against the deterministic
    policy. If it's blocked, the result has ``blocked=True`` and a ``violations``
    list explaining why and how to fix it -- the database is not touched. If it's
    allowed it runs (a read may come back with an injected LIMIT).

    A risky write may be simulated first to measure its blast radius. If that
    exceeds the confirm threshold the result has ``requires_confirmation=True``
    and a ``simulation`` summary (e.g. "would affect 2.3M rows") and the write is
    held. There is deliberately no agent-facing way to approve it -- an agent
    confirming its own write is not human confirmation; approval is out-of-band
    (Day 6). ``stated_task`` is the agent's description of what it's doing --
    captured for intent-mismatch detection later (sec. 10); advisory.
    """
    app: AppContext = ctx.request_context.lifespan_context
    # MCP gives us a stable client/session id; use it as the agent identity.
    agent = getattr(ctx, "client_id", None) or ctx.request_id
    return await app.session.run_query(sql, stated_task=stated_task, agent=agent)


def main() -> None:
    """Entry point: run the MCP server over stdio (the standard MCP transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
