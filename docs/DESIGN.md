# DESIGN.md — living design doc

> The living design document for `agent-db-safety`. This evolves as the build
> progresses. Read it (and `DECISIONS.md`) at the start of every session. If a
> design choice here conflicts with `CLAUDE.md`, `CLAUDE.md` wins — surface the
> conflict rather than silently diverging.

Status: **Day 0 — Foundation (in progress).** Nothing below the latency section
is built yet; this is the intended shape, not a description of existing code.

---

## 1. One-paragraph product

A runtime safety layer that sits between AI agents and a database (Postgres
first) and makes risky agent-issued database actions safe *before* they commit
and reversible *after*. Statements are parsed → classified → checked against a
deterministic policy; risky writes have their **blast radius simulated** before
a decision; allowed writes are **recorded for instant undo**; blocks return a
**structured, machine-readable explanation** so the agent can self-correct. The
load-bearing safety is deterministic and fast. Anything probabilistic
(LLM-based intent checks) is strictly advisory and never on the hot path.

## 2. The latency budget (the prime directive — see CLAUDE.md §4)

**A safety layer that adds latency to normal traffic is worthless and will be
ripped out.** Every line that touches the request path is a latency liability.

- **Budget:** added **p99 < 5 ms** on the pass-through path; **p50 overhead
  negligible**. Enforced in CI from Day 7 — exceeding it is a build failure.
- **Hot path does only cheap, in-memory work:** cached parse → classify → rule
  check. No blocking network, no LLM, no disk waits on the critical path, ever.
- **Non-agent traffic must not pay for agent safety.** Prefer architectures
  where it bypasses the engine entirely.
- **Simulation is expensive and takes locks** → opt-in, gated to risky writes
  only, time-boxed (`statement_timeout` + `lock_timeout`), aborts cleanly.
  Never for reads or routine traffic.
- **Audit logging and LLM/intent checks are async / out-of-band.** A query
  never waits on a log write or an intent check.
- **Fail-closed for writes, fail-open for reads.** Configurable; documented
  wherever it applies.
- **Before adding anything to the request path, state its latency cost** (in the
  commit message) and confirm it fits the budget. If unsure, measure first.

## 3. Architecture

```
agent ──(MCP today / wire-protocol later)──> [thin adapter] ──> [SAFETY ENGINE core] ──> Postgres
                                                                      │
                                                  parse → classify → policy → (simulate?) → decide
                                                                      │            (undo record on writes)
                                                                  async: audit log, advisory intent check
```

- **Phase A:** MCP server adapter (`adapters/mcp_server.py`). Agents talk to us
  as their DB tool. Keeps us off the hot path of any human traffic by
  construction.
- **Phase B (stretch):** transparent Postgres wire-protocol proxy.
- **Invariant:** the policy / simulation / undo **engine is a standalone,
  transport-agnostic core library.** Adapters are thin. **No policy logic in the
  adapter layer.** The engine stays pure and independently testable so a future
  Go port of the proxy is feasible.

## 4. Engine components (intended)

| Module | Responsibility | Hot path? |
|---|---|---|
| `engine/parser.py` | SQL → AST via `pglast`; parse cache | yes (must be fast + cached) |
| `engine/classifier.py` | read/write/DDL, tables/columns, WHERE presence, multi-statement, catalog refs | yes |
| `engine/policy.py` | deterministic YAML-driven rules; structured rejections | yes (pure in-memory) |
| `engine/simulate.py` | EXPLAIN (cheap) + BEGIN/ROLLBACK (precise, gated) blast radius | **off** normal path; gated risky writes only |
| `engine/undo.py` | before-images / undo log + `revert(action_id)` | write-capture only, not reads |
| `engine/audit.py` | async, non-blocking audit trail (also the traffic corpus) | async only |
| `engine/intent.py` | advisory intent-mismatch detection; optional async LLM | **never** on hot path; advisory only |

## 5. Differentiators (protect these — CLAUDE.md §3)

1. **Blast-radius simulation** — quantify real impact before deciding.
2. **Reversibility / instant undo** — every agent write reversible with one
   command, full audit trail.
3. *(Advisory only)* **Intent-mismatch detection** — flag contradictions
   between stated task and actual blast radius. A flag, never a sole gate.

Do not let the build collapse into "just another rule engine that blocks
`DROP TABLE`."

## 6. Honest limits (keep visible — CLAUDE.md §11)

- Semantic correctness is undecidable in general; we catch blast-radius and
  scope-contradiction cases and make the rest reversible.
- `BEGIN/ROLLBACK` simulation has caveats (triggers w/ external calls, sequence
  increments, locks). Gate, time-box, document.
- Reversibility isn't infinite (external calls, cascades, consumed sequences).
- LLM intent checks are non-deterministic — advisory only.
- False positives erode trust; tune for low false positives; prefer
  "simulate + reversible" over "block" when the deterministic case is uncertain.

## 7. Open questions / to decide as we go

- Seed dataset choice for Day 0 (needs FKs + a few large tables for realistic
  simulation/benchmarks). Candidate: Pagila + inflated large tables. **TBD.**
- Parse-cache strategy and eviction.
- Exact structured-rejection schema (reason code / explanation / suggested fix).
- Undo record format and storage location (separate schema vs sidecar).

_Last updated: Day 0._
