"""Optimize crew_assignment_fact from DFS tables and export locally (Excel) or on Databricks (Delta)."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pulp import LpBinary, LpMaximize, LpProblem, LpStatus, LpVariable, lpSum, value, PULP_CBC_CMD

try:
    from simulation.config import Config
except ImportError:
    try:
        from .config import Config
    except ImportError:
        from config import Config

# ============================================================
# ENVIRONMENT DETECTION
# ============================================================
# Databricks sets this env var automatically. Locally (e.g. VS Code) it's
# absent, so IS_DATABRICKS is False there.
IS_DATABRICKS = "DATABRICKS_RUNTIME_VERSION" in os.environ

try:
    from pyspark.sql import DataFrame as SparkDataFrame
    from pyspark.sql import SparkSession

    HAS_PYSPARK = True
except ImportError:
    SparkDataFrame = None  # type: ignore[assignment]
    SparkSession = None  # type: ignore[assignment]
    HAS_PYSPARK = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CREW_ASSIGNMENT_SHEET = "crew_assignment_fact"

REQUIRED_DFS = [
    "flight_schedule_fact",
    "duty_fact",
    "sectors_dim",
    "aircraft_dim",
    "crew_dim",
]

PROTECT_STANDBY_CREW = True  # pilots with standby status are never assigned duties
VERBOSE = True

# ============================================================
# CONFIG — attribute names are now identical on local and Databricks
# Config classes, so this just reads them straight through. The only
# thing that's still environment-dependent is whether `input_file`
# means anything: Databricks has no Excel input, it reads Delta tables.
# ============================================================


def _load_run_settings(cfg) -> dict:
    """Resolve run parameters from the (now-unified) Config."""
    return {
        "pilots_per_duty": int(cfg.pilots_per_duty),
        "min_rest_hours": int(cfg.min_rest_hours),
        "pre_duty_minutes": int(cfg.pre_duty_minutes),
        "post_duty_minutes": int(cfg.post_duty_minutes),
        "max_duty_mins_28d": int(cfg.max_duty_hours_28d * 60),
        "rolling_window_days": int(cfg.rolling_window_days),
        "max_work_days_in_window": int(cfg.max_work_days_in_window),
        "input_file": getattr(cfg, "input_file", None) if not IS_DATABRICKS else None,
    }


_DEFAULT_SETTINGS = _load_run_settings(Config())

PILOTS_PER_DUTY = _DEFAULT_SETTINGS["pilots_per_duty"]
MIN_REST_HOURS = _DEFAULT_SETTINGS["min_rest_hours"]
PRE_DUTY_TIME = _DEFAULT_SETTINGS["pre_duty_minutes"]
POST_DUTY_TIME = _DEFAULT_SETTINGS["post_duty_minutes"]
MAX_DUTY_MINS_28D = _DEFAULT_SETTINGS["max_duty_mins_28d"]
ROLLING_WINDOW_DAYS = _DEFAULT_SETTINGS["rolling_window_days"]
MAX_WORK_DAYS_PER_WINDOW = _DEFAULT_SETTINGS["max_work_days_in_window"]

INPUT_EXCEL_PATH = Path(_DEFAULT_SETTINGS["input_file"]) if _DEFAULT_SETTINGS["input_file"] else None
OUTPUT_EXCEL_PATH = INPUT_EXCEL_PATH

# Export switches — auto-configured per environment, override if needed.
EXPORT_TO_EXCEL = not IS_DATABRICKS and INPUT_EXCEL_PATH is not None
EXPORT_TO_DELTA = IS_DATABRICKS and HAS_PYSPARK
DELTA_CATALOG = "ctl-sandbox-dev"
DELTA_SCHEMA = "simulation_paper"


# ============================================================
# HELPERS
# ============================================================


def _to_string(series: pd.Series) -> pd.Series:
    """Cast series to stripped string type."""
    return series.astype("string").str.strip()


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def _duration_minutes(start_ts: pd.Series, end_ts: pd.Series) -> pd.Series:
    duration = (end_ts - start_ts).dt.total_seconds() / 60
    return _to_int(duration)


def load_dfs_tables(input_path: Path) -> dict[str, pd.DataFrame]:
    """Load required DFs exported by flight_data_generator (local/Excel only)."""
    workbook = pd.ExcelFile(input_path)
    missing = [sheet for sheet in REQUIRED_DFS if sheet not in workbook.sheet_names]
    if missing:
        raise ValueError(f"Missing required DFs in {input_path}: {missing}")

    return {sheet: pd.read_excel(input_path, sheet_name=sheet) for sheet in REQUIRED_DFS}


def _validate_dfs_tables(dfs_tables: dict[str, pd.DataFrame]) -> None:
    """Validate that in-memory DFs include required tables."""
    missing = [sheet for sheet in REQUIRED_DFS if sheet not in dfs_tables]
    if missing:
        raise ValueError(f"Missing required DFs in memory: {missing}")


def _to_pandas(df):
    """Convert a Spark DataFrame to pandas; pass pandas DataFrames through untouched."""
    if isinstance(df, pd.DataFrame):
        return df
    if HAS_PYSPARK and isinstance(df, SparkDataFrame):
        return df.toPandas()
    if hasattr(df, "toPandas"):
        # Duck-typed fallback in case SparkDataFrame import path differs.
        return df.toPandas()
    return df


def prepare_duty_fact(duty_fact: pd.DataFrame) -> pd.DataFrame:
    """Normalize duty timestamps and derive duration columns for output."""
    duty_df = duty_fact.copy()
    duty_df["duty_id"] = _to_string(duty_df["duty_id"])

    for col in (
        "duty_skd_start_ts",
        "duty_skd_end_ts",
        "duty_est_start_ts",
        "duty_est_end_ts",
        "duty_act_start_ts",
        "duty_act_end_ts",
    ):
        duty_df[col] = pd.to_datetime(duty_df[col], errors="coerce")

    duty_df["duty_skd_duration_min"] = _duration_minutes(duty_df["duty_skd_start_ts"], duty_df["duty_skd_end_ts"])
    duty_df["duty_est_duration_min"] = _duration_minutes(duty_df["duty_est_start_ts"], duty_df["duty_est_end_ts"])
    duty_df["duty_act_duration_min"] = _duration_minutes(duty_df["duty_act_start_ts"], duty_df["duty_act_end_ts"])

    return duty_df


def find_incompatible_duty_pairs(
    duty_df: pd.DataFrame,
    min_rest_hours: int,
    pre_duty_minutes: int,
    post_duty_minutes: int,
) -> list[tuple[str, str]]:
    """Find duty pairs that violate buffered minimum rest if assigned to the same crew."""
    conflicts: list[tuple[str, str]] = []
    ordered = duty_df.sort_values(["duty_skd_start_ts", "duty_id"]).to_dict("records")

    pre_buffer = timedelta(minutes=pre_duty_minutes)
    post_buffer = timedelta(minutes=post_duty_minutes)

    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            buffered_prev_end = first["duty_skd_end_ts"] + post_buffer
            buffered_next_start = second["duty_skd_start_ts"] - pre_buffer
            rest_hours = (buffered_next_start - buffered_prev_end).total_seconds() / 3600
            if rest_hours < min_rest_hours:
                conflicts.append((first["duty_id"], second["duty_id"]))

    return conflicts


def build_rolling_workday_windows(
    duty_df: pd.DataFrame,
    window_days: int = ROLLING_WINDOW_DAYS,
) -> list[tuple[pd.Timestamp, list[str]]]:
    """Build rolling `window_days`-day windows over duty start dates. Config field: rolling_window_days."""
    duty_days = (
        duty_df[["duty_id", "duty_skd_start_ts"]]
        .assign(duty_day=lambda frame: frame["duty_skd_start_ts"].dt.normalize())
        [["duty_id", "duty_day"]]
    )

    if duty_days.empty:
        return []

    min_day = duty_days["duty_day"].min()
    max_day = duty_days["duty_day"].max()

    windows: list[tuple[pd.Timestamp, list[str]]] = []
    current_start = min_day
    while current_start <= max_day:
        current_end = current_start + pd.Timedelta(days=window_days)

        window_duties = duty_days[
            (duty_days["duty_day"] >= current_start) & (duty_days["duty_day"] < current_end)
        ]["duty_id"].tolist()

        if window_duties:
            windows.append((current_start, window_duties))

        current_start += pd.Timedelta(days=1)

    return windows


def solve_max_coverage_assignments(
    duty_df: pd.DataFrame,
    crew_df: pd.DataFrame,
    pilots_per_duty: int = PILOTS_PER_DUTY,
    min_rest_hours: int = MIN_REST_HOURS,
    max_duty_mins_28d: int = MAX_DUTY_MINS_28D,
    rolling_window_days: int = ROLLING_WINDOW_DAYS,
    max_work_days_in_window: int = MAX_WORK_DAYS_PER_WINDOW,
    protect_standby_crew: bool = PROTECT_STANDBY_CREW,
) -> pd.DataFrame:
    """Solve ILP that maximizes covered duties."""
    duty_ids = duty_df["duty_id"].tolist()

    duty_days_by_id = duty_df.set_index("duty_id")["duty_skd_start_ts"].dt.normalize()
    if duty_days_by_id.isna().any():
        raise ValueError("All duties must have valid duty_skd_start_ts for rolling-day constraints.")

    duty_days = sorted(duty_days_by_id.unique())

    crew = crew_df.copy()
    crew["crew_id"] = _to_string(crew["crew_id"])
    crew["status"] = _to_string(crew["status"])
    crew_ids = crew["crew_id"].tolist()

    standby_ids = []
    if protect_standby_crew and "status" in crew.columns:
        standby_ids = crew[crew["status"].str.lower() == "standby"]["crew_id"].tolist()

    conflicts = list(set(find_incompatible_duty_pairs(duty_df, min_rest_hours, PRE_DUTY_TIME, POST_DUTY_TIME)))
    rolling_windows = build_rolling_workday_windows(duty_df, window_days=rolling_window_days)

    # --------------------------------------------------
    # FTL DUTY MINUTES
    # Duty duration used for regulatory constraints, including
    # briefing and debriefing buffers.
    # --------------------------------------------------
    duty_buffer_min = PRE_DUTY_TIME + POST_DUTY_TIME
    duty_minutes = {
        duty_id: int(minutes)
        for duty_id, minutes in (
            duty_df.assign(duty_ftl_duration_min=duty_df["duty_skd_duration_min"] + duty_buffer_min)
            .set_index("duty_id")["duty_ftl_duration_min"]
            .to_dict()
            .items()
        )
    }

    duty_dates = duty_df.set_index("duty_id")["duty_skd_start_ts"].dt.normalize()

    if VERBOSE:
        print(f"Generated {len(conflicts)} unique rest conflicts")
        print(f"Generated {len(rolling_windows)} rolling {rolling_window_days}-day work windows")
        print(f"Number of active crew: {len(crew_ids) - len(standby_ids)}")
        print(f"Number of standby crew: {len(standby_ids)}")

    problem = LpProblem("CrewAssignmentDFSMaxCoverage", LpMaximize)

    x = {(c, d): LpVariable(f"x_{c}_{d}", cat=LpBinary) for c in crew_ids for d in duty_ids}
    covered = {d: LpVariable(f"covered_{d}", cat=LpBinary) for d in duty_ids}

    problem += lpSum(covered[d] for d in duty_ids), "MaximizeCoveredDuties"

    constraint_counter = 0

    # Coverage constraints
    for d in duty_ids:
        problem += (
            lpSum(x[c, d] for c in crew_ids) == pilots_per_duty * covered[d]
        ), f"Cover_{d}_{constraint_counter}"
        constraint_counter += 1

    # Rest constraints
    for d1, d2 in conflicts:
        for c in crew_ids:
            problem += (x[c, d1] + x[c, d2] <= 1), f"Rest_{c}_{d1}_{d2}_{constraint_counter}"
            constraint_counter += 1

    # 28-day duty time limit
    for c in crew_ids:
        problem += (
            lpSum(duty_minutes[d] * x[c, d] for d in duty_ids) <= max_duty_mins_28d
        ), f"FTL28_DutyTime_{c}"
        constraint_counter += 1

    # Rolling work/rest window
    for c in crew_ids:
        for window_start, window_duties in rolling_windows:
            problem += (
                lpSum(x[c, d] for d in window_duties) <= max_work_days_in_window
            ), f"RollingWork_{c}_{window_start.date()}_{constraint_counter}"
            constraint_counter += 1

    # Standby constraints
    for c in standby_ids:
        for d in duty_ids:
            problem += (x[c, d] == 0), f"Standby_{c}_{d}_{constraint_counter}"
            constraint_counter += 1

    if VERBOSE:
        print("Solving ILP for optimize_roster...")

    problem.solve(PULP_CBC_CMD(msg=False))

    if VERBOSE:
        print(f"Status: {LpStatus[problem.status]}")

    if problem.status != 1:
        raise RuntimeError("No feasible optimization solution found.")

    durations = duty_df.set_index("duty_id")[
        ["duty_skd_duration_min", "duty_est_duration_min", "duty_act_duration_min"]
    ]

    records = []
    assignment_counter = 1
    for d in duty_ids:
        for c in crew_ids:
            if value(x[c, d]) > 0.5:
                row = durations.loc[d]
                records.append(
                    {
                        "assignment_id": f"ASGOPT{assignment_counter:06d}",
                        "crew_id": c,
                        "duty_id": d,
                        "duty_skd_duration_min": row["duty_skd_duration_min"],
                        "duty_est_duration_min": row["duty_est_duration_min"],
                        "duty_act_duration_min": row["duty_act_duration_min"],
                    }
                )
                assignment_counter += 1

    covered_count = sum(1 for d in duty_ids if value(covered[d]) > 0.5)
    coverage_pct = 100 * covered_count / len(duty_ids) if duty_ids else 0

    if VERBOSE:
        print(f"Covered duties: {covered_count}/{len(duty_ids)} ({coverage_pct:.1f}%)")

        crew_duty_hours = {}
        crew_assigned_duties = {}
        for c in crew_ids:
            assigned_duties = [d for d in duty_ids if value(x[c, d]) > 0.5]
            crew_assigned_duties[c] = assigned_duties
            crew_duty_hours[c] = sum(duty_minutes[d] * value(x[c, d]) for d in duty_ids) / 60

        hours_values = list(crew_duty_hours.values())
        print(f"Duty-hour limit: {max_duty_mins_28d / 60:.1f}h")
        print(f"Min crew duty hours: {min(hours_values):.1f}")
        print(f"Avg crew duty hours: {sum(hours_values) / len(hours_values):.1f}")
        print(f"Max crew duty hours: {max(hours_values):.1f}")

        print("Crew duty-date pattern:")
        for c in crew_ids:
            assigned_dates = {duty_dates.loc[d] for d in crew_assigned_duties[c] if d in duty_dates.index}
            window_sequences = []
            if assigned_dates:
                current_start = min(assigned_dates)
                last_date = max(assigned_dates)
                while current_start <= last_date:
                    sequence = "".join(
                        "W" if (current_start + pd.Timedelta(days=offset)) in assigned_dates else "O"
                        for offset in range(rolling_window_days)
                    )
                    window_sequences.append(f"{current_start:%Y-%m-%d}:{sequence}")
                    current_start += pd.Timedelta(days=1)
            window_text = ", ".join(window_sequences) if window_sequences else "none"
            print(f"  {c}: {rolling_window_days}-day sequences=[{window_text}]")

    result = pd.DataFrame(records)
    if result.empty:
        return pd.DataFrame(
            columns=[
                "assignment_id",
                "crew_id",
                "duty_id",
                "duty_skd_duration_min",
                "duty_est_duration_min",
                "duty_act_duration_min",
            ]
        )

    result["assignment_id"] = _to_string(result["assignment_id"])
    result["crew_id"] = _to_string(result["crew_id"])
    result["duty_id"] = _to_string(result["duty_id"])
    result["duty_skd_duration_min"] = _to_int(result["duty_skd_duration_min"])
    result["duty_est_duration_min"] = _to_int(result["duty_est_duration_min"])
    result["duty_act_duration_min"] = _to_int(result["duty_act_duration_min"])

    return result[
        [
            "assignment_id",
            "crew_id",
            "duty_id",
            "duty_skd_duration_min",
            "duty_est_duration_min",
            "duty_act_duration_min",
        ]
    ]


def export_crew_assignment_fact(output_path: Path, crew_assignment_fact: pd.DataFrame) -> Path:
    """Add or replace crew_assignment_fact sheet in the airline inputs workbook."""
    if output_path.exists():
        with pd.ExcelWriter(output_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            crew_assignment_fact.to_excel(writer, sheet_name=CREW_ASSIGNMENT_SHEET, index=False)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            crew_assignment_fact.to_excel(writer, sheet_name=CREW_ASSIGNMENT_SHEET, index=False)

    return output_path


def save_crew_assignment_fact_to_delta(
    crew_assignment_fact: pd.DataFrame,
    catalog: str = DELTA_CATALOG,
    schema: str = DELTA_SCHEMA,
    table_name: str = CREW_ASSIGNMENT_SHEET,
) -> None:
    """Overwrite the crew_assignment_fact Delta table with optimized assignments."""
    if not HAS_PYSPARK:
        raise RuntimeError(
            "save_crew_assignment_fact_to_delta() requires pyspark, which isn't available in "
            "this environment. Set EXPORT_TO_DELTA = False when running locally, or run this "
            "on a Databricks cluster."
        )

    spark = SparkSession.getActiveSession()
    if spark is None:
        spark = SparkSession.builder.getOrCreate()

    target_table = f"`{catalog}`.{schema}.{table_name}"
    sdf = spark.createDataFrame(crew_assignment_fact)
    (
        sdf.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(target_table)
    )

    if VERBOSE:
        print(f"Written {sdf.count():,} rows to {target_table}")


def optimize_roster_max_coverage(
    config=None,
    dfs_tables: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Run ILP max-coverage optimization and export crew_assignment_fact.

    - `config`: overrides the module-level defaults for this run (any object
      exposing the same attributes as `Config`).
    - `dfs_tables`: in-memory tables (pandas or Spark DataFrames) to use
      instead of loading from Excel. Required on Databricks, since there's
      no `input_file` to load from there.
    """
    settings = _load_run_settings(config) if config is not None else _DEFAULT_SETTINGS

    input_path = Path(settings["input_file"]) if settings["input_file"] else None
    if input_path is not None and not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path
    output_path = input_path

    if dfs_tables is None:
        if input_path is None:
            raise ValueError(
                "No dfs_tables provided and no input_file configured to load from "
                "(expected on Databricks — pass dfs_tables directly from Delta tables)."
            )
        tables = load_dfs_tables(input_path)
    else:
        tables = {name: _to_pandas(df) for name, df in dfs_tables.items()}
        _validate_dfs_tables(tables)

    duty_df = prepare_duty_fact(tables["duty_fact"])

    crew_assignment_fact = solve_max_coverage_assignments(
        duty_df=duty_df,
        crew_df=tables["crew_dim"],
        pilots_per_duty=settings["pilots_per_duty"],
        min_rest_hours=settings["min_rest_hours"],
        max_duty_mins_28d=settings["max_duty_mins_28d"],
        rolling_window_days=settings["rolling_window_days"],
        max_work_days_in_window=settings["max_work_days_in_window"],
        protect_standby_crew=PROTECT_STANDBY_CREW,
    )

    if dfs_tables is not None:
        # Keep the in-memory dict up to date for callers chaining further steps.
        dfs_tables["crew_assignment_fact"] = crew_assignment_fact

    if EXPORT_TO_EXCEL and output_path is not None:
        export_path = export_crew_assignment_fact(output_path, crew_assignment_fact)
        print(f"Exported sheet '{CREW_ASSIGNMENT_SHEET}' to {export_path}")

    if EXPORT_TO_DELTA:
        save_crew_assignment_fact_to_delta(crew_assignment_fact)
        print(f"Exported '{CREW_ASSIGNMENT_SHEET}' to Delta table {DELTA_CATALOG}.{DELTA_SCHEMA}")

    print(f"Generated {len(crew_assignment_fact)} optimized crew assignments")

    return crew_assignment_fact


def optimize_roster(dfs_tables: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Default optimization entry point (works with or without in-memory tables)."""
    return optimize_roster_max_coverage(dfs_tables=dfs_tables)


def main() -> None:
    optimize_roster()


if __name__ == "__main__":
    main()