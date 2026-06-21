"""Blast-radius simulation (differentiator #1).

OFF the normal request path. Only flagged *risky writes* are simulated, never
reads or routine traffic (CLAUDE.md sec. 4, Day 4). Two paths:

* **Cheap (estimate):** ``EXPLAIN (FORMAT JSON) <stmt>`` -- the planner's row /
  cost estimate. No execution, no row locks. Always run when simulation is on.
* **Precise (exact):** ``BEGIN; SET LOCAL statement_timeout/lock_timeout;
  <stmt>; ROLLBACK``. Actually executes the write to read the exact affected-row
  count off the command tag, then rolls it back. This takes locks, so it is
  strictly gated, time-boxed, and aborts cleanly.

Honest caveats (sec. 11): the precise path *executes* the statement before
rolling back, so side effects that don't roll back still happen -- sequence
increments (``nextval`` is not reclaimed on rollback), triggers that call
external services, ``NOTIFY``, etc. It also briefly holds the same locks the real
write would. That's why it is opt-in and only ever runs on a flagged risky write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from asyncpg.exceptions import LockNotAvailableError, QueryCanceledError

from engine.classifier import WRITE, Classification


@dataclass(frozen=True)
class SimulationConfig:
    """When and how to simulate. Default OFF -- simulation is opt-in (sec. 4)."""

    enabled: bool = False
    precise: bool = True  # also run BEGIN/ROLLBACK for an exact count
    statement_timeout_ms: int = 1000  # hard cap on the precise run
    lock_timeout_ms: int = 200  # don't wait on contended locks
    block_over_rows: int | None = None  # block if blast radius exceeds this
    confirm_over_rows: int | None = None  # require confirmation above this

    @classmethod
    def from_dict(cls, data: dict | None) -> SimulationConfig:
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            precise=bool(data.get("precise", True)),
            statement_timeout_ms=int(data.get("statement_timeout_ms", 1000)),
            lock_timeout_ms=int(data.get("lock_timeout_ms", 200)),
            block_over_rows=data.get("block_over_rows"),
            confirm_over_rows=data.get("confirm_over_rows"),
        )


@dataclass(frozen=True)
class SimulationResult:
    """What a simulation learned about a statement's blast radius."""

    method: str  # "precise" | "estimate" | "skipped"
    estimated_rows: int | None = None
    estimated_cost: float | None = None
    exact_rows: int | None = None
    timed_out: bool = False
    error: str | None = None

    @property
    def affected_rows(self) -> int | None:
        """Best available row count -- exact if we have it, else the estimate."""
        return self.exact_rows if self.exact_rows is not None else self.estimated_rows

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "estimated_rows": self.estimated_rows,
            "estimated_cost": self.estimated_cost,
            "exact_rows": self.exact_rows,
            "affected_rows": self.affected_rows,
            "timed_out": self.timed_out,
            "error": self.error,
        }


_SKIPPED = SimulationResult(method="skipped")

# Statement shapes worth simulating: bulk-mutating writes whose blast radius is
# not obvious from the text. INSERT (you know what you're inserting) and scoped
# point writes (UPDATE/DELETE ... WHERE col = value) are routine -> not simulated.
_RISKY_WRITE_STMTS = {"UpdateStmt", "DeleteStmt", "MergeStmt"}


def is_risky_write(classification: Classification) -> bool:
    """Gate: only a *risky-shaped* single write is simulated (sec. 4).

    Risky = a single-statement UPDATE/DELETE/MERGE that is not a point write, or
    a data-modifying CTE (routed here so it fails closed -- see ``simulate``).
    Routine writes (point updates/deletes by key, plain INSERTs) are skipped, so
    they pay no extra round trips, locks, or rollback side effects.
    """
    if classification.statement_count != 1 or not classification.statements:
        return False
    s = classification.statements[0]
    if s.kind != WRITE:
        return False
    if s.nested_dml:
        return True  # data-modifying CTE: simulate so it fails closed (P0)
    if s.stmt_type in _RISKY_WRITE_STMTS:
        return not s.point_write
    return False


def _rows_from_tag(tag: str | None) -> int | None:
    """Affected-row count from a command tag: 'UPDATE 664'/'DELETE 10'/'INSERT 0 5'."""
    if not tag:
        return None
    last = tag.split()[-1]
    return int(last) if last.isdigit() else None


async def _estimate(
    conn, sql: str, config: SimulationConfig
) -> tuple[int | None, float | None, str | None, bool]:
    """Planner estimate via EXPLAIN. Time-boxed (sec. 4).

    Returns (rows, cost, error, timed_out). EXPLAIN still takes an AccessShare
    lock for planning, so we run it inside a transaction with SET LOCAL
    statement/lock timeouts -- otherwise it could hang on a contended relation
    lock or pathological planner work. Read-only; always rolled back.
    """
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            f"SET LOCAL statement_timeout = {int(config.statement_timeout_ms)}"
        )
        await conn.execute(f"SET LOCAL lock_timeout = {int(config.lock_timeout_ms)}")
        raw = await conn.fetchval(f"EXPLAIN (FORMAT JSON) {sql}")
        plan = json.loads(raw)[0]["Plan"]
        return plan.get("Plan Rows"), plan.get("Total Cost"), None, False
    except (QueryCanceledError, LockNotAvailableError) as exc:
        return None, None, type(exc).__name__, True
    except Exception as exc:  # malformed plan, planner error -- estimate is optional
        return None, None, f"{type(exc).__name__}: {exc}", False
    finally:
        await tr.rollback()


async def _exact(
    conn, sql: str, config: SimulationConfig
) -> tuple[int | None, bool, str | None]:
    """Exact affected rows via BEGIN; <stmt>; ROLLBACK, hard time-boxed.

    Returns (exact_rows, timed_out, error). Always rolls back -- the write is
    never committed.
    """
    tr = conn.transaction()
    await tr.start()
    try:
        # SET LOCAL is transaction-scoped; values are ints from config (safe to
        # inline -- SET LOCAL does not accept bound parameters).
        await conn.execute(
            f"SET LOCAL statement_timeout = {int(config.statement_timeout_ms)}"
        )
        await conn.execute(f"SET LOCAL lock_timeout = {int(config.lock_timeout_ms)}")
        tag = await conn.execute(sql)
        return _rows_from_tag(tag), False, None
    except (QueryCanceledError, LockNotAvailableError) as exc:
        # Hit the statement/lock timeout -- abort cleanly, report it.
        return None, True, type(exc).__name__
    except Exception as exc:
        return None, False, f"{type(exc).__name__}: {exc}"
    finally:
        await tr.rollback()


async def simulate(
    conn, sql: str, classification: Classification, config: SimulationConfig
) -> SimulationResult:
    """Measure a statement's blast radius. Only ever runs on a risky write.

    Returns a ``skipped`` result for anything that isn't a single-statement
    write, or when simulation is disabled -- so callers can invoke it
    unconditionally and it self-gates.
    """
    if not config.enabled or not is_risky_write(classification):
        return _SKIPPED

    # Data-modifying CTE (P0): the outer command tag (e.g. "SELECT 1") does NOT
    # reflect the rows the nested write touches, so the exact count is not
    # measurable this way. Report it as unmeasurable -- the decision layer fails
    # closed on an unknown blast radius (apply_blast_radius).
    if classification.statements[0].nested_dml:
        return SimulationResult(
            method="unsupported",
            error="nested data-modifying CTE: blast radius not measurable",
        )

    est_rows, est_cost, est_err, est_timed_out = await _estimate(conn, sql, config)

    if not config.precise:
        return SimulationResult(
            method="estimate",
            estimated_rows=est_rows,
            estimated_cost=est_cost,
            timed_out=est_timed_out,
            error=est_err,
        )

    exact, timed_out, exact_err = await _exact(conn, sql, config)
    return SimulationResult(
        method="precise",
        estimated_rows=est_rows,
        estimated_cost=est_cost,
        exact_rows=exact,
        timed_out=timed_out or est_timed_out,
        error=exact_err or est_err,
    )
