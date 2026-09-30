"""Crew replacement logic: find replacements, validate rules, handle unavailability."""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Any

import pandas as pd

from .flight_ops import apply_flight_delay_hours, cancel_flight, propagate_rest_delays


def passes_duty_and_rest_rules(
    candidate_flights: pd.DataFrame,
    flight_row: pd.Series,
    candidate_id: str,
    config: Any,
) -> tuple[bool, str, dict[str, float]]:
    """Validate a hypothetical assignment against duty/rest/connection rules."""

    sim = candidate_flights[
        [
            "Crew ID",
            "Duty ID",
            "Flight Number",
            "Tail Number",
            "Actual Departure datetime",
            "Actual Arrival datetime",
        ]
    ].copy()

    sim.loc[len(sim)] = {
        "Crew ID": candidate_id,
        "Duty ID": flight_row["Duty ID"],
        "Flight Number": flight_row["Flight Number"],
        "Tail Number": flight_row["Tail Number"],
        "Actual Departure datetime": pd.to_datetime(flight_row["Actual Departure datetime"]),
        "Actual Arrival datetime": pd.to_datetime(flight_row["Actual Arrival datetime"]),
    }
    sim = sim.drop_duplicates(subset=["Flight Number"])

    # Connection check
    sim_sorted = sim.sort_values("Actual Departure datetime").reset_index(drop=True)
    for i in range(1, len(sim_sorted)):
        same_crew = sim_sorted.loc[i - 1, "Crew ID"] == sim_sorted.loc[i, "Crew ID"]
        same_tail = sim_sorted.loc[i - 1, "Tail Number"] == sim_sorted.loc[i, "Tail Number"]
        if not (same_crew or same_tail):
            continue
        prev_arr = pd.to_datetime(sim_sorted.loc[i - 1, "Actual Arrival datetime"])
        next_dep = pd.to_datetime(sim_sorted.loc[i, "Actual Departure datetime"])
        conn_min = (next_dep - prev_arr).total_seconds() / 60
        if conn_min < config.min_connection_minutes:
            return False, f"connection {conn_min:.0f}m < {config.min_connection_minutes}m", {}

    # Duty hours check (includes pre-duty report and post-duty release buffers)
    pre_buffer = timedelta(minutes=config.pre_duty_minutes)
    post_buffer = timedelta(minutes=config.post_duty_minutes)

    duty_windows = (
        sim.groupby("Duty ID", dropna=True)
        .agg(
            duty_start=("Actual Departure datetime", "min"),
            duty_end=("Actual Arrival datetime", "max"),
        )
        .sort_values("duty_start")
    )
    target_duty = flight_row["Duty ID"]
    if target_duty in duty_windows.index:
        target = duty_windows.loc[target_duty]
        duty_hours = (
            (target["duty_end"] + post_buffer) - (target["duty_start"] - pre_buffer)
        ).total_seconds() / 3600
        if duty_hours > config.max_duty_hours:
            return False, f"duty {duty_hours:.2f}h > {config.max_duty_hours}h", {}

    # Rest check (rest measured between duty periods including buffers)
    for i in range(1, len(duty_windows)):
        prev_end = duty_windows.iloc[i - 1]["duty_end"] + post_buffer
        next_start = duty_windows.iloc[i]["duty_start"] - pre_buffer
        rest_hours = (next_start - prev_end).total_seconds() / 3600
        if rest_hours < config.min_rest_hours:
            return False, f"rest {rest_hours:.2f}h < {config.min_rest_hours}h", {"rest_hours": rest_hours}

    return True, "ok", {}


def find_replacement(
    sick_crew: Any,
    flight_row: pd.Series,
    roster_df: pd.DataFrame,
    replacement_queue: deque[Any],
    config: Any,
    current_dt: pd.Timestamp | None = None,
) -> tuple[Any | None, float]:
    """Find best available replacement crew for a flight."""

    fl_dep = pd.to_datetime(flight_row["Actual Departure datetime"])
    fl_arr = pd.to_datetime(flight_row["Actual Arrival datetime"])
    best_rest_candidate: dict[str, Any] | None = None

    for candidate in replacement_queue:
        debug_prefix = f"[DEBUG] {flight_row['Flight Number']} | {candidate.id} | "

        if candidate.id == sick_crew.id:
            if config.debug:
                print(debug_prefix + "same crew as sick")
            continue

        already_assigned = (
            (roster_df["Crew ID"] == candidate.id)
            & (roster_df["Flight Number"] == flight_row["Flight Number"])
            & (roster_df["Assignment Status"] == "Assigned")
        ).any()
        if already_assigned:
            if config.debug:
                print(debug_prefix + "already assigned to this flight")
            continue

        if candidate.absent:
            if config.debug:
                print(debug_prefix + "candidate absent")
            continue

        if candidate.is_standby and current_dt is not None:
            time_until_departure = (fl_dep - current_dt).total_seconds() / 60
            if time_until_departure < config.standby_notification_minutes:
                if config.debug:
                    print(
                        debug_prefix +
                        f"standby needs {config.standby_notification_minutes}min notice, "
                        f"only {time_until_departure:.0f}min until departure"
                    )
                continue

        candidate_flights = roster_df[
            (roster_df["Crew ID"] == candidate.id)
            & (roster_df["Assignment Status"] == "Assigned")
        ]

        prior = candidate_flights[pd.to_datetime(candidate_flights["Actual Departure datetime"]) < fl_dep]
        if not prior.empty:
            latest = prior.loc[pd.to_datetime(prior["Actual Departure datetime"]).idxmax()]
            current_airport = latest["Arr Airport"]
        else:
            current_airport = candidate.base

        if current_airport != flight_row["Dep Airport"]:
            if config.debug:
                print(debug_prefix + f"airport mismatch ({current_airport} != {flight_row['Dep Airport']})")
            continue

        conflict = False
        for _, cf in candidate_flights.iterrows():
            if pd.isna(cf["Actual Departure datetime"]):
                continue
            cf_dep = pd.to_datetime(cf["Actual Departure datetime"])
            cf_arr = pd.to_datetime(cf["Actual Arrival datetime"])
            if cf_dep < fl_arr and cf_arr > fl_dep:
                conflict = True
                break

        if conflict:
            if config.debug:
                print(debug_prefix + "time overlap conflict")
            continue

        ok, reason, reason_data = passes_duty_and_rest_rules(candidate_flights, flight_row, candidate.id, config)
        if not ok:
            if reason.startswith("rest "):
                rest_hours = reason_data.get("rest_hours", 0)
                shortfall = max(0, config.min_rest_hours - rest_hours)
                if shortfall > 0 and (best_rest_candidate is None or shortfall < best_rest_candidate["delay_hours"]):
                    best_rest_candidate = {"candidate": candidate, "delay_hours": shortfall}
            if config.debug:
                print(debug_prefix + reason)
            continue

        if config.debug:
            print(debug_prefix + "eligible")

        replacement_queue.remove(candidate)
        replacement_queue.append(candidate)
        return candidate, 0.0

    if best_rest_candidate is not None:
        return best_rest_candidate["candidate"], float(best_rest_candidate["delay_hours"])

    return None, 0.0


def add_unavailable_records(
    crew_member: Any,
    current_dt: pd.Timestamp,
    flights: pd.DataFrame,
    roster_df: pd.DataFrame,
    replacement_queue: deque[Any],
    config: Any,
    engine: Any,
) -> None:
    """Mark crew as unavailable and attempt replacements for their flights."""

    affected = roster_df[
        (roster_df["Crew ID"] == crew_member.id)
        & (roster_df["Assignment Status"] == "Assigned")
        & (pd.to_datetime(roster_df["Actual Departure datetime"]) >= current_dt)
    ]

    processed_flights: list[str] = []

    for _, row in affected.iterrows():
        flight_number = row["Flight Number"]

        if flights.loc[flights["Flight Number"] == flight_number, "Cancelled"].iloc[0] == 1:
            continue

        roster_df.loc[
            (roster_df["Crew ID"] == row["Crew ID"])
            & (roster_df["Flight Number"] == flight_number)
            & (roster_df["Assignment Status"] == "Assigned"),
            ["Assignment Status", "Crew Change"],
        ] = ["Unavailable", 1]

        roster_df.loc[roster_df["Flight Number"] == flight_number, "Crew Change"] = 1

        replacement, delay_hours = find_replacement(
            crew_member, row, roster_df, replacement_queue, config, current_dt
        )

        if replacement and delay_hours > 0:
            apply_flight_delay_hours(
                flights,
                roster_df,
                flight_number,
                delay_hours,
                config,
                engine,
                source="REST",
            )
            propagate_rest_delays(flights, roster_df, row["Duty ID"], config, engine)

            if flights.loc[flights["Flight Number"] == flight_number, "Cancelled"].iloc[0] == 1:
                processed_flights.append(flight_number)
                continue

            updated = roster_df[
                (roster_df["Crew ID"] == row["Crew ID"])
                & (roster_df["Flight Number"] == flight_number)
                & (roster_df["Assignment Status"] == "Unavailable")
            ]
            if not updated.empty:
                row = updated.iloc[0]
            replacement, remaining_delay = find_replacement(
                crew_member, row, roster_df, replacement_queue, config, current_dt
            )
            if remaining_delay > 0:
                if config.debug:
                    print(f"[DEBUG] {flight_number} | {replacement.id} | still needs {remaining_delay:.2f}h rest after delay — rejected")
                replacement = None

        if replacement:
            roster_df.loc[len(roster_df)] = {
                "Crew ID": replacement.id,
                "Duty ID": row["Duty ID"],
                "Flight Number": flight_number,
                "Dep Airport": row["Dep Airport"],
                "Arr Airport": row["Arr Airport"],
                "Tail Number": row["Tail Number"],
                "Scheduled Departure datetime": row["Scheduled Departure datetime"],
                "Scheduled Arrival datetime": row["Scheduled Arrival datetime"],
                "Actual Departure datetime": row["Actual Departure datetime"],
                "Actual Arrival datetime": row["Actual Arrival datetime"],
                "Role": row["Role"],
                "Assignment Status": "Assigned",
                "Flight Delayed": row["Flight Delayed"],
                "Crew Change": 1,
                "Flight Cancelled": 0,
            }
            engine.record_reassignment(
                duty_id=row["Duty ID"],
                old_crew_id=crew_member.id,
                new_crew_id=replacement.id,
            )
        else:
            cancel_flight(flights, roster_df, flight_number, crew_member.id)

        processed_flights.append(flight_number)

    # After all replacements resolve, verify minimum crew on each affected flight
    for flight_number in processed_flights:
        verify_minimum_crew(flights, roster_df, flight_number, config)
