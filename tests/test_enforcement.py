"""Day 3 integration: policy enforcement through the adapter (needs Postgres).

Proves the engine's decision actually governs the database: a blocked statement
never reaches Postgres, an allowed read runs, and an unbounded read comes back
capped by an injected LIMIT. Skips cleanly when the dev DB isn't up.
"""

import json
import os

import asyncpg
import pytest

from adapters.mcp_server import ShadowSession
from engine.audit import AuditLog
from engine.policy import Policy
from engine.simulate import SimulationConfig
from engine.undo import UndoConfig, UndoStore

DB_DSN = os.environ.get(
    "AGENT_DB_DSN",
    "postgresql://postgres:postgres@localhost:5433/pagila",
)


@pytest.fixture
async def make_session(tmp_path):
    """Factory: build a ShadowSession with a given policy on the real pool."""
    pools = []
    audits = []

    async def _make(policy):
        try:
            pool = await asyncpg.create_pool(
                dsn=DB_DSN, min_size=1, max_size=4, timeout=5
            )
        except (OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"dev Postgres not reachable at {DB_DSN} ({exc})")
        log = tmp_path / f"audit{len(pools)}.jsonl"
        audit = AuditLog(log)
        await audit.start()
        pools.append(pool)
        audits.append(audit)
        store = UndoStore(policy.undo) if policy.undo.enabled else None
        return ShadowSession(pool, audit, policy, store), log

    yield _make

    for audit in audits:
        await audit.stop()
    for pool in pools:
        await pool.close()


async def test_blocked_statement_never_touches_the_database(make_session):
    # A disallowed table that also does not exist: if the statement were executed
    # we'd get a Postgres "relation does not exist" error. Blocked => error is
    # None, proving we never ran it.
    sess, _ = await make_session(Policy(allowed_tables=frozenset({"film"})))
    res = await sess.run_query("SELECT * FROM secret_accounts")
    assert res["blocked"] is True
    assert res["error"] is None  # the DB was never asked
    assert res["rows"] == []
    assert any(v["reason_code"] == "TABLE_NOT_ALLOWED" for v in res["violations"])


async def test_allowed_read_runs(make_session):
    sess, _ = await make_session(Policy(allowed_tables=frozenset({"film"})))
    res = await sess.run_query("SELECT film_id FROM film WHERE film_id = 1")
    assert res["blocked"] is False
    assert res["row_count"] == 1
    assert res["rows"][0]["film_id"] == 1


async def test_injected_limit_caps_rows_returned(make_session):
    # actor has 200 rows; max_rows_read=5 must cap the result at 5.
    sess, _ = await make_session(
        Policy(allowed_tables=frozenset({"actor"}), max_rows_read=5)
    )
    res = await sess.run_query("SELECT * FROM actor")
    assert res["blocked"] is False
    assert res["row_count"] == 5


async def test_observe_mode_logs_decision_but_still_runs(make_session):
    # film is NOT allowed -> the decision is "block", but observe mode runs anyway.
    sess, log = await make_session(
        Policy(mode="observe", allowed_tables=frozenset({"actor"}))
    )
    res = await sess.run_query("SELECT film_id FROM film WHERE film_id = 1")
    assert res["blocked"] is False  # observe never blocks the response
    assert res["row_count"] == 1  # it actually ran
    # ...but the recorded decision shows it WOULD have been blocked.
    await sess._audit.stop()  # flush
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["decision"]["allowed"] is False
    assert entry["decision"]["violations"][0]["reason_code"] == "TABLE_NOT_ALLOWED"


async def test_observe_mode_does_not_rewrite_live_reads(make_session):
    # P1c regression: observe must run the ORIGINAL sql, not an injected LIMIT --
    # actor has 200 rows; observe with max_rows_read=5 must still return all 200.
    sess, _ = await make_session(
        Policy(mode="observe", allowed_tables=frozenset({"actor"}), max_rows_read=5)
    )
    res = await sess.run_query("SELECT * FROM actor")
    assert res["blocked"] is False
    assert res["row_count"] == 200  # NOT capped at 5


# --- Day 4: blast-radius simulation through the adapter -----------------------


@pytest.fixture
async def scratch():
    """A throwaway 50-row table for write tests; dropped on teardown."""
    try:
        c = await asyncpg.connect(dsn=DB_DSN, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"dev Postgres not reachable at {DB_DSN} ({exc})")
    await c.execute("DROP TABLE IF EXISTS _sim_scratch")
    await c.execute("CREATE TABLE _sim_scratch (id int primary key)")
    await c.execute("INSERT INTO _sim_scratch SELECT generate_series(1, 50)")
    try:
        yield c
    finally:
        await c.execute("DROP TABLE IF EXISTS _sim_scratch")
        await c.close()


async def test_risky_write_held_until_operator_approval(make_session, scratch):
    sess, _ = await make_session(
        Policy(
            allowed_tables=None,
            simulation=SimulationConfig(
                enabled=True, precise=True, confirm_over_rows=10, block_over_rows=100000
            ),
        )
    )
    sql = "DELETE FROM _sim_scratch WHERE id <= 29"  # 29 rows > confirm limit 10

    # Agent attempt: held for confirmation, blast radius measured, NOT executed.
    res = await sess.run_query(sql)
    assert res["requires_confirmation"] is True
    assert res["blocked"] is False
    assert res["simulation"]["exact_rows"] == 29
    assert await scratch.fetchval("SELECT count(*) FROM _sim_scratch") == 50

    # Out-of-band operator approval (the agent can't reach this) runs it.
    res2 = await sess.run_query(sql, operator_approved=True)
    assert res2["requires_confirmation"] is False
    assert res2["status"] == "DELETE 29"
    assert await scratch.fetchval("SELECT count(*) FROM _sim_scratch") == 21


async def test_risky_write_over_block_limit_is_blocked(make_session, scratch):
    sess, _ = await make_session(
        Policy(
            allowed_tables=None,
            simulation=SimulationConfig(enabled=True, precise=True, block_over_rows=10),
        )
    )
    # 39 rows > block limit 10 -> blocked outright, even with operator approval.
    res = await sess.run_query(
        "DELETE FROM _sim_scratch WHERE id <= 39", operator_approved=True
    )
    assert res["blocked"] is True
    assert any(v["reason_code"] == "BLAST_RADIUS_EXCEEDED" for v in res["violations"])
    assert res["simulation"]["exact_rows"] == 39
    assert (
        await scratch.fetchval("SELECT count(*) FROM _sim_scratch") == 50
    )  # untouched


def test_agent_tool_has_no_confirmation_bypass():
    # QA P1e: the MCP tool (the agent's only interface) must not expose any way
    # to approve a held write -- that would be the agent confirming itself.
    import inspect

    from adapters.mcp_server import run_query as tool

    params = set(inspect.signature(tool).parameters)
    assert "confirm" not in params
    assert "operator_approved" not in params  # operator seam is server-side only


async def test_reads_are_not_simulated_through_adapter(make_session):
    sess, _ = await make_session(
        Policy(
            allowed_tables=frozenset({"film"}),
            simulation=SimulationConfig(enabled=True),
        )
    )
    res = await sess.run_query("SELECT film_id FROM film WHERE film_id = 1")
    assert res["simulation"] is None  # reads are never simulated
    assert res["row_count"] == 1


# --- Day 5: reversible writes through the adapter -----------------------------


async def test_write_is_reversible_through_adapter(make_session, scratch):
    sess, _ = await make_session(
        Policy(allowed_tables=None, undo=UndoConfig(enabled=True))
    )
    # _sim_scratch has 50 rows (id 1..50); add a non-PK column to mutate.
    await scratch.execute("ALTER TABLE _sim_scratch ADD COLUMN label text")
    res = await sess.run_query(
        "UPDATE _sim_scratch SET label = 'tagged' WHERE id <= 3",
        stated_task="tag rows",
        agent="agent-7",
    )
    assert res["reversible"] is True
    assert res["undo_action_id"]
    tagged = "SELECT count(*) FROM _sim_scratch WHERE label = 'tagged'"
    assert await scratch.fetchval(tagged) == 3

    rev = await sess.revert_write(res["undo_action_id"], agent="agent-7")
    assert rev["ok"] is True
    assert rev["operation"] == "update"
    assert await scratch.fetchval(tagged) == 0  # labels restored to NULL


async def test_reads_carry_no_undo_action(make_session):
    sess, _ = await make_session(
        Policy(allowed_tables=frozenset({"film"}), undo=UndoConfig(enabled=True))
    )
    res = await sess.run_query("SELECT film_id FROM film WHERE film_id = 1")
    assert res["undo_action_id"] is None
    assert res["reversible"] is None  # reads never go through the undo path


async def test_non_reversible_reason_is_surfaced(make_session, scratch):
    # QA P2: when a write can't be reverted, the agent gets a machine-readable
    # undo_reason, not just reversible=False.
    sess, _ = await make_session(
        Policy(allowed_tables=None, undo=UndoConfig(enabled=True))
    )
    res = await sess.run_query(
        "UPDATE _sim_scratch t SET id = id FROM (SELECT 1 AS x) s WHERE t.id = 1"
    )
    assert res["reversible"] is False
    assert res["undo_reason"] is not None
    assert "FROM/USING" in res["undo_reason"]
