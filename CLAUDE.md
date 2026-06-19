# CLAUDE.md

> Operating guide for Claude Code on this project. Read this file fully at the start of every session before writing any code. If anything here conflicts with a request I make in chat, surface the conflict instead of silently choosing.

---

## 1. What we are building (one paragraph)

A **runtime safety layer that sits between AI agents and a database (Postgres first)** and makes risky agent-issued database actions safe *before* they commit — and reversible *after*. Every statement an agent sends is parsed, classified, and checked against a deterministic policy; genuinely risky writes are **simulated to measure their blast radius** (e.g. "this would modify 2.3M rows") before a decision is made; allowed writes are **recorded so they can be instantly undone**; and when something is blocked, the agent gets a **structured, machine-readable explanation so it can self-correct and retry**. The load-bearing safety is deterministic and fast. Anything probabilistic (LLM-based intent checks) is strictly advisory and never on the hot path.

## 2. What this is NOT (anti-goals — do not drift into these)

- **Not "Git for databases."** We do not version data or schema over time (that's Dolt). Borrow git's *spirit* of reversibility only.
- **Not a schema-migration tool.** We are not Flyway / Liquibase / Alembic / Prisma migrations. We govern *live runtime traffic*, not change-log files.
- **Not a semantic layer / metrics store.** We do not replace raw SQL with a query DSL (that's the SLayer/Cube approach). Agents keep writing SQL; we supervise it.
- **Not a replacement for database permissions.** Postgres roles/RLS still do their job. We do the things permissions *cannot* express: dynamic, query-shape-aware, blast-radius-aware, reversible, per-agent-audited control.
- **Not an ORM or a connection pooler clone.** We may behave like a proxy later, but the product is safety, not pooling.

## 3. The wedge — why this is different from existing tools

There are already prevention/policy products in this space (e.g. Maybe Don't, hoop.dev Access Guardrails, AI2sql guardrails, AWS Strands hooks). They mostly do **policy-based blocking** of agent actions. They generally do **not** lead with the two things below. **These two capabilities are our differentiation. Protect them. Never let the build collapse into "just another rule engine that blocks DROP TABLE."**

1. **Blast-radius simulation** — quantify the real impact of a risky write *before* deciding (exact/estimated rows affected, estimated cost), not just pattern-match the statement text.
2. **Reversibility / instant undo** — every agent write is recorded such that it can be reverted with one command, with a full audit trail. Prevention catches the obvious; reversibility covers everything prediction provably cannot (intent is undecidable in the general case — see §11).

A third, **advisory-only** differentiator: **intent-mismatch detection** — compare the agent's *stated task* to the query's actual blast radius and flag contradictions. This is a flag, not a gate, and must never block on its own without deterministic backing or human confirmation.

## 4. THE NON-NEGOTIABLE CONSTRAINT: do not slow down the database

This is the single most important engineering rule in the project. A safety layer that adds latency to normal traffic will be ripped out and is worthless. Treat every line of code that touches the request path as a latency liability.

**Hard rules — enforce these in every "day" below:**

- **Normal / human / non-agent traffic must not pay for agent safety.** Prefer architectures where non-agent traffic bypasses the engine entirely. If traffic must pass through, the added work must be near-zero.
- **The hot path does only cheap, in-memory work:** parse (cached), classify, check rules. No blocking network calls, no LLM calls, no disk waits on the critical path. Ever.
- **Simulation is expensive and takes locks. It is opt-in, gated, and time-boxed.** Only flagged *risky writes* are simulated — never reads, never routine traffic. Every simulation has a hard statement timeout and lock timeout and aborts cleanly.
- **LLM / intent checks are async or out-of-band only.** They never sit in the path of a query the agent is waiting on.
- **Audit logging is asynchronous and non-blocking.** Never make a query wait on a log write.
- **Define an explicit latency budget and enforce it in CI.** Target: **added p99 latency on the pass-through path < 5 ms; p50 overhead effectively negligible.** A benchmark that exceeds the budget is a build failure, not a warning (see §9, Day 7).
- **Decide fail-open vs fail-closed explicitly and make it configurable.** Default: writes fail *closed* (block on uncertainty — safety), reads fail *open* (never let the safety layer take down read availability). Document this everywhere it applies.
- **Before adding anything to the request path, state its latency cost in the commit message and confirm it fits the budget.** If unsure, measure first.

## 5. Architecture direction

**Phase A (build this first): an MCP server.** The agent (Claude Code, Cursor, etc.) talks to our server as its database tool. This is the fastest path to a working, demoable product, it targets the exact users we care about, and it keeps us off the hot path of any human traffic by construction.

**Phase B (stretch goal, later): a transparent wire-protocol proxy.** Speaks the Postgres wire protocol so it works with any client automatically. Harder and latency-critical.

**The rule that makes both possible: the policy/simulation/undo engine is a standalone, transport-agnostic core library.** The MCP server and the future proxy are thin adapters over the same engine. **Never bake policy logic into the MCP layer.** Keep the engine pure and independently testable.

```
agent ──(MCP today / wire-protocol later)──> [thin adapter] ──> [SAFETY ENGINE core] ──> Postgres
                                                                      │
                                                  parse → classify → policy → (simulate?) → decide
                                                                      │            (undo record on writes)
                                                                  async: audit log, advisory intent check
```

## 6. Tech stack & conventions

- **Language (Phase A):** Python 3.11+. Chosen for iteration speed and the official MCP SDK. The eventual wire proxy (Phase B) should target **Go** for performance; design the engine so a port is feasible.
- **SQL parsing:** use a real Postgres parser, not regex or hand-rolled matching. Prefer `pglast` (Python binding to libpg_query, the actual Postgres parser) for fidelity; `sqlglot` is an acceptable fallback. **Never classify statements by string matching** — always operate on the AST.
- **DB driver:** `asyncpg` (async, fast).
- **Database for dev:** Postgres via Docker Compose. The filesystem/DB resets freely; never assume persistence between runs.
- **Config:** declarative **YAML** policy files, version-controlled, human-readable.
- **Tests:** `pytest`. Tests are written alongside code in the same slice, not deferred.
- **Formatting/lint:** `ruff` + `black`. Keep it clean from day one.
- **Async everywhere on the request path.** No synchronous blocking I/O in the engine.

### Repo structure (create in Day 0, evolve as needed)
```
/engine/            # transport-agnostic core: parse, classify, policy, simulate, undo
  parser.py
  classifier.py
  policy.py
  simulate.py
  undo.py
  audit.py          # async, non-blocking
  intent.py         # advisory only
/adapters/
  mcp_server.py     # Phase A
  wire_proxy/       # Phase B (stretch)
/policies/          # example YAML policies
/corpus/            # red (should-block) + green (should-allow) query sets + captured agent traffic
/benchmarks/        # pgbench harness, latency budget checks
/tests/
/docs/
  DECISIONS.md      # running decision log — update every session
  DESIGN.md         # the living design doc
docker-compose.yml
CLAUDE.md
README.md
```

## 7. How you (Claude Code) should work on this project

- **The builder is learning this space. Explain your reasoning as you go** — what each piece does and why — especially for protocol, parsing, and concurrency. Don't just emit code.
- **One vertical slice at a time.** Each "day" in §8 must *work end-to-end* and be committed before moving on. Do not start the next day until the current day's "Done when" criteria pass.
- **Do not one-shot the project.** Quality over speed. It is correct and expected for this to span many sessions.
- **Before adding anything to the request path, check it against the §4 latency budget.** Call it out explicitly.
- **Ask before:** introducing a new dependency, changing the architecture, or doing anything that could touch the hot path's performance. Default to the smallest change that works.
- **Write tests in the same slice as the code.** A slice isn't done without its tests.
- **Commit often, in small logical units,** with messages that note any request-path/latency impact.
- **Keep `docs/DECISIONS.md` updated** with every non-trivial choice and its rationale.
- **Be honest about limits.** If something can't be done safely or can't meet the latency budget, say so rather than shipping something that looks safe but isn't (see §11).

## 8. The build plan, segmented by "day"

Each day is a self-contained vertical slice. "Day" = a logical milestone, not a literal 24 hours. Do them in order. Each has: **Goal · Build · Done when · Latency/safety notes.**

### Day 0 — Foundation
- **Goal:** A clean repo and a running local Postgres an agent can reach.
- **Build:** Repo skeleton (§6), Docker Compose with Postgres + a seed dataset (use something realistic with FKs and a few large tables so simulation/benchmarks are meaningful), `docs/DECISIONS.md` and `docs/DESIGN.md` stubs, `ruff`/`black`/`pytest` wired up, a trivial smoke test.
- **Done when:** `docker compose up` gives a seeded Postgres; `pytest` runs green; README explains how to start.
- **Notes:** Establish the latency-budget mindset in DESIGN.md now.

### Day 1 — Pass-through MCP server + Shadow Mode
- **Goal:** An agent can connect and run queries through us, and we **log every query but block nothing.**
- **Build:** Minimal MCP server exposing a `run_query` tool that forwards to Postgres via `asyncpg` and returns results unchanged. Async, non-blocking audit logging of every statement (this log is also our **traffic corpus** — see §10).
- **Done when:** Claude Code (or another MCP client) connects, runs reads and writes, gets correct results; every statement appears in the async log; zero enforcement.
- **Notes:** This is the pass-through baseline we will benchmark against. Logging must not block the query.

### Day 2 — Parse & classify (still observe-only)
- **Goal:** Understand every statement structurally.
- **Build:** In `engine/parser.py` + `classifier.py`: parse each statement to an AST (`pglast`); classify type (read / write / DDL), tables & columns touched, presence/absence of a `WHERE`, multi-statement detection, references to system catalogs. Cache parses. Log the classification alongside each query. Still no enforcement.
- **Done when:** For the corpus from Day 1, classifications are correct, including tricky cases (CTEs, sub-selects, `UPDATE ... FROM`, statements with comments). Parse failures are handled safely (fail-closed for writes).
- **Notes:** Parsing is on the hot path → it must be fast and cached. Measure it.

### Day 3 — Deterministic policy engine v1
- **Goal:** Block the obviously dangerous; allow the obviously safe. Deterministic only.
- **Build:** `engine/policy.py` driven by YAML: read-only mode; table/column allowlists (default-deny); block bare `DELETE`/`UPDATE` with no `WHERE`; block `DROP`/`TRUNCATE` outside allowlist; block system-catalog access; auto-inject `LIMIT` on unbounded reads; statement-shape validation. On block, return a **structured, machine-readable rejection** (reason code, human explanation, suggested fix) so the agent can self-correct.
- **Done when:** Red corpus is blocked, green corpus passes, every rejection includes actionable feedback; all deterministic, no LLM.
- **Notes:** This is hot-path work — keep it pure in-memory rule evaluation. No I/O.

### Day 4 — Blast-radius simulation (differentiator #1)
- **Goal:** Quantify a risky write's true impact before deciding.
- **Build:** `engine/simulate.py`: cheap path = `EXPLAIN` (no execution, no locks) for cost/row estimates on flagged statements; precise path = `BEGIN; <statement>; <capture affected row count>; ROLLBACK` for flagged *writes only*, behind strict guards (hard `statement_timeout`, `lock_timeout`, opt-in config). Add row-count / cost thresholds that move a statement to block or to human-confirmation.
- **Done when:** A risky `UPDATE`/`DELETE` reports accurate affected-row counts before committing; thresholds trigger correctly; simulation never runs for reads or routine traffic; every simulation respects its timeouts and aborts cleanly.
- **Notes:** **This is the most latency- and lock-sensitive feature in the product.** It must be gated to risky writes, time-boxed, and never on the path of normal traffic. Document the honest caveats (triggers/sequences/external side-effects may not roll back — see §11).

### Day 5 — Reversibility / instant undo (differentiator #2)
- **Goal:** Any agent write can be undone with one command.
- **Build:** `engine/undo.py`: for allowed writes, record what's needed to reverse them (before-images of affected rows / an undo log keyed by a per-action ID), plus a `revert(action_id)` operation and a full audit trail tying each change to an agent identity and its stated task.
- **Done when:** An agent performs a write; a single revert restores prior state; the audit trail shows who/what/when and the undo record; reverts are themselves audited.
- **Notes:** Capturing before-images has a cost — keep it off the hot path of *reads*, make the write-capture efficient and configurable, and be explicit in docs about what cannot be perfectly reversed.

### Day 6 — Intent-mismatch detection (advisory only)
- **Goal:** Flag when a query's blast radius contradicts the agent's stated goal.
- **Build:** `engine/intent.py`: capture the agent's stated task (from MCP context), compare declared scope to measured blast radius, raise an advisory flag on large mismatches. Optional LLM "second opinion" **behind a flag, async/out-of-band, on the risky subset only** — never blocking, never load-bearing. Add a human-in-the-loop confirmation path for high-mismatch cases.
- **Done when:** A query whose scope wildly exceeds its stated task is flagged (deterministically where possible); the optional LLM check is clearly advisory and off the hot path; nothing blocks on intent alone.
- **Notes:** Reframe: we don't "know intent," we **detect contradiction**. Keep all of this off the path of queries the agent is waiting on.

### Day 7 — Performance hardening & proof (the §4 gate)
- **Goal:** Prove we don't slow the database.
- **Build:** `benchmarks/`: `pgbench`-driven harness comparing direct-to-Postgres vs through-our-engine; report p50/p99 added latency and throughput delta. Add fast-path/bypass for non-agent or already-approved traffic; confirm logging and intent checks are fully async; add a CI check that **fails the build if added p99 > 5 ms** on the pass-through path.
- **Done when:** Benchmarks show overhead within budget; the CI latency gate is active; results are written into the README as hard numbers.
- **Notes:** This day is the credibility of the whole project. Treat the budget as sacred.

### Day 8 — Test suite consolidation
- **Goal:** Lock in correctness and resistance to evasion.
- **Build:** Formalize red (should-block) and green (should-allow) corpora; measure **false-positive and false-negative rates** (false negatives on the red set must be ~zero; keep false positives low to preserve trust). Protocol/compatibility tests running a real client and a real ORM through the server unchanged. Evasion tests (comments, whitespace, casing, sneaky second statements, encoding tricks). Edge cases (malformed/huge queries) must fail safe.
- **Done when:** CI runs the full matrix; metrics are reported; every previously-found evasion is a permanent regression test.

### Day 9 — The demo & portfolio writeup
- **Goal:** The "whoa" moment, captured.
- **Build:** Connect a real agent (Claude Code) through the server. Script the demo: agent attempts a destructive op → blocked with a clear reason → agent self-corrects and the safe version passes; show a blast-radius preview ("would hit 2.3M rows"); show an undo of an agent write. Record it. Write the README/portfolio narrative including the latency numbers and the honest limits.
- **Done when:** A short recorded demo exists; README tells the story end to end with real metrics.

### Day 10+ — Stretch goals (only after the above is solid)
- **Wire-protocol proxy (Phase B):** TCP pass-through first (prove a normal client still works), then parse the startup/auth handshake, then intercept the query message, then plug in the *existing* engine. Latency budget applies even harder here.
- **Read-side / exfiltration guardrails:** detect mass reads of sensitive tables / data leaving into LLM context; this opens a compliance-oriented angle with a real buyer.

## 9. Definition of done (per slice, every time)
A slice is done only when: it works end-to-end · it has tests in the same commit · it respects the §4 latency budget (and says so) · `docs/DECISIONS.md` is updated · it's committed. No exceptions, no "I'll add tests later."

## 10. Primary research that runs alongside the build
The Shadow Mode log from Day 1 is also a **research asset**: a corpus of real agent-generated SQL almost nobody has studied systematically. Periodically review it to (a) sharpen the red/green corpora, (b) discover failure patterns that should become rules, and (c) inform the design. Capture the agent's stated task next to each query wherever possible — that pairing is what makes intent-mismatch detection possible at all.

## 11. Honest limits — keep these visible, never paper over them
- **Semantic correctness is undecidable in general.** A query can be perfectly valid SQL and still be "wrong." We do not claim to catch all of these — we catch *blast-radius* and *scope-contradiction* cases, and we make everything else *reversible*. This honesty is a feature, not a weakness.
- **Simulation via `BEGIN/ROLLBACK` has real caveats:** triggers calling external services, sequence increments, and other side-effects may not roll back; simulation takes locks. Gate, time-box, and document it.
- **Reversibility is not infinite:** some effects (external calls, cascades, already-consumed sequences) can't be perfectly undone. State this plainly.
- **LLM intent checks are non-deterministic and can be wrong.** They are advisory only, never the last line of defense, never on the hot path.
- **False positives erode trust.** Over-blocking gets the tool disabled, which protects nothing. Tune for low false positives; favor "simulate + reversible" over "block" wherever the deterministic case is uncertain.
- **If the stated intent is itself wrong, nothing catches it.** We check query-vs-task, not task-vs-reality.

## 12. Glossary (for the builder)
- **AST** — Abstract Syntax Tree; the structured, inspectable form of a parsed SQL statement.
- **MCP** — Model Context Protocol; the standard interface AI agents use to call external tools (our Phase A transport).
- **Hot path / request path** — the code a query passes through while the agent waits for a result. Latency-critical; keep it minimal.
- **Blast radius** — how much a statement would actually change or expose (rows affected, cost, data returned).
- **Shadow / observe mode** — logging and analyzing traffic without enforcing anything.
- **Simulation** — running a statement in a way that reveals its effect without committing it (e.g. `BEGIN ... ROLLBACK`).
- **Reversibility / undo** — recording enough to restore prior state after a write.
- **p50 / p99** — median and 99th-percentile latency; p99 is where real-world pain shows up.
- **Fail-open / fail-closed** — on uncertainty, allow (favor availability) vs block (favor safety). Configurable; default per §4.
- **False negative / false positive** — a dangerous query wrongly allowed / a safe query wrongly blocked.
- **Idempotency** — an operation that's safe to apply more than once without extra effect.

## 13. Session checklist (run this each time you start)
1. Re-read this file and `docs/DESIGN.md` and the latest `docs/DECISIONS.md`.
2. Identify the current "day"/slice and its "Done when".
3. Do the smallest end-to-end increment toward it.
4. For anything touching the request path, state its latency cost against the §4 budget.
5. Write tests in the same slice.
6. Update `docs/DECISIONS.md`; commit; stop at a working checkpoint.
