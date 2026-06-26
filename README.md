# Interdict

**Developer preview.** A runtime safety layer that sits between AI agents and a
database (Postgres first), making risky agent-issued writes safe *before* they
commit and reversible *after*.

Every statement an agent sends is parsed to a real Postgres AST, classified, and
checked against a deterministic policy. Genuinely risky writes have their **blast
radius simulated** ("this would modify 2.3M rows") before any decision is made;
reversible writes are recorded so they can be undone; writes that cannot be
recorded for safe undo are blocked by default. A blocked statement comes back
with a **structured, machine-readable explanation** so the agent can self-correct
and retry. The load-bearing safety is deterministic and fast -- anything
probabilistic (LLM-based intent checks) is strictly advisory and never on the hot
path.

---

## Why this exists

Database permissions answer "is this role allowed to touch this table." They
can't answer the questions that actually matter when an autonomous agent is
driving: *how much would this statement change?* *can I take it back if it was
wrong?* *does this query match what the agent said it was doing?* This layer
governs **live runtime traffic** with dynamic, query-shape-aware, blast-radius-
aware, reversible, per-agent control — the things permissions, ORMs, and
migration tools structurally cannot express.

It is **not** "git for databases," a migration tool, a semantic layer, or a
replacement for Postgres roles/RLS. Those still do their job; this does the part
they can't.

## The three things that make it different

Most tools in this space do policy-based blocking. The differentiation here is
the two capabilities that blocking alone can't provide, plus one advisory check:

1. **Blast-radius simulation** — quantify a risky write's *real* impact before
   deciding (exact affected rows, via a time-boxed `BEGIN; … ; ROLLBACK`), not
   just pattern-match the statement text.
2. **Reversibility / instant undo** — reversible writes record before/after
   images, so they can be reverted with one conditional, atomic call. Writes that
   cannot be captured safely are blocked by default, rather than executing and
   surprising the operator after the fact.
3. **Intent-mismatch detection (advisory only)** — flag when a query's blast
   radius contradicts the agent's stated task. A flag, never a sole gate, and
   never on the hot path.

## See it work

```text
1. A destructive write is blocked (with a fix the agent can use)
   agent> UPDATE accounts SET balance = 0
   BLOCKED [WRITE_WITHOUT_WHERE]: UPDATE/DELETE has no WHERE clause and would
      affect every row in the table.
      fix: Add a WHERE clause that scopes the statement to the intended rows.

2. The agent self-corrects using the suggested fix
   agent> UPDATE accounts SET balance = 0 WHERE id = 1
   OK: UPDATE 1 rows | reversible, undo id = 3811adb4…

3. A risky bulk write: blast radius measured, held for confirmation
   agent> DELETE FROM accounts WHERE balance < 2000
   HELD FOR CONFIRMATION — blast radius: 19 rows
      (a human operator must approve before this commits)
   table still intact: 50 rows (nothing was deleted)

4. An allowed write is undone with a single revert
   agent> UPDATE accounts SET balance = 999999 WHERE id = 2
   OK: UPDATE 1 rows | reversible, undo id = f47d5868…
   revert(f47d5868…) -> restored 1 row; balance back to 200
```

Run it yourself (needs the dev Postgres up): `uv run python -m examples.demo`
(source: [`examples/demo.py`](examples/demo.py)).

## Measured results

The non-negotiable engineering constraint is **do not slow down the database**: a
safety layer that adds latency to normal traffic gets ripped out. The budget is
**added p99 < 5 ms** on the pass-through path, checked by a local benchmark gate.

| What | Result |
|---|---|
| Hot-path cost, warm (parse-cache hit) | **2.6 µs** p50 / 2.7 µs p99 |
| Hot-path cost, cold (normal first sight of a query) | **166 µs** p50 / 189 µs p99 on the measured benchmark shape |
| Pathological SQL guard | Oversized or high-complexity inputs fail closed before parsing |
| End-to-end pass-through overhead (vs direct asyncpg) | **≈ 0 ms** p50 & p99 (at the noise floor) -- local gate PASS |
| Red corpus blocked (false-negative rate) | 38 statements, **0%** |
| Green corpus allowed (false-positive rate) | 18 statements, **0%** |
| Blast-radius accuracy (precise path) | **exact** — 664 measured vs a planner estimate of 0 |
| Undo round-trip | ~4 ms, conflict-checked, exact restore |
| Automated tests | **294** passing |

Full latency methodology, per-rate tables, and validity checks are in
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md); all numbers with how they were
obtained are in [`benchmarks/METRICS.md`](benchmarks/METRICS.md). The latency
benchmark uses an open-loop generator with coordinated-omission-safe timing,
HdrHistogram, a realistic Zipfian workload mix, and **paired** A/B/C measurement
so machine noise cancels in the per-layer delta.

## How it's built

```
agent ──(MCP today / wire-protocol later)──> [thin adapter] ──> [SAFETY ENGINE core] ──> Postgres
                                                                      │
                                                  parse → classify → policy → (simulate?) → decide
                                                                      │            (undo record on writes)
                                                                  async: audit log, advisory intent check
```

- The **engine** (`engine/`) is a standalone, transport-agnostic core: parse
  (`pglast`, cached) → classify → deterministic YAML policy → gated simulation →
  undo recording, with async non-blocking audit logging. Classification is always
  on the **AST**, never string matching, so comments, casing, whitespace, alias
  stars, whole-row refs, and wrapped writes (`EXPLAIN ANALYZE DELETE …`) can't
  smuggle anything past.
- **Adapters** are thin. Phase A is an **MCP server** so agents (Claude Code,
  Cursor, …) talk to it as their DB tool — which keeps it off the hot path of any
  human traffic by construction. A transparent Postgres wire-protocol proxy is a
  later stretch goal; the engine is designed so a Go port is feasible.
- **Hot-path discipline:** the request path does only cheap in-memory work.
  Simulation is opt-in, gated to risky writes, and time-boxed
  (`statement_timeout` + `lock_timeout`). Audit logging and intent checks are
  async/out-of-band. Writes fail closed on uncertainty; reads fail open.
- **Approval and undo controls:** confirmation-gated writes are held with an
  approval id and require `AGENT_OPERATOR_TOKEN` for execution. Reverts are bound
  to the originating agent unless the same operator token is supplied.

## Honest limits

Kept visible on purpose — this honesty is a feature:

- Semantic correctness is undecidable in general. We catch **blast-radius** and
  **scope-contradiction** cases and make the rest **reversible**; we don't claim
  to catch every "valid SQL but wrong" statement.
- `BEGIN/ROLLBACK` simulation can't roll back external side effects (triggers
  calling out, already-consumed sequences) and takes locks — hence the gating and
  time-boxing.
- Reversibility isn't infinite (external calls, cascades, consumed sequences).
  Shapes that cannot be recorded for safe undo are blocked by default; local
  evaluation can opt out with `undo.block_non_reversible: false`.
- Audit logging is non-blocking. If the audit writer cannot keep up, records are
  dropped rather than stalling queries; use the `audit_status` MCP tool to monitor
  queue depth and dropped-record count. The local JSONL log is not tamper-proof.
- LLM intent checks are non-deterministic — advisory only, never the last line of
  defense, never on the hot path.
- This is a local developer preview, not a production deployment recipe. Use a
  least-privilege Postgres role and review policies before pointing it at real
  data.

## Install & launch

One install, then one command opens the launcher. Pick the path that fits your
workflow:

```bash
# A) pip — installs the `agentdb` launcher
pip install agent-db-safety        # (from source today: pip install .)
agentdb                            # opens the landing screen

# B) Docker — brings up Postgres + the launcher together
docker compose --profile app run --rm app

# C) From source (dev)
uv sync && uv run python -m adapters.tui
```

The landing screen asks **who is writing the SQL**:

- **🤖 Agent Mode** — your AI agent (Claude Code, Cursor, …) writes SQL through
  our MCP server; the safety layer guards it. See *Connect it to your agent*.
- **⌨ Human Mode** — *you* write SQL by hand. Every statement is parsed,
  policy-checked, and — for a risky write — **simulated so you see the blast
  radius before anything runs**. Destructive writes ask before executing; every
  write prints an undo id (`\undo` to reverse it). Because you're the author, a
  block is advice, not a wall: `\override` runs it anyway (audited, and still
  undoable when the statement's shape allows).

```text
agentdb ▸ DELETE FROM clients WHERE active = true
╭─ ⚠ CONFIRM WRITE ─────────────────────────────╮
│ DELETE FROM clients WHERE active = true        │
│ Blast radius: 2,300,000 rows (precise)         │
│ Reversible: yes — an undo id will be kept      │
╰────────────────────────────────────────────────╯
  Execute? [y/n] (n):
```

See what the layer has caught for you any time with `\stats` (or `agentdb
stats`): statements guarded, blocked, held for confirmation, overrides, and the
largest blast radius it held back.

> **Why this beats a `.log`/dump backup.** A backup is coarse (whole database),
> slow to restore, and anonymous. Here, *every* write is recorded per-action and
> attributed — so you undo a single mistaken statement instantly with its id,
> with a full audit trail, instead of restoring the entire database and losing
> everyone else's work since the dump.

> npm packaging is planned; today the native paths are pip and Docker.

## Quickstart

Prerequisites: [Docker](https://docs.docker.com/get-docker/) (Compose v2) and
[`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Start a seeded local Postgres (Pagila + two large generated tables for
#    blast-radius / benchmark realism). Listens on host port 5433.
docker compose up -d

# 2. Install the Python env from the lockfile.
uv sync --frozen

# 3. Run the test suite (skips DB-backed tests cleanly if Postgres isn't up).
uv run pytest

# 4. See the safety layer in action end-to-end.
uv run python -m examples.demo

# 5. Start the MCP server for an agent/client.
AGENT_OPERATOR_TOKEN=dev-operator-token uv run python -m adapters.mcp_server

# 6. Run the local latency gate.
uv run python -m benchmarks.ci_latency_gate
```

First `docker compose up` generates ~5M rows and takes 1–2 minutes; later starts
are instant (data lives in a Docker volume). Connect with any client over
`postgresql://postgres:postgres@localhost:5433/pagila`, or
`docker exec -it agent-db-safety-pg psql -U postgres -d pagila`. To re-seed from
scratch: `docker compose down -v && docker compose up -d`. The test suite reads
`AGENT_DB_DSN` if set.

Useful environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_DB_DSN` | `postgresql://postgres:postgres@localhost:5433/pagila` | Target Postgres database. The default is local-dev only. |
| `AGENT_POLICY` | `policies/default.yaml` | YAML policy file loaded at startup. |
| `AGENT_AUDIT_LOG` | `logs/audit.jsonl` | Async JSONL audit/shadow log path. |
| `AGENT_OPERATOR_TOKEN` | unset | Required by `approve_query`; without it, held writes cannot be approved through MCP. |
| `AGENT_POOL_MIN` / `AGENT_POOL_MAX` | `1` / `10` | asyncpg pool sizing. |

MCP tools exposed by the server:

| Tool | Purpose |
|---|---|
| `run_query(sql, stated_task?)` | Classify, policy-check, optionally simulate, then execute or block a statement. |
| `list_pending_approvals()` | Show writes currently held for operator approval. |
| `approve_query(approval_id, operator_token)` | Execute a held write when the operator token matches `AGENT_OPERATOR_TOKEN`. |
| `revert_write(action_id, operator_token?)` | Revert a recorded write. The originating agent may revert its own action; the operator token overrides that check. |
| `audit_status()` | Show audit queue depth, dropped-record count, and log path. |

## Connect it to your agent (MCP)

The server speaks MCP over stdio, so the agent launches it as a subprocess. It's
the same engine and guarantees as Human Mode — the agent just calls `run_query`
instead of touching the database directly. The operator token stays in the
client config, never visible to the model.

Two ways to spell the launch command in any config below:

- **pip-installed:** `agentdb-mcp` (a console script; works from any directory)
- **from source:** `uv run --directory /ABSOLUTE/PATH/TO/agent-db-safety agentdb-mcp`

### Claude Code

```bash
# pip-installed
claude mcp add interdict \
  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \
  --env AGENT_OPERATOR_TOKEN=choose-a-secret \
  -- agentdb-mcp

# from source (no install): swap the command after `--`
claude mcp add interdict \
  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \
  -- uv run --directory /ABSOLUTE/PATH/TO/agent-db-safety agentdb-mcp
```

### Codex

CLI (`codex mcp add <name> --env … -- <command>`):

```bash
codex mcp add interdict \
  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \
  --env AGENT_OPERATOR_TOKEN=choose-a-secret \
  -- agentdb-mcp
```

…or edit `~/.codex/config.toml` directly (note: the table is `mcp_servers`,
with an underscore):

```toml
[mcp_servers.interdict]
command = "agentdb-mcp"        # from source: command = "uv",
                               #   args = ["run","--directory",
                               #           "/ABSOLUTE/PATH/TO/agent-db-safety",
                               #           "agentdb-mcp"]
[mcp_servers.interdict.env]
AGENT_DB_DSN = "postgresql://postgres:postgres@localhost:5433/pagila"
AGENT_OPERATOR_TOKEN = "choose-a-secret"
```

> **PATH gotcha (Codex):** Codex launches the server with the PATH it inherits,
> which may not include your shell's. If it can't find `agentdb-mcp`, use the
> absolute path from `which agentdb-mcp` as `command`. Verify with `codex mcp
> list`, then `/mcp` inside the Codex TUI.

The dev Postgres (`docker compose up -d`) must be running first. Once registered,
every statement the agent issues is parsed, policy-checked, simulated when risky,
and recorded for undo. Held writes are approved out-of-band via `approve_query`
with the operator token (which the agent does not have).

## Repo layout

```
engine/      # transport-agnostic safety core: parse, classify, policy, simulate, undo, audit, intent
adapters/    # MCP server (Phase A); wire-protocol proxy (Phase B, later)
policies/    # declarative YAML policy files
corpus/      # red (should-block) + green (should-allow) query sets
benchmarks/  # open-loop latency harness, RESULTS.md, METRICS.md, CI latency gate
examples/    # runnable end-to-end demo
db/          # Docker Postgres seed scripts (Pagila + large tables)
tests/       # pytest suite (294 tests: policy, simulation, undo, evasion, edge, compat)
```
