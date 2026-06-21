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

## 2026-06-20 — Day 2: parse + classify (observe-only)
**Decision:** Built `engine/parser.py` and `engine/classifier.py` and wired the
classification into the shadow-mode audit log. Everything is derived from the
`pglast` AST — never string matching (§6) — and it's observe-only: we describe
each statement, we don't decide on it yet.

* **Parser:** `parse(sql)` returns a `ParseResult(statements, error)`, LRU-cached
  (2048) on the raw SQL. It never raises onto the hot path — a syntax error
  becomes `error=...` with empty statements, so the request path is exception-
  free. Resolves the DESIGN.md "parse-cache strategy/eviction" open question:
  plain bounded LRU keyed on SQL text.
* **Classifier:** per-statement `StatementInfo` (kind, stmt_type, tables,
  columns, has_where, unbounded_write, nested_dml, touches_system_catalog) plus a
  `Classification` aggregate (statement_count, is_multi_statement, most-severe
  kind, parse_error). Kind severity order OTHER < READ < WRITE < DDL < UNKNOWN;
  a multi-statement batch takes its worst statement's kind.
* **Tricky cases handled** (the §8 Day 2 list + anti-evasion extras): CTE names
  excluded from real tables; `UPDATE...FROM` and sub-selects collect all tables;
  comments stripped by the real parser; multi-statement detected; system-catalog
  refs flagged (qualified `pg_catalog`/`information_schema` and unqualified
  `pg_*`). Extras: **`nested_dml`** catches a write hidden under a non-write top
  node (data-modifying CTE `WITH d AS (DELETE...) SELECT` — top node is a
  `SelectStmt` but it's a WRITE); **`unbounded_write`** flags any UPDATE/DELETE
  (top-level or nested) with no WHERE — exactly what Day 3 will block.
* **Parse failures fail safe:** `kind=UNKNOWN` + the parser message, no
  exception. UNKNOWN is the most-severe kind so writes fail closed downstream
  (§4). Enforcement is Day 3; Day 2 only records it.

**Latency/safety impact:** Adds parse + classify to the hot path. Measured on a
realistic CTE/UPDATE...FROM/sub-select statement: **0.168 ms cold (parse + walk),
~0.0001 ms warm (cache hit)** — ~30× under the §4 5 ms p99 budget cold, free when
cached. Both `parse` and `classify` are LRU-cached on the SQL text. No I/O, no
network, no blocking. The classification is added to the async audit entry only;
it changes no behavior and the pass-through result is unchanged.

## 2026-06-20 — Day 2 QA fixes (P1/P1/P2 from QA_REPORT.md)
**Decision:** Fixed three issues from QA review of the Day 2 classifier/parser,
each with a permanent regression test (§8 Day 8).

* **P1a — side-effecting statements no longer fall through to `OTHER`.** The
  classifier had a permissive `OTHER` fallback, so `DO`, `CALL`, `COPY` (incl.
  `COPY ... PROGRAM` shell-out), `LOCK`, `VACUUM`, `REINDEX`, `REFRESH
  MATERIALIZED VIEW`, `CREATE DATABASE`, `ALTER SYSTEM`, `EXECUTE`, etc. looked
  as harmless as `BEGIN`. Replaced it with an explicit safe-utility allowlist
  (`TransactionStmt`, `VariableSetStmt`, `VariableShowStmt`) → `OTHER`; every
  other/unrecognized top-level node **fails closed to `DDL` severity**.
  `UNKNOWN` stays reserved for parse failures. (`EXPLAIN` is intentionally not
  on the allowlist — `EXPLAIN ANALYZE` actually executes; Day 3 can refine.)
* **P1b — CTE filtering was scope-blind and could hide real tables.**
  `_real_tables` removed any unqualified RangeVar whose name appeared in a single
  global `cte_names` set, so a CTE in an inner subquery could suppress an outer
  real table (`SELECT * FROM secret WHERE EXISTS (WITH secret AS (...) ...)`
  returned no tables). Replaced with **scope-aware resolution**: each RangeVar is
  suppressed only if an *ancestor* statement's `WITH` clause defines its name
  (walked via the visitor's ancestor chain), and DML/MERGE **target relations are
  protected by node identity** so a same-named CTE can never hide a write target.
* **P2 — `parse()` could still raise on the hot path.** It caught only
  `ParseError`, but `pglast.parse_sql` raises e.g. `UnicodeEncodeError` on lone
  surrogates. Added a defensive `except Exception` that returns
  `ParseResult(error=...)` so malformed input becomes `UNKNOWN` instead of an
  exception — upholding the §4 non-raising-hot-path guarantee.

**Latency/safety impact:** Pure-classification changes; no I/O added. P1b's
per-RangeVar ancestor walk is negligible — cold classify still **0.167 ms (~30×
under the 5 ms p99 budget)**, warm unchanged. All three fixes make the classifier
*more* fail-closed (safer defaults), consistent with §4. 53 tests green.

## 2026-06-20 — Day 3: deterministic policy engine v1
**Decision:** Built `engine/policy.py` (pure, hot-path `evaluate()`), the YAML
`policies/default.yaml`, a red/green corpus under `corpus/`, and wired
enforcement into the adapter. New dependency **PyYAML** — sanctioned by §6 ("Config:
declarative YAML policy files"); used only at startup for policy/corpus loading,
never on the hot path.

* **Rejection schema (resolves the DESIGN.md open question):** a `Decision` has
  `allowed`, an `effective_sql` (rewritten or original), `rewritten`, and a tuple
  of `Violation(reason_code, message, suggested_fix, statement_index)`. Reason
  codes are stable strings an agent can branch on (`WRITE_WITHOUT_WHERE`,
  `DDL_NOT_ALLOWED`, `TABLE_NOT_ALLOWED`, `SYSTEM_CATALOG_ACCESS`,
  `MULTI_STATEMENT`, `READ_ONLY_MODE`, `COLUMN_BLOCKED`, `UNPARSEABLE`). We
  collect **all** violations, not the first, so the agent can fix everything in
  one round trip.
* **Rules:** read-only mode; default-deny table allowlist; bare UPDATE/DELETE
  blocked (via `unbounded_write`); DDL default-deny with a per-table allowlist;
  system-catalog block; multi-statement block; LIMIT auto-injection on unbounded
  reads. `allowed_tables=None`/`ddl_allowed_tables=None` mean "no allowlist →
  allow all" (mirrors each other); empty set = deny all.
* **Posture:** unparseable → fail closed (block); clean reads fail open; LIMIT
  injection is a guardrail that fails open (rewrite failure → run original, never
  block a read).
* **Columns — scoping decision (honest limit, §11):** implemented a
  `blocked_columns` **denylist** for sensitive columns rather than a default-deny
  column *allowlist*. A column allowlist needs schema-aware `SELECT *` expansion
  to avoid massive false positives, which we don't have yet. The denylist matches
  explicit column references only; `SELECT *` can't be checked without schema.
  Documented as a known gap for a later (Day 10) read-side pass.
* **LIMIT injection** mutates a **fresh** parse (never the cached AST) and sets
  both `limitCount` and `limitOption=LIMIT_OPTION_COUNT` (a count alone renders
  without the LIMIT keyword). Result is LRU-cached on `(sql, limit)`.
* **Adapter:** `ShadowSession` gains an optional `Policy`. `None` → pure
  pass-through (keeps Day 1/2 behaviour/tests). With a policy: `enforce` blocks
  before touching the DB and returns the structured rejection; `observe` logs the
  decision but still runs (safe live trial). The server loads `default.yaml` at
  startup.

**Latency/safety impact:** `evaluate()` is pure in-memory. Measured warm (with
the injection cache) at **2.2 µs** (~2200× under the 5 ms p99 budget); the only
non-trivial cost is the first LIMIT injection per unique read (~255 µs re-parse +
render, then cached). Verified end-to-end on the live server: `DROP TABLE film`
blocked (`DDL_NOT_ALLOWED`), scoped SELECT allowed, `SELECT * FROM actor` capped.
Corpus: **18/18 red blocked with expected reason codes, 12/12 green allowed**;
108 tests green.

## 2026-06-20 — Day 3 QA fixes (P0/P1×3/P2 from QA_REPORT.md)
**Decision:** Fixed five issues from QA review of the Day 3 policy engine, each
with a regression test and (where relevant) new red-corpus cases.

* **P0 — unsafe SELECTs were treated as safe reads.** `SELECT pg_sleep(60)` ties
  up a connection and `SELECT ... FOR UPDATE` takes row locks. The classifier now
  records `has_locking` (any `LockingClause`) and `functions` (bare lower-cased
  `FuncCall` names). New policy rules block locking clauses
  (`LOCKING_NOT_ALLOWED`, toggle `block_locking`) and a **denylist** of unsafe
  functions (`FUNCTION_NOT_ALLOWED`): defaults cover `pg_sleep*`, `set_config`,
  `pg_read_file`/`pg_ls_dir`, `pg_terminate/cancel_backend`, plus prefix families
  `lo_*`, `dblink*`, `pg_advisory*`. Config can add to (never subtract from) the
  defaults.
* **P1a — invalid mode silently disabled enforcement.** `Policy.__post_init__`
  now rejects any mode outside {enforce, observe} (config fails loud at load).
  Defense in depth: the adapter computes `enforce = mode != "observe"`, so an
  unknown mode fails closed (enforces) rather than passing traffic through.
* **P1b — observe mode rewrote live reads.** The adapter ran
  `decision.effective_sql` in all modes, so observe injected a LIMIT and changed
  live results. Now only *enforcing* mode applies the rewrite; observe runs the
  original SQL unchanged and merely logs the would-be effective SQL.
* **P1c — sensitive columns leaked through `SELECT *`.** `blocked_columns` is now
  a **per-table map** (`{table: {cols}}`). That lets us fail closed on `SELECT *`
  / `t.*` over a table that has restricted columns (we can't prove the star
  excludes them) while NOT over-blocking stars on non-sensitive tables (green
  `SELECT * FROM actor` still passes). Resolves the Day 3 "column allowlist"
  gap honestly without full schema introspection.
* **P2 — DROP ignored the DDL allowlist target.** `DropStmt` names targets in
  `objects` (name lists), not RangeVars, so every DROP looked object-level and
  was blocked even when allowlisted. The classifier now extracts relation-drop
  targets into `tables`, so `ddl.allow` applies to drops (`DROP TABLE film`
  allowed when film is allowlisted; `DROP TABLE actor` still blocked).

**Latency/safety impact:** All fixes are pure classification/policy (no I/O
added). The extra `FuncCall`/`LockingClause`/`DropStmt` visits are on the
already-cached parse walk: classify cold measured **0.14 ms** (~36× under the
5 ms p99 budget), warm unchanged. Every change tightens the fail-closed posture.
Corpus now **23/23 red blocked (with expected codes), 12/12 green allowed**;
119 tests green.

## 2026-06-20 — Day 4: blast-radius simulation (differentiator #1)
**Decision:** Built `engine/simulate.py` and wired gated simulation into the
decision flow. Two measurement paths:
* **Estimate (cheap):** `EXPLAIN (FORMAT JSON) <stmt>` -> planner `Plan Rows` +
  `Total Cost`. No execution, no row locks. (Observed it can be wildly off —
  estimated 0 where the real count was 664 — which is exactly why precise
  exists.)
* **Precise (exact):** `BEGIN; SET LOCAL statement_timeout/lock_timeout; <stmt>;
  ROLLBACK`. Executes the write to read the exact affected-row count off the
  command tag, then rolls back. Hard time-boxed; aborts cleanly on
  `QueryCanceled`/`LockNotAvailable`; always rolls back in a `finally`.

* **Gating (sec. 4):** `is_risky_write` = single-statement WRITE only. Reads,
  DDL, multi-statement, and routine traffic are never simulated (verified: reads
  add no DB round-trip). Simulation is opt-in (`SimulationConfig.enabled`,
  default OFF in the library; ON in the shipped `default.yaml`) and runs only
  when enforcing.
* **Thresholds (`apply_blast_radius`, pure):** over `block_over_rows` -> hard
  block (`BLAST_RADIUS_EXCEEDED`); over `confirm_over_rows` -> allowed but
  `requires_confirmation`; a timeout -> `requires_confirmation` (couldn't bound
  the impact). Block always wins over confirm.
* **Confirmation path:** the adapter holds a confirm-gated write (returns
  `requires_confirmation=True` + the `simulation` summary, DB untouched); the
  agent re-issues with `confirm=True` to proceed. `confirm` never overrides a
  hard block.
* **Honest caveats surfaced & documented (sec. 11):** the precise path executes
  before rolling back, so non-transactional side effects still happen (sequence
  `nextval`, external triggers, `NOTIFY`); it briefly takes the real write's
  locks; very large writes may hit the statement timeout and escalate to
  confirmation rather than returning an exact count. Bonus: precise simulation
  also reveals writes that *would fail* — e.g. a `DELETE FROM rental` surfaced a
  foreign-key violation before any commit (captured as a regression test).

**Latency/safety impact:** Simulation is off the normal path by construction —
only risky writes, only when enforcing, time-boxed. `apply_blast_radius` is pure.
`SimulationConfig` lives in `Policy` (loaded from YAML at startup). 136 tests
green. The precise path is the most lock-sensitive code in the project; its
guards (statement/lock timeout + unconditional rollback) are the load-bearing
safety and are directly tested (clean abort + table-untouched + connection still
usable).

## 2026-06-20 — Day 4 QA fixes (P0/P1×4 from QA_REPORT.md)
**Decision:** Fixed five simulation issues, each with a regression test.

* **P0 — nested-DML CTEs undercounted blast radius.** For `WITH u AS (UPDATE ...
  RETURNING 1) SELECT ...` the outer command tag is `SELECT 1`, so the tag-based
  count was wrong. `simulate` now detects `nested_dml` and returns
  `method="unsupported"` (no count, error) instead of trusting the tag; the
  decision layer fails closed on it (blocks). "Block until supported," per the
  report's first option.
* **P1 — EXPLAIN had no hard timeout.** `_estimate` now runs `EXPLAIN` inside a
  transaction with `SET LOCAL statement_timeout`/`lock_timeout` and reports
  `timed_out`. Verified: with `ACCESS EXCLUSIVE` held on the table, the estimate
  aborts in ~210 ms (lock_timeout) instead of hanging.
* **P1 — every single-statement write was simulated.** Added a real risk
  predicate: `is_risky_write` = single UPDATE/DELETE/MERGE that is **not** a
  point write, or a data-modifying CTE. A new classifier signal `point_write`
  (WHERE is a single `col = value`) excludes routine key-scoped writes; plain
  INSERTs are excluded too. Routine writes now pay no simulation cost (no extra
  round trips, locks, or rollback side effects).
* **P1 — unknown simulation result failed open for writes.** `apply_blast_radius`
  now fails closed when there's no row count: a timeout holds for confirmation;
  any other unmeasurable case (planner error, unsupported nested-DML) is a hard
  block with a new `BLAST_RADIUS_UNKNOWN` reason code.
* **P1 — agent could bypass confirmation.** Removed the agent-facing `confirm`
  argument from the MCP tool entirely. The hold/approve seam is now
  `operator_approved` on `ShadowSession.run_query`, which is **not** exposed to
  the agent (an agent approving its own write is not human confirmation). A
  confirmation-gated write is simply held until an out-of-band operator approves
  — a real human approval channel lands in Day 6. `operator_approved` never
  overrides a hard block.

**Latency/safety impact:** All changes tighten the fail-closed posture and
*reduce* DB work on routine writes (most are no longer simulated). Classifier
cold latency 0.084 ms (~60× under the 5 ms p99 budget) with the added
`point_write` check. The risk predicate is a documented heuristic (can't see
schema, so equality on a non-unique column is a conservative false-negative for
riskiness — undo/policy still cover those). 141 tests green.

<!-- Append future decisions below this line. -->
