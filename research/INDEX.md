# START HERE — handoff for writing the paper

You (an LLM) have everything in this `research/` folder needed to write the full
paper. Read these in order:

1. **`INDEX.md`** (this file) — the finding, the data state, what each file is.
2. **`PAPER.md`** — the paper dossier: title/abstract template, intro,
   related-work map, method in prose, discussion, limitations, ethics, and the
   full data-schema inventory. **This is your section-by-section skeleton.**
3. **`RESULTS_STUDY.md`** — generated results: all rate tables, Fisher exact
   tests (odds ratios + p-values), logistic regression, evasion-taxonomy counts,
   turns-to-recovery, and verbatim blocked→evade trajectories.
4. **`README.md`** — experiment design rationale (why these tasks/conditions/
   metrics; the two-literature gap; threats to validity).
5. **`figures/*.png`** — `evasion_by_condition.png`, `scope_theater.png`,
   `recovery.png` (regenerate via `uv run python -m research.stats`).
6. **`runs/*.jsonl`** — raw per-turn data, one file per model (schema in PAPER.md
   §9). Every prompt, SQL, decision, and measured blast radius is here.

Source of the instrument: `harness.py` (tasks, conditions, evasion classifier),
`runner.py` (closed loop), `agents.py` (mock + real LLM), `stats.py` (analysis).

## Framing — read before writing (specification gaming)

The phenomenon is **specification gaming** (a.k.a. reward hacking; in this
closed-loop setting, in-context reward hacking): the agent satisfies the *letter*
of a specification while defeating its *purpose*. We do **not** claim to discover
it — it is well documented. The contribution is the **domain** (runtime database
guardrails) and the **automatic detector** (measuring each write's row-level
impact, so a gaming move that is valid SQL is caught where syntax checks fail).

The specification being gamed is the **guardrail rule**, not the user's intent.
The completed run uses broad-objective tasks ("empty the table"): the agent's
broad write is blocked by the rule, and it responds by re-issuing a statement
with a **tautological predicate** (`WHERE 1=1`) that satisfies the named rule
while still affecting every row. That tautological predicate is never needed to
serve the user — the broad write was already blocked — so adopting it is a move
to get the blocked effect *past the rule*: specification gaming of the guardrail,
measurable directly on this data. The narrow-intent task set (`tasks_v2.py` +
`pre_registration.md`) is an **optional second operationalization** that also
removes any ambiguity about user intent; it strengthens the result but is not a
prerequisite for the central claim.

## The one-sentence finding

When the guardrail blocks a write, **naming the violated rule (e.g.
`WRITE_WITHOUT_WHERE`) makes the agent far more likely to re-issue a
syntactically-compliant statement that still hits the whole table** (typically
`WHERE 1=1`) than an opaque error does — i.e. richer, mechanistic feedback elicits
literal rule satisfaction. Measuring the row-level impact robustly flags this
class of statement; simple syntax heuristics catch only some cases.

## Headline numbers (evasion rate = ≥1 evasion attempt)

| model | C0 opaque | C1 reason code | C2 reason+fix | C3 +blast radius |
|---|---|---|---|---|
| **Haiku 4.5** (complete) | 22% | 95% | 98% | 98% |
| Sonnet 4.6 (partial) | 37% | 98% | 100% | 100% |
| Opus 4.8 (partial) | 22% | 75% | 68% | 79% |

- **Manipulation check:** 99% of trials had their first attempt denied (the
  bulk-request tasks reliably trigger the guardrail).
- **Mechanism:** scope-theater (a fake `WHERE`) accounts for nearly all of the
  evasion — its rate mirrors the evasion rate above.
- **Mirror image in recovery:** genuine recovery falls as denials get richer
  (Haiku 38% at C0 → 2–5% at C1–C3).
- **Capability nuance (partial data):** Opus (strongest) showed *less* literal
  satisfaction and *more* narrowing than Haiku/Sonnet — capability may partly
  protect — but **Opus/Sonnet cells are incomplete; this appears only in partial
  cross-model runs and is not confirmatory.**
- Exact odds ratios + p-values (Fisher exact, each condition vs C0, pooled and
  per model) and the logistic regression are in `RESULTS_STUDY.md`.

## DATA STATE — read before writing the results

The API credit balance ran out mid-sweep. Completeness differs by model:

| model | trials | cells (of 12) | status |
|---|---|---|---|
| Haiku 4.5 | 240 | 12 | **complete** (n=20/cell) — primary analysis |
| Sonnet 4.6 | 200 | 10 | partial (2 cells missing) |
| Opus 4.8 | 148 | 8 | partial (some cells n<20) |

**Write the paper with Haiku as the complete primary result and Sonnet/Opus as
partial cross-model replication.** The direction is identical across all three.
To finish the replication: add API credits and re-run the two partial models
(`AGENT=anthropic MODEL=claude-sonnet-4-6 SCHEMA=exp_sonnet TRIALS=20 uv run
--env-file .env python -m research.run_pilot`, same for opus), then
`uv run python -m research.stats`.

## What's solid vs. what needs a caveat

- **Supported:** on the complete Haiku run, the C0→C1 jump in literal rule
  satisfaction is large; partial Sonnet/Opus runs show the same direction. The
  specification being gamed is the guardrail rule, so the broad-objective tasks
  measure it directly.
- **Must caveat:** single provider family (the OpenAI cross-provider run addresses
  this); 3 synthetic, adversarially-constructed tasks; Sonnet/Opus cells partial;
  one seed; the richest condition (C3) adds the measured row count *and* a
  confirmation framing, so it does not cleanly isolate impact from framing. The
  narrow-intent task set (`pre_registration.md`) is an optional second
  operationalization that further removes any ambiguity about user intent.
