"""
Validation of roster_df against simulation constraints.

Checks:
- MAX_DUTY_HOURS (12): No crew duty period exceeds 12 hours
  (duty period = pre_duty_minutes before first flight to post_duty_minutes after last flight)
- MAX_DUTY_HOURS_7D (60): No crew exceeds 60 hours of duty in any 7 consecutive days
- MAX_DUTY_HOURS_14D (110): No crew exceeds 110 hours of duty in any 14 consecutive days
- MAX_DUTY_HOURS_28D (190): No crew exceeds 190 hours of duty in any 28 consecutive days
- MIN_REST_HOURS (10): Minimum 10 hours rest between consecutive duty periods
- MIN_CONNECTION_MINUTES (45): Minimum 45 minutes between consecutive flights in a duty
"""

import sys
import pandas as pd
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from simulation.config import Config

# =====================================================
# Configuration (must match simulation/config.py)
# =====================================================

MAX_DUTY_HOURS = Config.max_duty_hours
MIN_REST_HOURS = Config.min_rest_hours
MIN_CONNECTION_MINUTES = Config.min_connection_minutes
MAX_DUTY_HOURS_7D = Config.max_duty_hours_7d
MAX_DUTY_HOURS_14D = Config.max_duty_hours_14d
MAX_DUTY_HOURS_28D = Config.max_duty_hours_28d
PRE_DUTY_MINUTES = Config.pre_duty_minutes
POST_DUTY_MINUTES = Config.post_duty_minutes

# =====================================================
# Validation Functions
# =====================================================

def _collect_rolling_window_hours(
    duty_windows: pd.DataFrame,
    window_days: int,
) -> list[float]:
    """Return duty-hour totals for all crew/day rolling windows."""

    window_hours: list[float] = []

    for _, crew_duties in duty_windows.groupby("Crew ID"):

        crew_duties = (
            crew_duties.sort_values("duty_date")
        )

        for day in crew_duties["duty_date"].unique():

            end_day = day + timedelta(days=window_days)

            hours = crew_duties[
                (crew_duties["duty_date"] >= day)
                &
                (crew_duties["duty_date"] < end_day)
            ]["duty_hours"].sum()

            window_hours.append(float(hours))

    return window_hours


def _rolling_window_hour_stats(
    duty_windows: pd.DataFrame,
    window_days: int,
) -> tuple[float, float, float]:
    """Return min/average/max duty hours across rolling windows."""

    window_hours = _collect_rolling_window_hours(
        duty_windows,
        window_days,
    )

    if not window_hours:
        return 0.0, 0.0, 0.0

    return (
        min(window_hours),
        sum(window_hours) / len(window_hours),
        max(window_hours),
    )

def validate_roster(roster_df):
    """
    Run all constraint checks on the final roster.
    """

    print("=" * 60)
    print("ROSTER CONSTRAINT VALIDATION")
    print("=" * 60)

    assigned = roster_df[
        roster_df["Assignment Status"] == "Assigned"
    ].copy()

    assigned["Actual Departure datetime"] = pd.to_datetime(
        assigned["Actual Departure datetime"]
    )

    assigned["Actual Arrival datetime"] = pd.to_datetime(
        assigned["Actual Arrival datetime"]
    )

    violations_found = False

    pre_buffer = timedelta(
        minutes=PRE_DUTY_MINUTES
    )

    post_buffer = timedelta(
        minutes=POST_DUTY_MINUTES
    )

    # --------------------------------------------------
    # CHECK 1: MAX DUTY HOURS
    # --------------------------------------------------

    print(
        f"\n[CHECK 1] MAX_DUTY_HOURS <= {MAX_DUTY_HOURS}h "
        f"(incl. {PRE_DUTY_MINUTES}min pre + "
        f"{POST_DUTY_MINUTES}min post)"
    )

    print("-" * 40)

    duty_windows = (
        assigned.groupby(
            ["Crew ID", "Duty ID"]
        )
        .agg(
            duty_start=(
                "Actual Departure datetime",
                "min",
            ),
            duty_end=(
                "Actual Arrival datetime",
                "max",
            ),
        )
        .reset_index()
    )

    duty_windows["duty_hours"] = (
        (
            duty_windows["duty_end"]
            + post_buffer
        )
        -
        (
            duty_windows["duty_start"]
            - pre_buffer
        )
    ).dt.total_seconds() / 3600

    duty_violations = duty_windows[
        duty_windows["duty_hours"]
        > MAX_DUTY_HOURS
    ]

    if duty_violations.empty:

        print(
            f"PASS - All {len(duty_windows)} duties "
            f"within {MAX_DUTY_HOURS}h limit"
        )

    else:

        violations_found = True

        print(
            f"FAIL - {len(duty_violations)} "
            f"violation(s):"
        )

        for _, row in duty_violations.iterrows():

            print(
                f"    {row['Crew ID']} | "
                f"Duty {row['Duty ID']} | "
                f"{row['duty_hours']:.2f}h "
                f"(exceeds by "
                f"{row['duty_hours'] - MAX_DUTY_HOURS:.2f}h)"
            )

    # --------------------------------------------------
    # CHECK 2A: 28-DAY CUMULATIVE DUTY HOURS
    # --------------------------------------------------

    print(
        f"\n[CHECK 1B] TOTAL DUTY HOURS <= "
        f"{MAX_DUTY_HOURS_28D}h "
        f"(28-day horizon)"
    )

    print("-" * 40)

    crew_totals = (
        duty_windows
        .groupby("Crew ID")["duty_hours"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "duty_hours": "total_duty_hours"
            }
        )
    )

    cumulative_violations = crew_totals[
        crew_totals["total_duty_hours"]
        > MAX_DUTY_HOURS_28D
    ]

    if cumulative_violations.empty:

        print(
            f"PASS - All crew within "
            f"{MAX_DUTY_HOURS_28D}h limit"
        )

    else:

        violations_found = True

        print(
            f"FAIL - "
            f"{len(cumulative_violations)} "
            f"violation(s):"
        )

        for _, row in cumulative_violations.iterrows():

            print(
                f"    {row['Crew ID']} | "
                f"{row['total_duty_hours']:.1f}h "
                f"(exceeds by "
                f"{row['total_duty_hours'] - MAX_DUTY_HOURS_28D:.1f}h)"
            )

    print(
        f"Window hours (min/avg/max) = "
        f"{crew_totals['total_duty_hours'].min():.1f}h / "
        f"{crew_totals['total_duty_hours'].mean():.1f}h / "
        f"{crew_totals['total_duty_hours'].max():.1f}h"
    )

    
    # --------------------------------------------------
    # CHECK 2B: 7-DAY DUTY LIMIT
    # --------------------------------------------------

    print(
        f"\n[CHECK 1C] TOTAL DUTY HOURS <= "
        f"{MAX_DUTY_HOURS_7D}h "
        f"(any 7 consecutive days)"
    )

    print("-" * 40)

    duty_windows["duty_date"] = (
        duty_windows["duty_start"].dt.floor("D")
    )

    violations_7d = []

    min_7d_hours, avg_7d_hours, max_7d_hours = _rolling_window_hour_stats(
        duty_windows,
        window_days=7,
    )

    for crew_id, crew_duties in (
        duty_windows.groupby("Crew ID")
    ):

        crew_duties = (
            crew_duties.sort_values("duty_date")
        )

        for day in crew_duties["duty_date"].unique():

            end_day = day + timedelta(days=7)

            hours = crew_duties[
                (crew_duties["duty_date"] >= day)
                &
                (crew_duties["duty_date"] < end_day)
            ]["duty_hours"].sum()

            if hours > MAX_DUTY_HOURS_7D:

                violations_7d.append(
                    {
                        "Crew ID": crew_id,
                        "Start Day": day,
                        "Hours": hours,
                    }
                )


    if not violations_7d:

        print(
            f"PASS - No crew exceeded "
            f"{MAX_DUTY_HOURS_7D}h in any "
            f"7-day period"
        )

        print(
            f"Window hours (min/avg/max) = "
            f"{min_7d_hours:.1f}h / {avg_7d_hours:.1f}h / {max_7d_hours:.1f}h"
        )

    else:

        violations_found = True

        print(
            f"FAIL - "
            f"{len(violations_7d)} violation(s)"
        )

        print(
            f"Window hours (min/avg/max) = "
            f"{min_7d_hours:.1f}h / {avg_7d_hours:.1f}h / {max_7d_hours:.1f}h"
        )

        for v in violations_7d[:10]:

            print(
                f"    {v['Crew ID']} | "
                f"{v['Hours']:.1f}h"
            )

    # --------------------------------------------------
    # CHECK 2C: 14-DAY DUTY LIMIT
    # --------------------------------------------------

    print(
        f"\n[CHECK 1D] TOTAL DUTY HOURS <= "
        f"{MAX_DUTY_HOURS_14D}h "
        f"(any 14 consecutive days)"
    )

    print("-" * 40)

    violations_14d = []

    min_14d_hours, avg_14d_hours, max_14d_hours = _rolling_window_hour_stats(
        duty_windows,
        window_days=14,
    )

    for crew_id, crew_duties in (
        duty_windows.groupby("Crew ID")
    ):

        crew_duties = (
            crew_duties.sort_values("duty_date")
        )

        for day in crew_duties["duty_date"].unique():

            end_day = day + timedelta(days=14)

            hours = crew_duties[
                (crew_duties["duty_date"] >= day)
                &
                (crew_duties["duty_date"] < end_day)
            ]["duty_hours"].sum()

            if hours > MAX_DUTY_HOURS_14D:

                violations_14d.append(
                    {
                        "Crew ID": crew_id,
                        "Start Day": day,
                        "Hours": hours,
                    }
                )

    if not violations_14d:

        print(
            f"PASS - No crew exceeded "
            f"{MAX_DUTY_HOURS_14D}h in any "
            f"14-day period"
        )

        print(
            f"Window hours (min/avg/max) = "
            f"{min_14d_hours:.1f}h / {avg_14d_hours:.1f}h / {max_14d_hours:.1f}h"
        )

    else:

        violations_found = True

        print(
            f"FAIL - "
            f"{len(violations_14d)} violation(s)"
        )

        print(
            f"Window hours (min/avg/max) = "
            f"{min_14d_hours:.1f}h / {avg_14d_hours:.1f}h / {max_14d_hours:.1f}h"
        )

        for v in violations_14d[:10]:

            print(
                f"    {v['Crew ID']} | "
                f"{v['Hours']:.1f}h"
            )
            
    # --------------------------------------------------
    # CHECK 3: MINIMUM REST
    # --------------------------------------------------

    print(
        f"\n[CHECK 2] MIN_REST_HOURS >= "
        f"{MIN_REST_HOURS}h "
        f"(measured between duty periods)"
    )

    print("-" * 40)

    rest_violations = []

    for crew_id, crew_duties in (
        duty_windows.groupby("Crew ID")
    ):

        crew_duties = (
            crew_duties
            .sort_values("duty_start")
        )

        for i in range(
            1,
            len(crew_duties),
        ):

            prev_end = (
                crew_duties.iloc[i - 1]["duty_end"]
                + post_buffer
            )

            next_start = (
                crew_duties.iloc[i]["duty_start"]
                - pre_buffer
            )

            rest_hours = (
                next_start - prev_end
            ).total_seconds() / 3600

            if rest_hours < MIN_REST_HOURS:

                rest_violations.append(
                    {
                        "Crew ID": crew_id,
                        "Previous Duty":
                            crew_duties.iloc[i - 1]["Duty ID"],
                        "Next Duty":
                            crew_duties.iloc[i]["Duty ID"],
                        "Rest Hours":
                            rest_hours,
                    }
                )

    if not rest_violations:

        print(
            f"PASS - All crew have >= "
            f"{MIN_REST_HOURS}h rest "
            f"between duties"
        )

    else:

        violations_found = True

        print(
            f"FAIL - {len(rest_violations)} "
            f"violation(s):"
        )

        for v in rest_violations:

            print(
                f"    {v['Crew ID']} | "
                f"Duty {v['Previous Duty']} -> "
                f"{v['Next Duty']} | "
                f"{v['Rest Hours']:.2f}h rest "
                f"(short by "
                f"{MIN_REST_HOURS - v['Rest Hours']:.2f}h)"
            )

    # --------------------------------------------------
    # CHECK 4: MIN CONNECTION TIME
    # --------------------------------------------------

    print(
        f"\n[CHECK 3] MIN_CONNECTION_MINUTES >= "
        f"{MIN_CONNECTION_MINUTES}min"
    )

    print("-" * 40)

    connection_violations = []

    for (crew_id, duty_id), group in (
        assigned.groupby(
            ["Crew ID", "Duty ID"]
        )
    ):

        group = group.sort_values(
            "Actual Departure datetime"
        )

        for i in range(
            1,
            len(group),
        ):

            prev_arr = (
                group.iloc[i - 1]
                ["Actual Arrival datetime"]
            )

            next_dep = (
                group.iloc[i]
                ["Actual Departure datetime"]
            )

            connection_min = (
                next_dep - prev_arr
            ).total_seconds() / 60

            if (
                connection_min
                < MIN_CONNECTION_MINUTES
            ):

                connection_violations.append(
                    {
                        "Crew ID": crew_id,
                        "Duty ID": duty_id,
                        "From Flight":
                            group.iloc[i - 1]["Flight Number"],
                        "To Flight":
                            group.iloc[i]["Flight Number"],
                        "Connection Min":
                            connection_min,
                    }
                )

    if not connection_violations:

        print(
            f"PASS - All connections >= "
            f"{MIN_CONNECTION_MINUTES}min"
        )

    else:

        violations_found = True

        print(
            f"FAIL - "
            f"{len(connection_violations)} "
            f"violation(s):"
        )

        for v in connection_violations:

            print(
                f"    {v['Crew ID']} | "
                f"Duty {v['Duty ID']} | "
                f"{v['From Flight']} -> "
                f"{v['To Flight']} | "
                f"{v['Connection Min']:.0f}min "
                f"(short by "
                f"{MIN_CONNECTION_MINUTES - v['Connection Min']:.0f}min)"
            )

    # --------------------------------------------------
    # SUMMARY
    # --------------------------------------------------

    print("\n" + "=" * 60)

    if violations_found:

        print(
            "RESULT: VIOLATIONS DETECTED - "
            "see details above"
        )

    else:

        print(
            "RESULT: ALL CONSTRAINTS SATISFIED"
        )

    print("=" * 60)

    return not violations_found