"""Optimize crew_assignment_fact from DFs and export to airline inputs.xlsx."""

from pathlib import Path
from datetime import timedelta

import pandas as pd
from pulp import LpBinary, LpMaximize, LpProblem, LpStatus, LpVariable, lpSum, value, PULP_CBC_CMD
try:
    from simulation.config import Config
except ImportError:
    from config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_EXCEL_PATH = Path(Config.input_file)
OUTPUT_EXCEL_PATH = INPUT_EXCEL_PATH
CREW_ASSIGNMENT_SHEET = "crew_assignment_fact"
MAX_DUTY_MINS_28D = int(Config.max_duty_hours_28D * 60)
PILOTS_PER_DUTY = Config.pilots_per_duty
MIN_REST_HOURS = Config.min_rest_hours
PRE_DUTY_TIME = Config.pre_duty_minutes
POST_DUTY_TIME = Config.post_duty_minutes
ROLLING_WINDOW_DAYS = Config.rolling_window_days
MAX_WORK_DAYS_PER_WINDOW = Config.max_work_days_in_window

PROTECT_STANDBY_CREW = True
VERBOSE = True

REQUIRED_DFs = [
    "flight_schedule_fact",
    "duty_fact",
    "sectors_dim",
    "aircraft_dim",
    "crew_dim",
]


def _to_string(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def _duration_minutes(start_ts: pd.Series, end_ts: pd.Series) -> pd.Series:
    duration = (end_ts - start_ts).dt.total_seconds() / 60
    return _to_int(duration)


def load_dfs_tables(input_path: Path) -> dict[str, pd.DataFrame]:
    """Load required DFs exported by flight_data_generator."""

    workbook = pd.ExcelFile(input_path)
    missing = [sheet for sheet in REQUIRED_DFs if sheet not in workbook.sheet_names]
    if missing:
        raise ValueError(f"Missing required DFs in {input_path}: {missing}")

    return {sheet: pd.read_excel(input_path, sheet_name=sheet) for sheet in REQUIRED_DFs}


def _validate_dfs_tables(dfs_tables: dict[str, pd.DataFrame]) -> None:
    """Validate that in-memory DFs include required tables."""

    missing = [sheet for sheet in REQUIRED_DFs if sheet not in dfs_tables]
    if missing:
        raise ValueError(f"Missing required DFs in memory: {missing}")


def prepare_duty_fact(duty_fact: pd.DataFrame) -> pd.DataFrame:
    """Normalize duty timestamps and derive duration columns for output."""

    duty_df = duty_fact.copy()
    duty_df["duty_id"] = _to_string(duty_df["duty_id"])
    duty_df["duty_skd_start_ts"] = pd.to_datetime(duty_df["duty_skd_start_ts"], errors="coerce")
    duty_df["duty_skd_end_ts"] = pd.to_datetime(duty_df["duty_skd_end_ts"], errors="coerce")
    duty_df["duty_est_start_ts"] = pd.to_datetime(duty_df["duty_est_start_ts"], errors="coerce")
    duty_df["duty_est_end_ts"] = pd.to_datetime(duty_df["duty_est_end_ts"], errors="coerce")
    duty_df["duty_act_start_ts"] = pd.to_datetime(duty_df["duty_act_start_ts"], errors="coerce")
    duty_df["duty_act_end_ts"] = pd.to_datetime(duty_df["duty_act_end_ts"], errors="coerce")

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
        for second in ordered[i + 1 :]:
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
    """Build 9-day rolling windows over duty start dates."""

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
            (duty_days["duty_day"] >= current_start)
            & (duty_days["duty_day"] < current_end)
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

    duty_days_by_id = (
        duty_df
        .set_index("duty_id")["duty_skd_start_ts"]
        .dt.normalize()
    )

    if duty_days_by_id.isna().any():
        raise ValueError(
            "All duties must have valid duty_skd_start_ts for rolling-day constraints."
        )

    duty_days = sorted(duty_days_by_id.unique())

    duties_by_day: dict[pd.Timestamp, list[str]] = {}
    for duty_id in duty_ids:
        day = duty_days_by_id.loc[duty_id]
        duties_by_day.setdefault(day, []).append(duty_id)

    crew = crew_df.copy()

    crew["crew_id"] = _to_string(
        crew["crew_id"]
    )

    crew["status"] = _to_string(
        crew["status"]
    )

    crew_ids = crew["crew_id"].tolist()

    standby_ids = []

    if protect_standby_crew and "status" in crew.columns:
        standby_ids = crew[
            crew["status"].str.lower() == "standby"
        ]["crew_id"].tolist()

    conflicts = find_incompatible_duty_pairs(
        duty_df,
        min_rest_hours,
        PRE_DUTY_TIME,
        POST_DUTY_TIME,
    )

    rolling_windows = build_rolling_workday_windows(
        duty_df,
        window_days=rolling_window_days,
    )

    # Remove duplicate conflict pairs
    conflicts = list(set(conflicts))

    # --------------------------------------------------
    # FTL DUTY MINUTES
    # Duty duration used for regulatory constraints
    # Includes briefing and debriefing buffers
    # --------------------------------------------------

    DUTY_BUFFER_MIN = (
        PRE_DUTY_TIME
        + POST_DUTY_TIME
    )

    duty_minutes = {
        duty_id: int(minutes)
        for duty_id, minutes in (
            duty_df
            .assign(
                duty_ftl_duration_min=
                    duty_df["duty_skd_duration_min"]
                    + DUTY_BUFFER_MIN
            )
            .set_index("duty_id")
            ["duty_ftl_duration_min"]
            .to_dict()
            .items()
        )
    }

    duty_dates = duty_df.set_index("duty_id")[
        "duty_skd_start_ts"
    ].dt.normalize()

    if VERBOSE:
        print(
            f"Generated {len(conflicts)} unique rest conflicts"
        )
        print(
            f"Generated {len(rolling_windows)} rolling {rolling_window_days}-day work windows"
        )
        print(
            f"Number of active crew: {len(crew_ids) - len(standby_ids)}")
        
        print(
            f"Number of standby crew: {len(standby_ids)} ")



    problem = LpProblem(
        "CrewAssignmentDFSMaxCoverage",
        LpMaximize,
    )
    
    x = {
        (c, d): LpVariable(
            f"x_{c}_{d}",
            cat=LpBinary,
        )
        for c in crew_ids
        for d in duty_ids
    }

    covered = {
        d: LpVariable(
            f"covered_{d}",
            cat=LpBinary,
        )
        for d in duty_ids
    }

    worked_day = {
        (c, day): LpVariable(
            f"worked_{c}_{day.strftime('%Y%m%d')}",
            cat=LpBinary,
        )
        for c in crew_ids
        for day in duty_days
    }

    problem += lpSum(
        covered[d]
        for d in duty_ids
    ), "MaximizeCoveredDuties"

    # --------------------------------------------------
    # UNIQUE CONSTRAINT COUNTER
    # --------------------------------------------------
    constraint_counter = 0

    # Coverage constraints
    for d in duty_ids:

        problem += (
            lpSum(
                x[c, d]
                for c in crew_ids
            )
            == pilots_per_duty * covered[d]
        ), f"Cover_{d}_{constraint_counter}"

        constraint_counter += 1

    # Rest constraints
    for d1, d2 in conflicts:

        for c in crew_ids:

            problem += (
                x[c, d1] + x[c, d2] <= 1
            ), (
                f"Rest_{c}_{d1}_{d2}_{constraint_counter}"
            )
            constraint_counter += 1

    # 28 day duty time limit
    for c in crew_ids:

        problem += (
            lpSum(
                duty_minutes[d] * x[c, d]
                for d in duty_ids
            )
            <= max_duty_mins_28d
        ), f"FTL28_DutyTime_{c}"

        constraint_counter += 1

    # Rolling 9 day work/rest window
    for c in crew_ids:
        for window_start, window_duties in rolling_windows:
            problem += (
                lpSum(
                    x[c, d]
                    for d in window_duties
                ) <= max_work_days_in_window
            ), (
                f"RollingWork_{c}_{window_start.date()}_{constraint_counter}"
            )
            constraint_counter += 1

    # Standby constraints
    for c in standby_ids:

        for d in duty_ids:

            problem += (
                x[c, d] == 0
            ), (
                f"Standby_{c}_{d}_{constraint_counter}"
            )

            constraint_counter += 1

    if VERBOSE:
        print("Solving ILP for optimize_roster...")

    problem.solve(PULP_CBC_CMD(msg=False))

    if VERBOSE:
        print(
            f"Status: {LpStatus[problem.status]}"
        )

    if problem.status != 1:
        raise RuntimeError(
            "No feasible optimization solution found."
        )

    durations = duty_df.set_index(
        "duty_id"
    )[
        [
            "duty_skd_duration_min",
            "duty_est_duration_min",
            "duty_act_duration_min",
        ]
    ]

    records = []
    assignment_counter = 1

    for d in duty_ids:

        for c in crew_ids:

            if value(x[c, d]) > 0.5:

                row = durations.loc[d]

                records.append(
                    {
                        "assignment_id":
                            f"ASGOPT{assignment_counter:06d}",
                        "crew_id": c,
                        "duty_id": d,
                        "duty_skd_duration_min":
                            row["duty_skd_duration_min"],
                        "duty_est_duration_min":
                            row["duty_est_duration_min"],
                        "duty_act_duration_min":
                            row["duty_act_duration_min"],
                    }
                )

                assignment_counter += 1

    covered_count = sum(
        1
        for d in duty_ids
        if value(covered[d]) > 0.5
    )

    coverage_pct = (
        100 * covered_count / len(duty_ids)
        if duty_ids
        else 0
    )

    if VERBOSE:
        print(
            f"Covered duties: {covered_count}/{len(duty_ids)} "
            f"({coverage_pct:.1f}%)"
        )
    
    crew_duty_hours = {}
    crew_assigned_duties = {}

    for c in crew_ids:
        assigned_duties = [
            d
            for d in duty_ids
            if value(x[c, d]) > 0.5
        ]

        crew_assigned_duties[c] = assigned_duties

        total_hours = (
            sum(
                duty_minutes[d] * value(x[c, d])
                for d in duty_ids
            )
            / 60
        )

        crew_duty_hours[c] = total_hours

    if VERBOSE:

        values = list(
            crew_duty_hours.values()
        )

        print(
            f"Duty-hour limit: "
            f"{MAX_DUTY_MINS_28D / 60:.1f}h"
        )

        print(
            f"Min crew duty hours: "
            f"{min(values):.1f}"
        )

        print(
            f"Avg crew duty hours: "
            f"{sum(values)/len(values):.1f}"
        )

        print(
            f"Max crew duty hours: "
            f"{max(values):.1f}"
        )

        # Crew duty-date pattern for each crew member

        print("Crew duty-date pattern:")

        for c in crew_ids:
            assigned_dates = {
                duty_dates.loc[d]
                for d in crew_assigned_duties[c]
                if d in duty_dates.index
            }

            window_sequences = []

            if assigned_dates:
                current_start = min(assigned_dates)
                last_date = max(assigned_dates)

                while current_start <= last_date:
                    sequence = "".join(
                        "W"
                        if (current_start + pd.Timedelta(days=offset)) in assigned_dates
                        else "O"
                        for offset in range(ROLLING_WINDOW_DAYS)
                    )
                    window_sequences.append(
                        f"{current_start:%Y-%m-%d}:{sequence}"
                    )
                    current_start += pd.Timedelta(days=1)

            window_text = ", ".join(window_sequences) if window_sequences else "none"

            # print(
            #     f"  {c}: 9-day sequences=[{window_text}]"
            # )


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

    result["assignment_id"] = _to_string(
        result["assignment_id"]
    )

    result["crew_id"] = _to_string(
        result["crew_id"]
    )

    result["duty_id"] = _to_string(
        result["duty_id"]
    )

    result["duty_skd_duration_min"] = _to_int(
        result["duty_skd_duration_min"]
    )

    result["duty_est_duration_min"] = _to_int(
        result["duty_est_duration_min"]
    )

    result["duty_act_duration_min"] = _to_int(
        result["duty_act_duration_min"]
    )

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
    """Add or replace crew_assignment_fact sheet in airline inputs workbook."""

    if output_path.exists():
        with pd.ExcelWriter(output_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            crew_assignment_fact.to_excel(writer, sheet_name=CREW_ASSIGNMENT_SHEET, index=False)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            crew_assignment_fact.to_excel(writer, sheet_name=CREW_ASSIGNMENT_SHEET, index=False)

    return output_path


def optimize_roster_max_coverage(
    config=None,
    dfs_tables: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Run ILP max-coverage optimization and export crew_assignment_fact."""

    input_path = INPUT_EXCEL_PATH
    output_path = OUTPUT_EXCEL_PATH
    pilots_per_duty = PILOTS_PER_DUTY
    min_rest_hours = MIN_REST_HOURS
    protect_standby_crew = PROTECT_STANDBY_CREW
    max_duty_mins_28d = MAX_DUTY_MINS_28D
    rolling_window_days = ROLLING_WINDOW_DAYS
    max_work_days_in_window = MAX_WORK_DAYS_PER_WINDOW

    if config is not None and hasattr(config, "input_file"):
        configured_input = Path(config.input_file)
        if not configured_input.is_absolute():
            configured_input = PROJECT_ROOT / configured_input
        input_path = configured_input
        output_path = configured_input

    if config is not None and hasattr(config, "pilots_per_duty"):
        pilots_per_duty = int(config.pilots_per_duty)
    if config is not None and hasattr(config, "min_rest_hours"):
        min_rest_hours = int(config.min_rest_hours)
    if config is not None and hasattr(config, "max_duty_hours_28D"):
        max_duty_mins_28d = int(config.max_duty_hours_28D * 60)
    if config is not None and hasattr(config, "rolling_window_days"):
        rolling_window_days = int(config.rolling_window_days)
    if config is not None and hasattr(config, "max_work_days_in_window"):
        max_work_days_in_window = int(config.max_work_days_in_window)

    if dfs_tables is None:
        tables = load_dfs_tables(input_path)
    else:
        _validate_dfs_tables(dfs_tables)
        tables = dfs_tables

    duty_df = prepare_duty_fact(tables["duty_fact"])

    crew_assignment_fact = solve_max_coverage_assignments(
        duty_df=duty_df,
        crew_df=tables["crew_dim"],
        pilots_per_duty=pilots_per_duty,
        min_rest_hours=min_rest_hours,
        max_duty_mins_28d=max_duty_mins_28d,
        rolling_window_days=rolling_window_days,
        max_work_days_in_window=max_work_days_in_window,
        protect_standby_crew=protect_standby_crew,
    )

    export_path = export_crew_assignment_fact(output_path, crew_assignment_fact)
    print(f"Generated {len(crew_assignment_fact)} optimized crew assignments")
    print(f"Exported sheet '{CREW_ASSIGNMENT_SHEET}' to {export_path}")

    return crew_assignment_fact


def optimize_roster() -> pd.DataFrame:
    """Default optimization entry point."""
    return optimize_roster_max_coverage()


def main() -> None:
    optimize_roster()


if __name__ == "__main__":
    main()
