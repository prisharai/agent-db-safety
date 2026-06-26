# Interdict

**A safety layer between whoever is writing SQL — an AI agent, or you — and your
Postgres database.** Developer preview.

## What it does

Give an agent (or a tired human) direct database access and a single bad
statement can wipe a table. `DELETE FROM clients` with no `WHERE`. An `UPDATE`
that was meant for one row but hits a million. A stray semicolon that turns one
scoped delete into a full-table one. Database permissions don't help here —
they answer *"is this role allowed to touch this table,"* not *"how much will
this particular statement change, and can I take it back?"*

Interdict answers those two questions, on every statement, before damage is done:

- **It measures the real impact before running.** For a risky write it actually
  simulates the statement in a throwaway transaction and reports the count —
  *"this DELETE would affect 2,300,000 rows"* — then asks for confirmation
  instead of just running it. We call that number the statement's **blast
  radius**.
- **It makes writes undoable.** Every write it allows is recorded so you can
  reverse it with one command, with a full audit trail of who did what. Writes
  it can't safely record are blocked rather than run-and-hope.
- **It explains every block.** A blocked statement comes back with a reason code
  and a suggested fix — readable by a human, and machine-readable so an agent
  can correct itself and retry.

The checks that decide *block / allow / confirm* are deterministic and fast
(microseconds), so normal traffic isn't slowed down. Anything fuzzy (an optional
LLM "does this match the stated task?" check) is advisory only and never sits in
the path of a query you're waiting on.

```text
> UPDATE accounts SET balance = 0                ⛔ blocked: no WHERE — would hit every row
                                                   fix: add a WHERE that scopes it
> UPDATE accounts SET balance = 0 WHERE id = 1   ✓ UPDATE 1   (undo id 3811adb4)
> DELETE FROM accounts WHERE balance < 2000      ⚠ confirm: would delete 19 rows  [y/n]
> \undo                                          ✓ reverted — 1 row restored
```

---

## User guide

### 1. Install & launch

One install, then one command opens the launcher:

```bash
# A) pip
pip install agent-db-safety        # from source today: pip install .
agentdb

# B) Docker (brings up Postgres + the launcher together)
docker compose --profile app run --rm app

# C) From source (dev)
uv sync && uv run python -m adapters.tui
```

The launcher asks **who is writing the SQL**:

```text
  1  🤖  An agent writes SQL   (Claude Code, Codex — via MCP)
  2  ⌨   I write SQL           (Human Mode)
```

### 2. Human Mode — you write SQL

You type SQL at a prompt; every statement goes through the safety layer first.
Safe reads and scoped writes just run. A risky write is simulated and shown
before anything happens:

```text
agentdb ▸ DELETE FROM clients WHERE active = true
╭─ ⚠ CONFIRM WRITE ─────────────────────────────╮
│ DELETE FROM clients WHERE active = true        │
│ Blast radius: 2,300,000 rows (precise)         │
│ Reversible: yes — an undo id will be kept      │
╰────────────────────────────────────────────────╯
  Execute? [y/n] (n):
```

Press `y` to run it, `n` to cancel. Nothing touched the database until you said
yes. After a write runs you get an undo id; `\undo` reverses it.

Because *you* wrote the SQL, a block is advice, not a wall — `\override` runs a
blocked statement anyway (after a confirmation, fully audited, and still undoable
when the statement's shape allows).

**Commands:**

| Command | What it does |
|---|---|
| *(any SQL)* | run it through the safety layer |
| `\undo` | reverse the most recent write |
| `\revert <id>` | reverse a specific write by its undo id |
| `\override` | run the last **blocked** statement anyway (your call) |
| `\stats` | what the layer has caught for you (see below) |
| `\history` | this session's executed writes |
| `\tables` | tables the policy allows |
| `\help` · `\quit` | help · leave |

**See your savings** any time with `\stats` (or `agentdb stats` from the shell):
statements guarded, blocked, held for confirmation, overrides, reverts, and the
largest blast radius it held back.

> **Why this beats keeping a `.log`/dump backup.** A backup is the whole
> database, slow to restore, and anonymous. Here every write is recorded
> per-action and attributed, so you undo *one* mistaken statement instantly by
> its id — instead of restoring the entire database and losing everyone else's
> work since the last dump.

### 3. Agent Mode — your AI agent writes SQL

The agent talks to Interdict's MCP server and calls `run_query` instead of
touching the database directly. Same engine, same guarantees as Human Mode. Two
ways to spell the launch command in the configs below:

- **pip-installed:** `agentdb-mcp` (works from any directory)
- **from source:** `uv run --directory /ABSOLUTE/PATH/TO/agent-db-safety agentdb-mcp`

**Claude Code:**

```bash
claude mcp add interdict \
  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \
  --env AGENT_OPERATOR_TOKEN=choose-a-secret \
  -- agentdb-mcp
```

**Codex** (CLI, or edit `~/.codex/config.toml`):

```bash
codex mcp add interdict \
  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \
  --env AGENT_OPERATOR_TOKEN=choose-a-secret \
  -- agentdb-mcp
```

```toml
# ~/.codex/config.toml   (table is mcp_servers, with an underscore)
[mcp_servers.interdict]
command = "agentdb-mcp"        # from source: command = "uv", args = ["run",
                               #   "--directory","/ABS/PATH","agentdb-mcp"]
[mcp_servers.interdict.env]
AGENT_DB_DSN = "postgresql://postgres:postgres@localhost:5433/pagila"
AGENT_OPERATOR_TOKEN = "choose-a-secret"
```

> **Codex PATH gotcha:** Codex may not inherit your shell's PATH. If it can't
> find `agentdb-mcp`, use the absolute path from `which agentdb-mcp` as
> `command`. Verify with `codex mcp list`, then `/mcp` in the Codex TUI.

A held (confirmation-gated) write is approved out-of-band with `approve_query`
and the operator token, which the agent never sees.

### 4. Set up the dev database

The launcher needs a Postgres to talk to. The repo ships a seeded one:

```bash
docker compose up -d        # seeded Postgres on localhost:5433 (Pagila + large tables)
```

First start generates ~5M rows (1–2 min); later starts are instant. Point at any
other database with `AGENT_DB_DSN`. Re-seed from scratch with
`docker compose down -v && docker compose up -d`.

---

## How it works

```
writer ──(MCP / Human Mode TUI)──> [thin adapter] ──> [SAFETY ENGINE] ──> Postgres
                                                            │
                                       parse → classify → policy → (simulate?) → decide
                                                            │           (record undo on writes)
                                                        async: audit log, advisory intent check
```

- **The engine** (`engine/`) is a standalone, transport-agnostic core. It parses
  each statement to a real Postgres AST with `pglast` (never string matching, so
  comments, casing, whitespace, alias stars, and wrapped writes like
  `EXPLAIN ANALYZE DELETE …` can't smuggle anything past), classifies it, checks
  it against a declarative YAML policy, and — only for a risky write — simulates
  the blast radius with a time-boxed `BEGIN; … ; ROLLBACK`.
- **Adapters are thin renderers** over that engine. The MCP server (Agent Mode)
  and the `rich` terminal UI (Human Mode) share the exact same gate; a web UI
  later would too. Policy logic never lives in an adapter.
- **The hot path stays cheap.** Only blocking-vs-allowing is on it (in-memory,
  microseconds). Simulation is opt-in, gated to risky writes, and time-boxed
  (`statement_timeout` + `lock_timeout`). Audit logging and the optional LLM
  intent check are async/out-of-band. Writes fail closed on uncertainty; reads
  fail open so the layer can never take down read availability.

It is **not** "git for databases," a migration tool, a semantic layer, or a
replacement for Postgres roles/RLS — those still do their job; this does the part
they structurally can't.

## Measured results

The hard constraint is **don't slow down the database** — a safety layer that
adds latency gets ripped out. Budget: added p99 < 5 ms on the pass-through path,
enforced by a local benchmark gate.

| What | Result |
|---|---|
| Hot-path cost, warm (parse-cache hit) | **2.6 µs** p50 / 2.7 µs p99 |
| Hot-path cost, cold (first sight of a query) | **166 µs** p50 / 189 µs p99 |
| End-to-end overhead vs direct asyncpg | **≈ 0 ms** p50 & p99 — gate PASS |
| Red corpus blocked (false negatives) | 40 statements, **0%** |
| Green corpus allowed (false positives) | 18 statements, **0%** |
| Blast-radius accuracy (precise path) | **exact** affected-row count |
| Undo round-trip | ~4 ms, conflict-checked, exact restore |
| Automated tests | **308** |

Full methodology and per-rate tables: [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md)
and [`benchmarks/METRICS.md`](benchmarks/METRICS.md).

## Configuration reference

Environment variables (used by both modes):

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_DB_DSN` | `postgresql://postgres:postgres@localhost:5433/pagila` | Target Postgres. Default is local-dev only. |
| `AGENT_POLICY` | `policies/default.yaml` | YAML policy loaded at startup. |
| `AGENT_AUDIT_LOG` | `logs/audit.jsonl` | Async JSONL audit log (also feeds `\stats`). |
| `AGENT_OPERATOR_TOKEN` | unset | Required to approve held writes via MCP. |
| `AGENT_POOL_MIN` / `AGENT_POOL_MAX` | `1` / `10` | asyncpg pool sizing. |

MCP tools the server exposes:

| Tool | Purpose |
|---|---|
| `run_query(sql, stated_task?)` | Classify, policy-check, simulate if risky, then execute or block. |
| `list_pending_approvals()` | Writes currently held for operator approval. |
| `approve_query(approval_id, operator_token)` | Execute a held write when the token matches. |
| `revert_write(action_id, operator_token?)` | Revert a recorded write. |
| `audit_status()` | Audit queue depth, dropped-record count, log path. |

## Honest limits

Kept visible on purpose:

- Semantic correctness is undecidable in general. We catch **blast-radius** and
  **scope-contradiction** cases and make the rest **reversible**; we don't claim
  to catch every "valid SQL but wrong" statement.
- `BEGIN/ROLLBACK` simulation can't undo external side effects (triggers calling
  out, already-consumed sequences) and takes locks — hence the gating and
  time-boxing.
- Reversibility isn't infinite (external calls, cascades, consumed sequences).
  Shapes that can't be recorded for safe undo are blocked by default;
  local evaluation can opt out with `undo.block_non_reversible: false`.
- Audit logging is non-blocking: under overload it drops records rather than
  stalling queries (`audit_status` reports this), and the local JSONL log isn't
  tamper-proof.
- LLM intent checks are advisory only — never the last line of defense, never on
  the hot path.
- This is a local developer preview, not a production recipe. Use a
  least-privilege Postgres role and review your policy before pointing it at real
  data.

## Repo layout

```
engine/      # safety core: parse, classify, policy, simulate, undo, audit, intent, session
adapters/    # mcp_server.py (Agent Mode), tui.py (Human Mode)
policies/    # declarative YAML policy files
corpus/      # red (should-block) + green (should-allow) query sets
benchmarks/  # latency harness, RESULTS.md, METRICS.md, CI latency gate
examples/    # runnable end-to-end demo
db/          # Docker Postgres seed scripts (Pagila + large tables)
tests/       # pytest suite (308 tests)
```
