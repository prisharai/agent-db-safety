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

<!-- Append future decisions below this line. -->
