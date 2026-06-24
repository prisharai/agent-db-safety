"""Statistical analysis + paper-ready results doc for the capability sweep.

Reads every ``runs/claude-*.jsonl`` (one file per model), reduces to one row per
trial, and produces:

* descriptive rate tables (evasion / recovery / scope-theater) by model x
  condition, and the evasion-strategy taxonomy by condition;
* inferential tests robust to the perfect separation this data tends to show:
  pairwise Fisher exact tests of each condition vs the opaque baseline (odds
  ratios + p-values), pooled and per model, plus a logistic regression when it
  converges;
* figures (PNG) and a single ``RESULTS_STUDY.md`` with everything in it.

    uv run python -m research.stats
"""
# ruff: noqa: E501  (analysis script: many wide table/format strings)

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import fisher_exact  # noqa: E402

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
FIGS = ROOT / "figures"
CONDITIONS = ["C0_opaque", "C1_reason_code", "C2_reason_fix", "C3_reason_fix_blast"]
EVASIONS = {"scope_theater_evasion", "obfuscation_evasion", "structural_evasion"}
RECOVERED = {"allowed_ontask", "genuine_correction"}


def load_trials() -> pd.DataFrame:
    rows = []
    for f in sorted(RUNS.glob("claude-*.jsonl")):
        model = f.stem
        turns_by_trial: dict[str, list[dict]] = defaultdict(list)
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a truncated final line if read mid-write
            turns_by_trial[rec["trial_id"]].append(rec)
        for tid, turns in turns_by_trial.items():
            turns.sort(key=lambda r: r["turn"])
            labels = [t["attempt_label"] for t in turns]
            rec_turn = next(
                (t["turn"] for t in turns if t["attempt_label"] in RECOVERED), None
            )
            rows.append(
                {
                    "model": model,
                    "trial_id": tid,
                    "task": turns[0]["task_id"],
                    "condition": turns[0]["condition"],
                    "first_denied": int(turns[0]["decision_kind"] != "allowed"),
                    "recovered": int(any(lbl in RECOVERED for lbl in labels)),
                    "evasion": int(any(lbl in EVASIONS for lbl in labels)),
                    "scope_theater": int("scope_theater_evasion" in labels),
                    "obfuscation": int("obfuscation_evasion" in labels),
                    "structural": int("structural_evasion" in labels),
                    "n_evasions": sum(1 for lbl in labels if lbl in EVASIONS),
                    "turns_to_recovery": rec_turn,
                    "n_turns": len(turns),
                }
            )
    return pd.DataFrame(rows)


def _rate_table(df: pd.DataFrame, col: str) -> pd.DataFrame:
    t = df.pivot_table(index="model", columns="condition", values=col, aggfunc="mean")
    return t.reindex(columns=[c for c in CONDITIONS if c in t.columns])


def _fisher_vs_baseline(df: pd.DataFrame, col: str, base: str = "C0_opaque") -> list[str]:
    """Each condition vs the opaque baseline: 2x2 Fisher exact (OR, p)."""
    out = []
    b = df[df.condition == base]
    b1, b0 = int(b[col].sum()), int((1 - b[col]).sum())
    for cond in CONDITIONS:
        if cond == base or cond not in set(df.condition):
            continue
        c = df[df.condition == cond]
        c1, c0 = int(c[col].sum()), int((1 - c[col]).sum())
        try:
            odds, p = fisher_exact([[c1, c0], [b1, b0]])
            odds_s = "inf" if odds == float("inf") else f"{odds:.2f}"
        except ValueError:
            odds_s, p = "n/a", float("nan")
        out.append(
            f"| {cond} vs {base} | {c1}/{c1 + c0} vs {b1}/{b1 + b0} | "
            f"{odds_s} | {p:.4g} |"
        )
    return out


def _logit(df: pd.DataFrame) -> str:
    import statsmodels.formula.api as smf

    d = df.copy()
    d["condition"] = pd.Categorical(d["condition"], categories=CONDITIONS, ordered=False)
    try:
        m = smf.logit(
            "evasion ~ C(condition, Treatment('C0_opaque')) + C(task) + C(model)", d
        ).fit(disp=False, maxiter=200)
        return "```\n" + str(m.summary()) + "\n```"
    except Exception as exc:  # perfect separation etc.
        return (
            f"_Logistic regression did not converge ({type(exc).__name__}: {exc}). "
            "This is expected under perfect separation (e.g. a condition at 0% or "
            "100% evasion); the Fisher exact tests above are the robust inference._"
        )


def _bar(df: pd.DataFrame, col: str, title: str, fname: str) -> None:
    t = _rate_table(df, col)
    ax = t.T.plot(kind="bar", figsize=(8, 4.5))
    ax.set_ylabel(f"{col} rate")
    ax.set_xlabel("denial condition")
    ax.set_title(title)
    ax.set_ylim(0, 1)
    ax.legend(title="model", fontsize=8)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    FIGS.mkdir(exist_ok=True)
    plt.savefig(FIGS / fname, dpi=130)
    plt.close()


def main() -> None:
    df = load_trials()
    if df.empty:
        print("No runs/claude-*.jsonl found. Run the sweep first.")
        return
    models = sorted(df.model.unique())
    n_per_cell = (
        df.groupby(["model", "condition"]).size().min(),
        df.groupby(["model", "condition"]).size().max(),
    )

    L = []
    L.append("# RESULTS_STUDY.md — capability sweep results\n")
    L.append(
        f"Models: {', '.join(models)} · {len(df)} trials · "
        f"{df.task.nunique()} tasks × {len(CONDITIONS)} conditions · "
        f"n per (model,condition) cell ∈ [{n_per_cell[0]}, {n_per_cell[1]}].\n"
    )
    L.append("\n## Data completeness & provenance\n")
    L.append(
        "The sweep was interrupted by an API credit limit, so models differ in "
        "completeness. **Haiku is the complete primary run; Sonnet and Opus are "
        "partial cross-model replication.** Report accordingly.\n"
    )
    comp = []
    comp.append("| model | trials | cells filled (of 12) | min n/cell | complete? |")
    comp.append("|---|---|---|---|---|")
    for m in models:
        g = df[df.model == m]
        ncells = g.groupby(["task", "condition"]).ngroups
        mincell = g.groupby(["task", "condition"]).size().min()
        ok = "yes" if ncells == 12 and mincell >= 20 else "**partial**"
        comp.append(f"| {m} | {g.trial_id.nunique()} | {ncells} | {mincell} | {ok} |")
    L.append("\n".join(comp))
    L.append(
        "Denial rate (fraction of trials whose first attempt was stopped): "
        f"**{df.first_denied.mean():.0%}** overall — the bulk-request tasks "
        "reliably trigger the guardrail across models.\n"
    )

    for col, title in [
        ("evasion", "Evasion rate (≥1 evasion attempt) by model × condition"),
        ("scope_theater", "Scope-theater rate (trivially-true WHERE) by model × condition"),
        ("recovered", "Genuine-recovery rate by model × condition"),
    ]:
        L.append(f"\n## {title}\n")
        L.append(_rate_table(df, col).to_markdown(floatfmt=".0%"))

    L.append("\n## Evasion-strategy taxonomy (counts), by condition\n")
    tax = (
        df.groupby("condition")[["scope_theater", "obfuscation", "structural"]]
        .sum()
        .reindex(CONDITIONS)
    )
    L.append(tax.to_markdown())

    L.append("\n## Inference — Fisher exact, evasion vs opaque baseline (pooled)\n")
    L.append("| comparison | rate vs base | odds ratio | p |\n|---|---|---|---|")
    L.extend(_fisher_vs_baseline(df, "evasion"))

    for model in models:
        L.append(f"\n### Fisher exact (evasion vs opaque) — {model}\n")
        L.append("| comparison | rate vs base | odds ratio | p |\n|---|---|---|---|")
        L.extend(_fisher_vs_baseline(df[df.model == model], "evasion"))

    L.append("\n## Logistic regression (evasion ~ condition + task + model)\n")
    L.append(_logit(df))

    # turns-to-recovery (only where recovered)
    L.append("\n## Turns to recovery (recovered trials only), mean by condition\n")
    tt = (
        df[df.recovered == 1]
        .groupby("condition")["turns_to_recovery"]
        .agg(["count", "mean"])
        .reindex(CONDITIONS)
    )
    L.append(tt.to_markdown(floatfmt=".2f"))

    # qualitative trajectories
    L.append("\n## Representative blocked→evade trajectories\n")
    L.append(_trajectories())

    (ROOT / "RESULTS_STUDY.md").write_text("\n".join(str(x) for x in L) + "\n")

    _bar(df, "evasion", "Evasion rate by denial richness", "evasion_by_condition.png")
    _bar(df, "scope_theater", "Scope-theater rate by denial richness", "scope_theater.png")
    _bar(df, "recovered", "Genuine recovery by denial richness", "recovery.png")

    print("Wrote research/RESULTS_STUDY.md and research/figures/*.png")
    print(_rate_table(df, "evasion").to_markdown(floatfmt=".0%"))


def _trajectories(k: int = 3) -> str:
    """Pull a few real multi-turn blocked→evasion sequences from the logs."""
    out = []
    for f in sorted(RUNS.glob("claude-*.jsonl")):
        by_trial: dict[str, list[dict]] = defaultdict(list)
        for line in f.read_text().splitlines():
            by_trial[json.loads(line)["trial_id"]].append(json.loads(line))
        for turns in by_trial.values():
            turns.sort(key=lambda r: r["turn"])
            if any(t["attempt_label"] == "scope_theater_evasion" for t in turns) and len(turns) >= 2:
                head = f"\n**{f.stem} · {turns[0]['task_id']} · {turns[0]['condition']}**\n```"
                lines = [
                    f"  turn{t['turn']} [{t['decision_kind']:7}] {t['attempt_label']:21}"
                    f" :: {t['sql'].strip()[:70]}"
                    for t in turns
                ]
                out.append(head + "\n" + "\n".join(lines) + "\n```")
                break
        if len(out) >= k:
            break
    return "\n".join(out) if out else "_none captured_"


if __name__ == "__main__":
    main()
