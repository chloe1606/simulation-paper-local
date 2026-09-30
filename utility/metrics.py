
import re
import pandas as pd


def _resolve_column(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    """Return the first matching column from candidates (case-insensitive)."""

    lookup = {str(col).strip().lower(): col for col in df.columns}
    for candidate in candidates:
        match = lookup.get(candidate.strip().lower())
        if match is not None:
            return match

    if required:
        raise KeyError(
            f"Could not find any of {candidates} in dataframe columns: {list(df.columns)}"
        )
    return None


def compute_key_metrics(engine, sim_log: str) -> tuple[str, pd.DataFrame]:
    """
    Compute key metrics from a completed SimulationEngine run.

    Parameters
    ----------
    engine  : SimulationEngine — completed simulation (engine.run() already called)
    sim_log : str              — captured stdout from the simulation run

    Returns
    -------
    config_str : str           — one-line run configuration summary for printing
    df_metrics : pd.DataFrame  — two-column (Metric, Value) table ready for display()
    """
    flights = engine.flights
    roster  = engine.roster_df
    n       = len(flights)

    # Support both legacy and normalized naming conventions.
    flight_no_col = _resolve_column(flights, ["Flight Number", "flight_number"])
    duty_col = _resolve_column(flights, ["Duty ID", "duty_id"], required=False)
    cancelled_col = _resolve_column(flights, ["Cancelled", "cancelled"])
    dep_col = _resolve_column(
        flights,
        ["Departure datetime", "dep_datetime", "scheduled_departure_datetime"],
    )
    act_dep_col = _resolve_column(
        flights,
        ["Actual Departure datetime", "actual_dep_datetime", "actual_departure_datetime"],
    )
    arr_col = _resolve_column(
        flights,
        ["Arrival datetime", "arr_datetime", "scheduled_arrival_datetime"],
        required=False,
    )
    act_arr_col = _resolve_column(
        flights,
        ["Actual Arrival datetime", "actual_arr_datetime", "actual_arrival_datetime"],
        required=False,
    )

    roster_flight_no_col = _resolve_column(roster, ["Flight Number", "flight_number"])
    roster_duty_col = _resolve_column(roster, ["Duty ID", "duty_id"])
    roster_crew_col = _resolve_column(roster, ["Crew ID", "crew_id"])
    roster_status_col = _resolve_column(roster, ["Assignment Status", "assignment_status"])

    duration_col = _resolve_column(
        flights,
        ["Duration", "duration_min", "act_duration_min", "duty_act_duration_min"],
        required=False,
    )

    # ---- Number of Flights ----------------------------------------------
    num_flights_scheduled = len(flights)

    # ---- Flight completion rate ------------------------------------------
    covered  = set(roster.loc[roster[roster_status_col] == "Assigned", roster_flight_no_col])
    operated = flights[flights[flight_no_col].isin(covered) & (flights[cancelled_col] == 0)]
    num_flights_operated = len(operated)
    R = len(operated) / n * 100

    # ---- Cancellations ---------------------------------------------------
    total_cancelled = n - len(operated)

    # ---- Total delay (min) -----------------------------------------------
    total_delay = int(
        (flights[act_dep_col] - flights[dep_col])
        .dt.total_seconds().sum() / 60
    )

    # ---- Delay propagation factor ----------------------------------------
    propagation_factor = (
        engine.secondary_delay_minutes
        / engine.primary_delay_minutes
        if engine.primary_delay_minutes > 0
        else 0
    )

    amplification_factor = (
        (engine.primary_delay_minutes + engine.secondary_delay_minutes)
        / engine.primary_delay_minutes
        if engine.primary_delay_minutes > 0
        else 0
    )

    # ---- Duty coverage --------------------------------------------------
    #print(roster)
    # Use scheduled duties as the denominator when available; fall back to roster duties.
    total_num_of_duties = (
        flights[duty_col].dropna().nunique()
        if duty_col is not None
        else roster[roster_duty_col].dropna().nunique()
    )
    covered_num_of_duties = (
        roster.loc[roster[roster_status_col] == "Assigned", roster_duty_col]
        .dropna()
        .nunique()
    )
    duty_coverage = (
        100 * covered_num_of_duties / total_num_of_duties
        if total_num_of_duties > 0
        else 0.0
    )

    # ---- Crew utilisation (%) -------------------------------------------
    # assigned (crew, duty) pairs / (n_duties x 2 required pilot-slots)
    assigned_slots = (
        roster.loc[roster[roster_status_col] == "Assigned", [roster_crew_col, roster_duty_col]]
        .drop_duplicates().shape[0]
    )
    n_duties = flights[duty_col].nunique() if duty_col is not None else roster[roster_duty_col].nunique()
    crew_util = round(assigned_slots / (n_duties * 2) * 100, 1) if n_duties > 0 else 0.0

    # ---- Crew block hours ------------------------------------------------
    # Build a per-flight duration lookup once, then map onto assigned roster rows.
    if duration_col is not None:
        duration_lookup = pd.to_numeric(
            flights.set_index(flight_no_col)[duration_col], errors="coerce"
        ).fillna(0)
    elif arr_col is not None:
        duration_lookup = (
            pd.to_datetime(flights[arr_col], errors="coerce")
            - pd.to_datetime(flights[dep_col], errors="coerce")
        ).dt.total_seconds().fillna(0) / 60
        duration_lookup.index = flights[flight_no_col]
    elif act_arr_col is not None:
        duration_lookup = (
            pd.to_datetime(flights[act_arr_col], errors="coerce")
            - pd.to_datetime(flights[act_dep_col], errors="coerce")
        ).dt.total_seconds().fillna(0) / 60
        duration_lookup.index = flights[flight_no_col]
    else:
        duration_lookup = pd.Series(0, index=flights[flight_no_col], dtype="float64")

    assigned_flown = roster.loc[
        (roster[roster_status_col] == "Assigned")
        & (roster[roster_flight_no_col].isin(operated[flight_no_col]))
    ].copy()
    duration_min_series = (
        assigned_flown[roster_flight_no_col].map(duration_lookup).fillna(0)
    )

    total_block_h = round(duration_min_series.sum() / 60, 1)
    n_active      = sum(1 for c in engine.crew_pool if not c.is_standby)
    avg_block_h   = round(total_block_h / n_active, 1) if n_active > 0 else 0.0

    sim_days        = (
        (engine.flights[dep_col].max() - engine.simulation_start)
        .total_seconds() / 86400
    )
    avg_block_h_day = round(total_block_h / n_active / sim_days, 1) if n_active > 0 and sim_days > 0 else 0.0

    # ---- Other metrics ---------------------------------------------------
    flight_reassignments = engine.crew_reassignments
    duties_reassigned = len(engine.reassigned_duties)
    dt_violations = engine.duty_overrun_cancellations
    op_delay      = (
        (operated[act_dep_col] - operated[dep_col])
        .dt.total_seconds().sum() / 60
    )
    recovery = round(op_delay / len(operated), 1) if len(operated) > 0 else 0.0

    ave_working_hours = round(duration_min_series.sum() / 60 / n_active / sim_days, 1) if n_active > 0 and sim_days > 0 else 0.0
    on_time_flights = len(operated[operated[act_dep_col] <= operated[dep_col]])
    on_time_rate = round(on_time_flights / len(operated) * 100, 1) if len(operated) > 0 else 0.0

    # ---- Build table -----------------------------------------------------
    rows = [
        {
            "Metric": "Flight completion rate (%)",
            "Value": f"{R:.1f}",
            "Description": "Percentage of scheduled flights that operated (assigned and not cancelled).",
        },
        {
            "Metric": "Scheduled flights",
            "Value": str(num_flights_scheduled),
            "Description": "Total number of flights in the timetable before disruptions.",
        },
        {
            "Metric": "Operated flights",
            "Value": str(num_flights_operated),
            "Description": "Flights that were assigned crew and completed without cancellation.",
        },
        {
            "Metric": "Cancelled flights",
            "Value": str(total_cancelled),
            "Description": "Scheduled flights that did not operate.",
        },
        {
            "Metric": "Total delay (min)",
            "Value": str(total_delay),
            "Description": "Sum of departure delays across all flights in minutes.",
        },
        {
            "Metric": "Delay propagation factor",
            "Value": f"{propagation_factor:.3f}",
            "Description": "Total delay divided by primary random delay, indicating knock-on effects.",
        },
        {
            "Metric": "Crew utilisation (%)",
            "Value": f"{crew_util}",
            "Description": "Share of required pilot-duty slots that are filled.",
        },
        {
            "Metric": "Crew block hours (total)",
            "Value": f"{total_block_h:.1f}",
            "Description": "Total assigned flying time across all crew.",
        },
        {
            "Metric": "Crew block hours (avg / crew)",
            "Value": f"{avg_block_h:.1f}",
            "Description": "Average assigned flying hours per active crew member.",
        },
        {
            "Metric": "Duty coverage (%)",
            "Value": f"{duty_coverage:.1f}", 
            "Description": "Covered duties over total duties",
        },
        {
            "Metric": "Crew average working hours",
            "Value": f"{ave_working_hours:.1f}",
            "Description": "Per-day average assigned flying hours per active crew member.",
        },
        {
            "Metric": "On-time flights",
            "Value": str(on_time_flights),
            "Description": "Number of operated flights departing on or before schedule.",
        },
        {
            "Metric": "On-time rate (%)",
            "Value": f"{on_time_rate}",
            "Description": "Percentage of operated flights that depart on time.",
        },
        {
            "Metric": "Flight reassignments",
            "Value": str(flight_reassignments),
            "Description": "Successful crew replacements performed at flight level.",
        },
                {
            "Metric": "Recovered duties",
            "Value": str(duties_reassigned),
            "Description": "Duties where at least one crew replacement occurred.",
        },
        {
            "Metric": "Duty-time violation flights",
            "Value": str(dt_violations),
            "Description": "Flights cancelled because duty-time limits were exceeded.",
        },
        {
            "Metric": "Schedule recovery time (min/flt)",
            "Value": str(recovery),
            "Description": "Average departure delay per operated flight in minutes.",
        },
    ]
    df_metrics = pd.DataFrame(rows, columns=["Metric", "Value", "Description"])
    
    # Show full column contents
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_columns', None)

    # ---- Config summary line --------------------------------------------
    n_standby = sum(1 for c in engine.crew_pool if c.is_standby)
    cfg = engine.config
    config_str = (
        f"Seed: {cfg.seed} | Absence prob: {cfg.absence_probability} "
        f"| Delay prob: {cfg.delay_probability} "
        f"| Crew: {n_active} active + {n_standby} standby"
    )


    return config_str, df_metrics