"""
Correlation analysis for the "duty coverage vs delay performance are governed
by independent mechanisms" claim - LONG-BY-METRIC-NAME format.

Expected input shape: one row per (Metric, run) combination, where a "run" is
identified by active_crew, reserve_crew, absence_prob, delay_prob, seed.
Columns: Metric | Category | Description | Value | active_crew | reserve_crew
| absence_prob | delay_prob | seed

ASSUMPTION FLAGGED: a "Value" column holding the metric's numeric value is
required for any of this to work, and wasn't explicitly listed - this is
assumed present (matching your metrics.py output schema: Metric | Category |
Value | Description). If your actual value column has a different name, add
it to VALUE_COL_CANDIDATES below.

This script:
  1. Pivots the long-by-metric format into one row per run (wide format),
     with one column per metric name.
  2. Optionally filters to a specific active_crew / reserve_crew configuration
     (most of your charts fixed Active Crew = 30) - set FILTER below.
  3. Computes pairwise Pearson correlations between each driver (delay_prob,
     absence_prob) and each outcome (duty coverage, on-time rate, recovery
     time), both on raw per-seed rows AND on cell-means (averaged across
     seeds) as a robustness check against seed reuse across parameter cells.
  4. Runs an OLS regression with an interaction term per outcome:
         outcome ~ delay_prob + absence_prob + delay_prob:absence_prob
     to directly test additive vs. interacting effects.

No statsmodels dependency - OLS is computed via numpy + scipy.stats.
"""

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, t as t_dist

# ============================================================
# CONFIG - edit these to match your file
# ============================================================
EXCEL_PATH = "C:\\Users\\ChloeHaffenden\\Documents\\simulation_paper\\output\\crew_resilience_seed_sweep_20260818_111840.xlsx"   # <-- change to your file path
SHEET_NAME = 0                                 # <-- change if not the first sheet

# Set to None to use all rows, or e.g. {"active_crew": 30, "reserve_crew": 0}
# to restrict to one crew configuration before computing correlations -
# mixing crew configurations together would confound the delay/absence
# analysis with a third varying factor.
FILTER = {"active_crew": 30, "reserve_crew": 0}
 
METRIC_COL_CANDIDATES = ["Metric", "metric"]
CATEGORY_COL_CANDIDATES = ["Category", "category"]
VALUE_COL_CANDIDATES = ["Value", "value"]  # <-- flagged assumption, see docstring
DELAY_COL_CANDIDATES = ["delay_prob", "Delay Probability"]
ABSENCE_COL_CANDIDATES = ["absence_prob", "Absence Probability"]
SEED_COL_CANDIDATES = ["seed", "Seed"]
ACTIVE_CREW_COL_CANDIDATES = ["active_crew", "Active Crew"]
RESERVE_CREW_COL_CANDIDATES = ["reserve_crew", "Reserve Crew"]
 
# Known exact metric-name strings from metrics.py, plus fallback substrings
# for fuzzy matching in case labels were edited.
OUTCOME_METRIC_CANDIDATES = {
    "duty_coverage": ["Duty coverage (%)", "duty coverage"],
    "on_time_rate": ["On-time rate (%)", "on-time rate", "on time rate"],
    "recovery_time": [
        "Schedule recovery time (min/flt)", "schedule recovery time", "recovery time",
    ],
}
 
 
# ============================================================
# Column / metric resolution helpers
# ============================================================
def _resolve_column(df: pd.DataFrame, candidates: list[str], label: str) -> str:
    lookup = {str(col).strip().lower(): col for col in df.columns}
    for candidate in candidates:
        match = lookup.get(candidate.strip().lower())
        if match is not None:
            return match
    raise KeyError(
        f"Could not find a column for '{label}' among {candidates}. "
        f"Available columns: {list(df.columns)}"
    )
 
 
def _resolve_metric_name(available_metrics: list[str], candidates: list[str], label: str) -> str:
    lookup = {str(m).strip().lower(): m for m in available_metrics}
    # exact match first
    for candidate in candidates:
        match = lookup.get(candidate.strip().lower())
        if match is not None:
            return match
    # fallback: substring match
    for candidate in candidates:
        for m in available_metrics:
            if candidate.strip().lower() in str(m).strip().lower():
                return m
    raise KeyError(
        f"Could not find a metric matching '{label}' among {candidates}. "
        f"Available metric names: {available_metrics}"
    )
 
 
# ============================================================
# Manual OLS with p-values (no statsmodels dependency)
# ============================================================
def ols_with_pvalues(X: np.ndarray, y: np.ndarray, term_names: list[str]) -> pd.DataFrame:
    n, k = X.shape
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    residuals = y - X @ beta
    dof = n - k
    sigma2 = float(residuals @ residuals) / dof
    xtx_inv = np.linalg.inv(X.T @ X)
    cov_beta = sigma2 * xtx_inv
    se = np.sqrt(np.diag(cov_beta))
    t_stats = beta / se
    p_values = 2 * (1 - t_dist.cdf(np.abs(t_stats), dof))
 
    ss_res = float(residuals @ residuals)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r_squared = 1 - ss_res / ss_tot
 
    result = pd.DataFrame({
        "term": term_names, "coef": beta, "std_err": se, "t": t_stats, "p_value": p_values,
    })
    result.attrs["r_squared"] = r_squared
    result.attrs["n"] = n
    return result
 
 
def run_interaction_regression(df: pd.DataFrame, x1_col: str, x2_col: str, y_col: str) -> pd.DataFrame:
    x1 = df[x1_col].to_numpy(dtype=float)
    x2 = df[x2_col].to_numpy(dtype=float)
    y = df[y_col].to_numpy(dtype=float)
    interaction = x1 * x2
    X = np.column_stack([np.ones_like(x1), x1, x2, interaction])
    term_names = ["intercept", x1_col, x2_col, f"{x1_col}:{x2_col} (interaction)"]
    return ols_with_pvalues(X, y, term_names)
 
 
def run_quadratic_regression(df: pd.DataFrame, x_col: str, y_col: str) -> pd.DataFrame:
    """Fit y ~ x + x^2 to test for non-linearity in a single predictor - e.g.
    whether duty_coverage's relationship with absence_prob curves (consistent
    with reserve-queue exhaustion at higher absence rates) rather than being
    purely linear. The x^2 term's p-value is the test of non-linearity; R^2
    here should be compared against the linear-only R^2 (see
    print_quadratic_comparison) to judge whether the added curvature is
    meaningful, not just statistically detectable.
    """
    x = df[x_col].to_numpy(dtype=float)
    y = df[y_col].to_numpy(dtype=float)
    X = np.column_stack([np.ones_like(x), x, x ** 2])
    term_names = ["intercept", x_col, f"{x_col}^2 (quadratic)"]
    return ols_with_pvalues(X, y, term_names)
 
 
def run_linear_only_regression(df: pd.DataFrame, x_col: str, y_col: str) -> pd.DataFrame:
    """y ~ x only - the linear-only baseline to compare the quadratic fit's
    R^2 against."""
    x = df[x_col].to_numpy(dtype=float)
    y = df[y_col].to_numpy(dtype=float)
    X = np.column_stack([np.ones_like(x), x])
    term_names = ["intercept", x_col]
    return ols_with_pvalues(X, y, term_names)
 
 
def pearson_row(df: pd.DataFrame, x_col: str, y_col: str, label: str) -> dict:
    r, p = pearsonr(df[x_col], df[y_col])
    return {"pair": label, "n": len(df), "pearson_r": r, "p_value": p}
 
 
def significance_stars(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"
 
 
# ============================================================
# Main
# ============================================================
def main():
    raw = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
 
    metric_col = _resolve_column(raw, METRIC_COL_CANDIDATES, "Metric")
    value_col = _resolve_column(raw, VALUE_COL_CANDIDATES, "Value")
    delay_col = _resolve_column(raw, DELAY_COL_CANDIDATES, "delay_prob")
    absence_col = _resolve_column(raw, ABSENCE_COL_CANDIDATES, "absence_prob")
    seed_col = _resolve_column(raw, SEED_COL_CANDIDATES, "seed")
    active_crew_col = _resolve_column(raw, ACTIVE_CREW_COL_CANDIDATES, "active_crew")
    reserve_crew_col = _resolve_column(raw, RESERVE_CREW_COL_CANDIDATES, "reserve_crew")
 
    print(f"Loaded {len(raw)} rows from {EXCEL_PATH!r}")
    print(f"Resolved: Metric={metric_col!r}, Value={value_col!r}, "
          f"delay_prob={delay_col!r}, absence_prob={absence_col!r}, seed={seed_col!r}, "
          f"active_crew={active_crew_col!r}, reserve_crew={reserve_crew_col!r}\n")
 
    # Value may be stored as text (e.g. metrics.py writes str(x) into the
    # Value column) - coerce to numeric, dropping anything that fails.
    raw = raw.copy()
    raw[value_col] = pd.to_numeric(raw[value_col], errors="coerce")
    n_before = len(raw)
    raw = raw.dropna(subset=[value_col])
    if len(raw) < n_before:
        print(f"[WARN] Dropped {n_before - len(raw)} rows with non-numeric Value "
              f"(e.g. dict-valued rows like primary_uncovered_reasons, if present).\n")
 
    if FILTER:
        for key, val in FILTER.items():
            col = _resolve_column(raw, [key], key)
            raw = raw[raw[col] == val]
        print(f"Filtered to {FILTER} -> {len(raw)} rows remain\n")
 
    # ---- Pivot: one row per run, one column per metric name --------------
    run_id_cols = [active_crew_col, reserve_crew_col, absence_col, delay_col, seed_col]
    wide = raw.pivot_table(index=run_id_cols, columns=metric_col, values=value_col, aggfunc="first")
    wide = wide.reset_index()
    available_metrics = [c for c in wide.columns if c not in run_id_cols]
 
    resolved_outcomes = {}
    for key, candidates in OUTCOME_METRIC_CANDIDATES.items():
        try:
            resolved_outcomes[key] = _resolve_metric_name(available_metrics, candidates, key)
        except KeyError as e:
            print(f"[WARN] Skipping '{key}': {e}")
 
    if not resolved_outcomes:
        raise SystemExit("No outcome metrics could be resolved - check OUTCOME_METRIC_CANDIDATES "
                          "against the 'Available metric names' list printed above.")
 
    print(f"Pivoted to {len(wide)} runs (rows), resolved outcomes -> {resolved_outcomes}\n")
 
    # ---- 1. Pairwise Pearson correlations, raw per-seed rows -------------
    print("=" * 70)
    print("PEARSON CORRELATIONS (raw per-seed rows)")
    print("=" * 70)
    raw_rows = []
    for outcome_key, outcome_col in resolved_outcomes.items():
        raw_rows.append(pearson_row(wide, delay_col, outcome_col, f"delay_prob vs {outcome_key}"))
        raw_rows.append(pearson_row(wide, absence_col, outcome_col, f"absence_prob vs {outcome_key}"))
    raw_df = pd.DataFrame(raw_rows)
    raw_df["sig"] = raw_df["p_value"].apply(significance_stars)
    print(raw_df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
 
    # ---- 2. Same correlations on cell-means (averaged across seeds) -----
    cell_means = (
        wide.groupby([delay_col, absence_col])[list(resolved_outcomes.values())]
        .mean()
        .reset_index()
    )
    print(f"\n{'=' * 70}")
    print(f"PEARSON CORRELATIONS (cell means, n = {len(cell_means)} independent cells)")
    print("=" * 70)
    cell_rows = []
    for outcome_key, outcome_col in resolved_outcomes.items():
        cell_rows.append(pearson_row(cell_means, delay_col, outcome_col, f"delay_prob vs {outcome_key}"))
        cell_rows.append(pearson_row(cell_means, absence_col, outcome_col, f"absence_prob vs {outcome_key}"))
    cell_df = pd.DataFrame(cell_rows)
    cell_df["sig"] = cell_df["p_value"].apply(significance_stars)
    print(cell_df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
 
    # ---- 3. Interaction regression ----------------------------------------
    print(f"\n{'=' * 70}")
    print("OLS WITH INTERACTION TERM (tests additive vs. interacting effects)")
    print("=" * 70)
    ols_tables = {}
    for outcome_key, outcome_col in resolved_outcomes.items():
        reg = run_interaction_regression(wide, delay_col, absence_col, outcome_col)
        ols_tables[outcome_key] = reg
        print(f"\n--- {outcome_key} ~ delay_prob + absence_prob + interaction "
              f"(R^2 = {reg.attrs['r_squared']:.3f}, n = {reg.attrs['n']}) ---")
        reg_display = reg.copy()
        reg_display["sig"] = reg_display["p_value"].apply(significance_stars)
        print(reg_display.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
 
    # ---- 4. Quadratic term for duty_coverage vs absence_prob -------------
    # duty_coverage's linear R^2 (from the interaction regression above) is
    # typically noticeably lower than on_time_rate's/recovery_time's,
    # suggesting the relationship may curve rather than being purely linear -
    # e.g. from reserve-queue exhaustion at higher absence rates. This adds
    # an absence_prob^2 term (holding delay_prob's known non-relationship
    # aside) and reports whether it's a statistically and practically
    # meaningful improvement over the linear-only fit.
    quad_table = None
    if "duty_coverage" in resolved_outcomes:
        outcome_col = resolved_outcomes["duty_coverage"]
        linear_fit = run_linear_only_regression(wide, absence_col, outcome_col)
        quad_fit = run_quadratic_regression(wide, absence_col, outcome_col)
        quad_table = quad_fit
 
        quad_term_p = quad_fit["p_value"].iloc[-1]
        r2_linear = linear_fit.attrs["r_squared"]
        r2_quad = quad_fit.attrs["r_squared"]
        delta_r2 = r2_quad - r2_linear
 
        print(f"\n{'=' * 70}")
        print("QUADRATIC TERM: duty_coverage ~ absence_prob + absence_prob^2")
        print("=" * 70)
        print(f"Linear-only R^2:    {r2_linear:.4f}")
        print(f"With quadratic R^2: {r2_quad:.4f}  (delta R^2 = {delta_r2:+.4f})")
        print(f"Quadratic term p-value: {quad_term_p:.4f} ({significance_stars(quad_term_p)})")
        quad_display = quad_fit.copy()
        quad_display["sig"] = quad_display["p_value"].apply(significance_stars)
        print(quad_display.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
 
        if quad_term_p < 0.05 and delta_r2 > 0.02:
            print(
                "\n[FINDING] The quadratic term is statistically significant and "
                f"meaningfully improves fit (delta R^2 = {delta_r2:+.4f}). This supports "
                "describing duty coverage's relationship with absence probability as "
                "non-linear (e.g. consistent with reserve-queue exhaustion at higher "
                "absence rates) rather than purely linear."
            )
        elif quad_term_p < 0.05:
            print(
                "\n[CAUTION] The quadratic term is statistically significant but the R^2 "
                f"improvement is small (delta R^2 = {delta_r2:+.4f}). With n={quad_fit.attrs['n']}, "
                "this could still be statistically detectable without being a practically "
                "meaningful curvature - inspect the fitted curve before writing this up as "
                "a substantive non-linearity finding."
            )
        else:
            print(
                "\n[FINDING] The quadratic term is not statistically significant "
                f"(p={quad_term_p:.4f}). The lower linear R^2 for duty_coverage is "
                "more likely due to noise/other unmodelled factors than a curved "
                "absence_prob relationship - avoid claiming non-linearity here."
            )
    else:
        print("\n[INFO] 'duty_coverage' outcome not resolved - skipping quadratic check.")
 
    # ---- Save everything ---------------------------------------------------
    out_path = "correlation_results.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        raw_df.to_excel(writer, sheet_name="Pearson (raw rows)", index=False)
        cell_df.to_excel(writer, sheet_name="Pearson (cell means)", index=False)
        for outcome_key, reg in ols_tables.items():
            reg.to_excel(writer, sheet_name=f"OLS {outcome_key}"[:31], index=False)
        if quad_table is not None:
            quad_table.to_excel(writer, sheet_name="Quadratic duty_coverage", index=False)
    print(f"\nSaved full results to: {out_path}")
 
 
if __name__ == "__main__":
    main()
 