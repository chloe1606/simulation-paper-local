"""
Validation orchestration.
"""

from simulation.config import Config

from simulation.validation.duty_hours import (
    check_max_duty_hours,
)

from simulation.validation.cumulative_hours import (
    check_28_day_limit,
    check_7_day_limit,
    check_14_day_limit,
)

from simulation.validation.rest_periods import (
    check_min_rest,
)

from simulation.validation.connections import (
    check_connections,
)

from simulation.validation.crew_per_flight import (
    check_crew_per_flight,
)

from simulation.validation.lhr_curfew import (
    check_lhr_curfew,
)

from simulation.validation.roster_days import (
    check_roster_days,
)

from datetime import timedelta

import pandas as pd

# =====================================================
# Configuration
# =====================================================

_config = Config()

MAX_DUTY_HOURS = _config.max_duty_hours
MIN_REST_HOURS = _config.min_rest_hours
MIN_CONNECTION_MINUTES = (
    _config.min_connection_minutes
)

MAX_DUTY_HOURS_7D = (
    _config.max_duty_hours_7d
)

MAX_DUTY_HOURS_14D = (
    _config.max_duty_hours_14d
)

MAX_DUTY_HOURS_28D = (
    _config.max_duty_hours_28d
)

PRE_DUTY_MINUTES = (
    _config.pre_duty_minutes
)

POST_DUTY_MINUTES = (
    _config.post_duty_minutes
)

PILOTS_PER_DUTY = (
    _config.pilots_per_duty
)

CURFEW_HOUR = (
    _config.curfew_hour
)

ROSTER_DAYS_ON = (
    _config.duty_days_on
)

DUTY_WINDOW_DAYS = (
    _config.rolling_window_days
)

# =====================================================
# Main Validation Function
# =====================================================


def validate_roster(roster_df):

    print("=" * 60)
    print("ROSTER CONSTRAINT VALIDATION")
    print("=" * 60)

    assigned = roster_df[
        roster_df["Assignment Status"]
        == "Assigned"
    ].copy()

    assigned[
        "Actual Departure datetime"
    ] = pd.to_datetime(
        assigned[
            "Actual Departure datetime"
        ]
    )

    assigned[
        "Actual Arrival datetime"
    ] = pd.to_datetime(
        assigned[
            "Actual Arrival datetime"
        ]
    )

    violations_found = False

    pre_buffer = timedelta(
        minutes=PRE_DUTY_MINUTES
    )

    post_buffer = timedelta(
        minutes=POST_DUTY_MINUTES
    )

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

    duty_windows["duty_date"] = (
        duty_windows[
            "duty_start"
        ].dt.floor("D")
    )

    violations_found = (
        check_max_duty_hours(
            duty_windows,
            MAX_DUTY_HOURS,
            violations_found,
        )
    )

    violations_found = (
        check_28_day_limit(
            duty_windows,
            MAX_DUTY_HOURS_28D,
            violations_found,
        )
    )

    violations_found = (
        check_7_day_limit(
            duty_windows,
            MAX_DUTY_HOURS_7D,
            violations_found,
        )
    )

    violations_found = (
        check_14_day_limit(
            duty_windows,
            MAX_DUTY_HOURS_14D,
            violations_found,
        )
    )

    violations_found = (
        check_min_rest(
            duty_windows,
            pre_buffer,
            post_buffer,
            MIN_REST_HOURS,
            violations_found,
        )
    )

    violations_found = (
        check_connections(
            assigned,
            MIN_CONNECTION_MINUTES,
            violations_found,
        )
    )

    violations_found = (
        check_crew_per_flight(
            assigned,
            PILOTS_PER_DUTY,
            violations_found,
        )
    )

    violations_found = (
        check_lhr_curfew(
            assigned,
            CURFEW_HOUR,
            violations_found,
        )
    )

    violations_found = (
        check_roster_days(
            assigned,
            ROSTER_DAYS_ON,
            DUTY_WINDOW_DAYS,
            violations_found,
        )
    )

    print("\n" + "=" * 60)

    if violations_found:

        print(
            "RESULT: VIOLATIONS DETECTED"
        )

    else:

        print(
            "RESULT: ALL CONSTRAINTS SATISFIED"
        )

    print("=" * 60)