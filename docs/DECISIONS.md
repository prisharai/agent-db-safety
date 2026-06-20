# DECISIONS.md — running decision log

> A chronological log of every non-trivial decision and its rationale. Append a
> new entry per decision; never rewrite history. Update this **every session**
> (CLAUDE.md §7, §9, §13). Each entry: date, the decision, why, and any
> latency/safety impact.

Format:

```
## YYYY-MM-DD — <short title>
**Decision:** what we chose.
**Why:** the rationale / alternatives considered.
**Latency/safety impact:** effect on the §4 budget or safety posture (or "none").
```

---

## 2026-06-19 — Project kickoff, Day 0 started
**Decision:** Begin Day 0 (Foundation). Created `docs/DESIGN.md` and
`docs/DECISIONS.md` stubs before writing any code. Building one vertical slice
at a time, in order, per CLAUDE.md §8.
**Why:** CLAUDE.md §7/§13 require reading the operating guide and establishing
the design + decision docs before code. Day 0's "Done when" depends on these
stubs existing alongside the repo skeleton, Docker Compose, and tooling.
**Latency/safety impact:** None yet (docs only). Latency-budget mindset recorded
in DESIGN.md §2 as required by Day 0 notes.

## 2026-06-19 — Seed dataset: Pagila + large generated tables
**Decision:** Use **Pagila** (Postgres port of the Sakila sample DB) for the
realistic relational schema, plus a couple of **large generated tables (a few
million rows each)** layered on top for blast-radius and benchmark realism.
**Why:** Pagila gives genuine relational structure — foreign keys, views,
multiple table types — which exercises the parser/classifier on realistic
shapes (joins, CTEs, `UPDATE ... FROM`, FK cascades). But Pagila's tables are
small, so simulation ("would hit 2.3M rows") and the §4 latency benchmarks
would be meaningless on it alone. The large generated tables supply real
volume. Together: real structure *and* real scale. Alternatives considered:
pure synthetic generator (no real relational realism) and pgbench's own schema
(too thin for classification variety).
**Latency/safety impact:** None on the request path (dev data only). The large
tables are what make the Day 7 latency proof and Day 4 simulation credible.

## 2026-06-19 — Env & dependency management: pyproject.toml + uv
**Decision:** Manage the Python env and dependencies with **`pyproject.toml` +
`uv`**. Pin `pglast`, `asyncpg`, `pytest`, `ruff`, and `black` there; commit the
`uv.lock` for reproducibility.
**Why:** `uv` gives a fast, reproducible lockfile and a single declarative
manifest. `pglast` (libpg_query binding) is the real Postgres parser required by
CLAUDE.md §6 — never regex/string matching. `asyncpg` is the fast async driver
for the no-blocking-I/O rule. `ruff`+`black` keep it clean from day one;
`pytest` runs tests in the same slice as code. Alternatives: plain
`venv`+`requirements.txt` (no lockfile) and poetry (slower, heavier).
**Latency/safety impact:** None directly. `asyncpg` chosen partly *for* the §4
budget (async, no blocking I/O on the engine path).

## 2026-06-20 — Day 0 seed crash diagnosed + fixed; smoke test closes Day 0
**Decision:** Root-caused the stalled Day 0 seed and finished the slice. The
init crashed mid-seed with `PANIC: could not fsync ... Input/output error`
during the 2M-row `metric_sample` insert — **not** a SQL bug, but the Docker
Desktop VM running out of disk while writing WAL (Pagila + the 3M-row
`app_event` had already loaded). Fix: reclaimed ~3 GB of Docker build cache
(`docker builder prune -af`), dropped the half-seeded volume
(`docker compose down -v`), and re-seeded clean. Added `tests/test_smoke.py`,
which connects via `asyncpg` and asserts real row counts for all four seeded
tables, and filled in `README.md` with quickstart + reset instructions.
**Why:** The init scripts only run on an empty data volume, so the partial seed
was a silent trap — a re-`up` would have skipped seeding and left the large
tables missing while looking healthy. The smoke test checks counts (not
`assert True`) precisely so this failure mode is caught automatically; it skips
gracefully when no DB is reachable so CI without Docker stays green. Chose the
conservative cleanup (build cache + this project's volume only) over a global
`docker system prune -af` to avoid touching unrelated images on the machine.
**Latency/safety impact:** None on the request path (dev tooling + data only).
Verified seed: film 1000, customer 599, app_event 3,000,000,
metric_sample 2,000,000. Day 0 "Done when" now fully met:
`docker compose up` → seeded Postgres, `pytest` green, README explains startup.

## 2026-06-20 — Day 1: pass-through MCP server + shadow-mode audit log
**Decision:** Built the Day 1 slice. Added the official **`mcp`** SDK and stood
up a `FastMCP` server (`adapters/mcp_server.py`) exposing one `run_query` tool
that forwards SQL to Postgres unchanged and returns the result — **shadow mode:
log everything, block nothing.** Four sub-decisions worth recording:

1. **Single-execution primitive: `conn.prepare(sql)` + `stmt.fetch()`.** Probed
   asyncpg and confirmed this runs the statement *once* and exposes BOTH the
   returned rows AND the command tag via `stmt.get_statusmsg()` (`"SELECT 3"`,
   `"UPDATE 5"`, `"CREATE TABLE"`). So we get affected-row counts for writes
   with **no string matching** (CLAUDE.md §6) and no double execution of a
   side-effecting statement. `execute()` gives the tag but discards rows;
   `fetch()` gives rows but discards the tag — prepare+fetch gives both.
2. **Audit log is sync-enqueue / async-write (`engine/audit.py`).** `record()`
   is a synchronous, non-blocking `Queue.put_nowait` on the query path; a single
   background consumer batches entries and writes JSONL via `asyncio.to_thread`
   so the disk write never blocks the event loop (and thus never adds latency to
   other in-flight queries). On a full queue we **drop + count** rather than
   block — logging is fail-open by design (§4: a query must never wait on a log).
3. **DB errors are captured and returned, not raised.** In shadow mode a failing
   statement yields a structured `{"error": ...}` and is still logged (the
   corpus wants failures too, §10). The agent gets the DB's own message verbatim.
4. **`ShadowSession` lives in the adapter for now, but is transport-agnostic.**
   It depends only on an asyncpg pool + `AuditLog`, so it's unit-testable without
   MCP. There is no policy engine yet; when Day 3 adds parse/classify/policy the
   *decision* moves into `engine/` and the adapter calls it — the transport layer
   never grows policy logic (§5).

Config (DSN, audit path, pool size) is env-overridable with docker-compose
defaults. Runtime `logs/` added to `.gitignore`. Tests in
`tests/test_pass_through.py` cover reads, writes/DDL affected-counts, error
capture, and that every statement (success and failure) reaches the audit log.
**Latency/safety impact:** Request-path cost is a pool acquire + one Postgres
round trip (the irreducible cost of running the query) plus one non-blocking
enqueue. No blocking I/O, no network, no LLM on the path. This is the
pass-through baseline the Day 7 benchmark measures against.

<!-- Append future decisions below this line. -->
