"""Flight-level operations: delays, cancellations, connection propagation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from .config import Config


def refresh_roster_actual_times(flights: pd.DataFrame, roster_df: pd.DataFrame) -> None:
    """Sync roster actual times from flights after any delay."""

    actual_lookup = (
        flights[["Flight Number", "Actual Departure datetime", "Actual Arrival datetime"]]
        .drop_duplicates(subset="Flight Number")
        .set_index("Flight Number")
    )
    roster_df["Actual Departure datetime"] = roster_df["Flight Number"].map(
        actual_lookup["Actual Departure datetime"]
    )
    roster_df["Actual Arrival datetime"] = roster_df["Flight Number"].map(
        actual_lookup["Actual Arrival datetime"]
    )


def apply_flight_delay_hours(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    flight_number: str,
    delay_hours: float,
    config: Config,
    engine: Any,
    source: str = "AUTO",
) -> None:
    """Apply delay to one flight, propagate connections, and check safeguards."""

    idx = flights[flights["Flight Number"] == flight_number].index
    flights.loc[idx, "Actual Departure datetime"] += timedelta(hours=delay_hours)
    flights.loc[idx, "Actual Arrival datetime"] += timedelta(hours=delay_hours)

    refresh_roster_actual_times(flights, roster_df)
    roster_df.loc[roster_df["Flight Number"] == flight_number, "Flight Delayed"] = 1

    print(
        f"[{source}_DELAY] {flight_number} delayed by "
        f"{delay_hours:.2f}h | reason: crew needing rest"
    )

    propagate_connection_delays(flights, roster_df, flight_number, config, engine)

    duty_id = flights.loc[idx, "Duty ID"].iloc[0]
    check_duty_overrun(flights, roster_df, duty_id, config)

    check_excessive_delay(flights, roster_df, duty_id, config)
    check_curfew(flights, roster_df, duty_id, config)


def propagate_connection_delays(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    delayed_flight_number: str,
    config: Config,
    engine: Any,
) -> None:
    """Push subsequent flights in the same duty to maintain minimum connection time."""

    flight_row = flights[flights["Flight Number"] == delayed_flight_number].iloc[0]
    duty_id = flight_row["Duty ID"]

    duty_flight_numbers = (
        flights[flights["Duty ID"] == duty_id]
        .sort_values("Departure datetime")["Flight Number"]
        .tolist()
    )

    for i in range(len(duty_flight_numbers) - 1):
        current_fn = duty_flight_numbers[i]
        next_fn = duty_flight_numbers[i + 1]

        current_arr = flights.loc[flights["Flight Number"] == current_fn, "Actual Arrival datetime"].iloc[0]
        next_idx = flights[flights["Flight Number"] == next_fn].index[0]
        next_dep = flights.loc[next_idx, "Actual Departure datetime"]

        connection_min = (next_dep - current_arr).total_seconds() / 60

        if connection_min < config.min_connection_minutes:
            shortfall_min = config.min_connection_minutes - connection_min
            shortfall = timedelta(minutes=shortfall_min)

            engine.secondary_delay_minutes += shortfall_min

            flights.loc[next_idx, "Actual Departure datetime"] += shortfall
            flights.loc[next_idx, "Actual Arrival datetime"] += shortfall

            roster_df.loc[roster_df["Flight Number"] == next_fn, "Flight Delayed"] = 1

            print(
                f"[CONNECTION] {next_fn} pushed by "
                f"{shortfall_min:.0f}min to maintain "
                f"{config.min_connection_minutes}min connection after "
                f"{current_fn}"
            )

    refresh_roster_actual_times(flights, roster_df)


def propagate_rest_delays(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    duty_id: str,
    config: Config,
    engine: Any,
    _visited: set[str] | None = None,
) -> int:
    """Adjust subsequent duties if the rest gap becomes too short."""
    if _visited is None:
        _visited = set()
    if duty_id in _visited:
        return 0
    _visited.add(duty_id)

    pre_buffer = timedelta(minutes=config.pre_duty_minutes)
    post_buffer = timedelta(minutes=config.post_duty_minutes)
    min_rest = timedelta(hours=config.min_rest_hours)

    affected_crew = roster_df[
        (roster_df["Duty ID"] == duty_id)
        & (roster_df["Assignment Status"] == "Assigned")
    ]["Crew ID"].unique()

    adjustments = 0

    for crew_id in affected_crew:
        crew_in_duty = roster_df[
            (roster_df["Crew ID"] == crew_id)
            & (roster_df["Duty ID"] == duty_id)
            & (roster_df["Assignment Status"] == "Assigned")
        ]
        if crew_in_duty.empty:
            continue

        duty_end_dt = pd.to_datetime(crew_in_duty["Actual Arrival datetime"]).max()

        future = roster_df[
            (roster_df["Crew ID"] == crew_id)
            & (roster_df["Assignment Status"] == "Assigned")
            & (pd.to_datetime(roster_df["Actual Departure datetime"]) > duty_end_dt)
        ].sort_values("Actual Departure datetime")

        if future.empty:
            continue

        next_duty_id = future.iloc[0]["Duty ID"]
        next_duty_rows = future[future["Duty ID"] == next_duty_id]
        next_first_dep = pd.to_datetime(next_duty_rows["Actual Departure datetime"].min())

        rest = (next_first_dep - pre_buffer) - (duty_end_dt + post_buffer)
        shortfall = min_rest - rest

        if shortfall.total_seconds() > 0:
            delay_hours = shortfall.total_seconds() / 3600
            delay_minutes = shortfall.total_seconds() / 60
            engine.secondary_delay_minutes += delay_minutes

            first_flight = (
                next_duty_rows
                .sort_values("Actual Departure datetime")
                .iloc[0]["Flight Number"]
            )

            apply_flight_delay_hours(
                flights, roster_df, first_flight, delay_hours, config, engine, source="REST"
            )
            propagate_rest_delays(flights, roster_df, next_duty_id, config, engine, _visited)
            adjustments += 1

    return adjustments


def check_curfew(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    duty_id: str,
    config: Config,
) -> int:
    """Cancel flights departing after the airport curfew."""
    cancelled_count = 0

    duty_flights = flights[(flights["Duty ID"] == duty_id) & (flights["Cancelled"] == 0)]

    for _, flight in duty_flights.iterrows():
        flight_number = flight["Flight Number"]
        actual_dep = flight["Actual Departure datetime"]

        dep_time_minutes = actual_dep.hour * 60 + actual_dep.minute
        curfew_minutes = config.curfew_hour * 60 + config.curfew_minute

        if dep_time_minutes >= curfew_minutes:
            flights.loc[flights["Flight Number"] == flight_number, "Cancelled"] = 1
            roster_df.loc[roster_df["Flight Number"] == flight_number, "Flight Cancelled"] = 1
            roster_df.loc[
                (roster_df["Flight Number"] == flight_number)
                & (roster_df["Assignment Status"] == "Assigned"),
                "Assignment Status",
            ] = "Cancelled - Curfew"

            print(
                f"[CURFEW] {flight_number} cancelled | "
                f"actual departure {actual_dep:%H:%M} exceeds "
                f"{config.curfew_hour:02d}:{config.curfew_minute:02d} curfew"
            )
            cancelled_count += 1

    return cancelled_count


def check_excessive_delay(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    duty_id: str,
    config: Config,
) -> int:
    """Auto-cancel flights whose total delay exceeds the configured threshold."""
    max_delay = timedelta(minutes=config.max_delay_minutes)
    cancelled_count = 0

    duty_flights = flights[(flights["Duty ID"] == duty_id) & (flights["Cancelled"] == 0)]

    for _, flight in duty_flights.iterrows():
        flight_number = flight["Flight Number"]
        scheduled_dep = flight["Departure datetime"]
        actual_dep = flight["Actual Departure datetime"]
        total_delay = actual_dep - scheduled_dep

        if total_delay >= max_delay:
            flights.loc[flights["Flight Number"] == flight_number, "Cancelled"] = 1
            roster_df.loc[roster_df["Flight Number"] == flight_number, "Flight Cancelled"] = 1
            roster_df.loc[
                (roster_df["Flight Number"] == flight_number)
                & (roster_df["Assignment Status"] == "Assigned"),
                "Assignment Status",
            ] = "Cancelled - Excessive Delay"

            delay_minutes = total_delay.total_seconds() / 60

            print(
                f"[EXCESSIVE_DELAY] {flight_number} cancelled | "
                f"total delay {delay_minutes:.0f}min exceeds "
                f"{config.max_delay_minutes}min threshold"
            )
            cancelled_count += 1

    return cancelled_count


def check_duty_overrun(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    duty_id: str,
    config: Config,
) -> int:
    """Cancel flights from a duty once its buffered duration exceeds the limit."""
    pre_buffer = timedelta(minutes=config.pre_duty_minutes)
    post_buffer = timedelta(minutes=config.post_duty_minutes)
    max_duty = timedelta(hours=config.max_duty_hours)

    duty_flights = flights[(flights["Duty ID"] == duty_id) & (flights["Cancelled"] == 0)].sort_values("Actual Departure datetime")

    if len(duty_flights) == 0:
        return 0

    duty_start = duty_flights.iloc[0]["Actual Departure datetime"] - pre_buffer
    cancelled_count = 0

    for i in range(len(duty_flights)):
        flight_end = duty_flights.iloc[i]["Actual Arrival datetime"] + post_buffer
        actual_duty_hours = flight_end - duty_start

        if actual_duty_hours > max_duty:
            overrun_flights = duty_flights.iloc[i:]

            for _, flight in overrun_flights.iterrows():
                flight_number = flight["Flight Number"]

                if flights.loc[flights["Flight Number"] == flight_number, "Cancelled"].iloc[0] == 1:
                    continue

                flights.loc[flights["Flight Number"] == flight_number, "Cancelled"] = 1
                roster_df.loc[roster_df["Flight Number"] == flight_number, "Flight Cancelled"] = 1
                roster_df.loc[
                    (roster_df["Flight Number"] == flight_number)
                    & (roster_df["Assignment Status"] == "Assigned"),
                    "Assignment Status",
                ] = "Cancelled - Duty Overrun"

                overrun_minutes = (actual_duty_hours - max_duty).total_seconds() / 60

                assigned_crew = roster_df[
                    (roster_df["Flight Number"] == flight_number)
                    & (roster_df["Assignment Status"] == "Cancelled - Duty Overrun")
                ]["Crew ID"].tolist()

                print(
                    f"[DUTY_OVERRUN] {flight_number} cancelled | "
                    f"duty {duty_id} exceeded {config.max_duty_hours}h limit "
                    f"by {overrun_minutes:.0f}min | crew: {', '.join(assigned_crew)}"
                )

                cancelled_count += 1

            break

    return cancelled_count


def verify_minimum_crew(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    flight_number: str,
    config: Config,
) -> bool:
    """Verify that a flight still has the required number of pilots assigned."""
    if flights.loc[flights["Flight Number"] == flight_number, "Cancelled"].iloc[0] == 1:
        return True

    assigned_crew = roster_df[
        (roster_df["Flight Number"] == flight_number)
        & (roster_df["Assignment Status"] == "Assigned")
    ]

    if len(assigned_crew) < config.pilots_per_duty:
        flights.loc[flights["Flight Number"] == flight_number, "Cancelled"] = 1
        roster_df.loc[roster_df["Flight Number"] == flight_number, "Flight Cancelled"] = 1
        roster_df.loc[
            (roster_df["Flight Number"] == flight_number)
            & (roster_df["Assignment Status"] == "Assigned"),
            "Assignment Status",
        ] = "Cancelled - Insufficient Crew"

        current_count = len(assigned_crew)
        crew_ids = assigned_crew["Crew ID"].tolist()

        print(
            f"[CREW_SHORT] {flight_number} cancelled | "
            f"only {current_count}/{config.pilots_per_duty} pilots assigned | "
            f"remaining: {', '.join(crew_ids) if crew_ids else 'none'}"
        )

        return False

    return True


def cancel_flight(
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    flight_number: str,
    sick_crew_id: str,
) -> None:
    """Flag cancellation on both flights and roster."""

    flights.loc[flights["Flight Number"] == flight_number, "Cancelled"] = 1
    roster_df.loc[roster_df["Flight Number"] == flight_number, "Flight Cancelled"] = 1
    roster_df.loc[
        (roster_df["Flight Number"] == flight_number)
        & (roster_df["Assignment Status"] == "Assigned"),
        "Assignment Status",
    ] = "Cancelled"

    print(f"[CANCEL] {flight_number} | no valid replacement for {sick_crew_id}")