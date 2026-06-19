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

<!-- Append future decisions below this line. -->
