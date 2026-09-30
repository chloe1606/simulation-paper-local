"""
Generate realistic airline data and export DFS-structured tables.

Builds structured dataframes:
  - flight_schedule_fact
  - sectors_dim
  - duty_fact
  - aircraft_dim
  - crew_dim

Deployment-aware:
  - On Databricks (with pyspark available), tables are written to Unity Catalog
    Delta tables in addition to (or instead of) Excel.
  - Locally (e.g. VS Code), pyspark/Delta export is skipped automatically and
    only the Excel workbook is produced.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# ============================================================
# ENVIRONMENT DETECTION
# ============================================================

def _detect_databricks() -> bool:
    """True if running inside a Databricks runtime."""
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


IS_DATABRICKS = _detect_databricks()

# pyspark is only expected to be importable on Databricks (or in a local
# environment that happens to have it installed). Import defensively so the
# script still runs standalone in VS Code without pyspark installed.
try:
    from pyspark.sql import SparkSession

    HAS_PYSPARK = True
except ImportError:
    SparkSession = None  # type: ignore[assignment]
    HAS_PYSPARK = False

# Optional project-specific config (provides a shared output path when this
# script lives inside the larger simulation package). Falls back to a local
# timestamped path when that package isn't on the path, e.g. run standalone.
try:
    from simulation.config import Config  # type: ignore

    _DEFAULT_EXCEL_PATH = Path(Config.input_file)
except ImportError:
    _DEFAULT_EXCEL_PATH = (
        Path.cwd()
        / "input"
        / f"airline_inputs_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    )

# ============================================================
# PARAMETERS — adjust these to change the generated schedule
# ============================================================

BASE_AIRPORT = "LHR"

# Destination airports with realistic one-way flight duration in minutes
DESTINATIONS = {
    # British Isles
    "DUB": 75,
    "EDI": 85,
    "GLA": 90,
    "ABZ": 95,
    # Near Europe
    "AMS": 75,
    "CDG": 75,
    "BRU": 70,
    "FRA": 95,
    "ZRH": 105,
    "MUC": 110,
    "CPH": 115,
    "HAM": 105,
    # Medium-haul Europe
    "BCN": 135,
    "MAD": 145,
    "LIS": 160,
    "FCO": 155,
    "NAP": 170,
    "VCE": 135,
    "MXP": 120,
    # Longer Europe
    "ATH": 230,
    "IST": 240,
    "HER": 255,
    "LCA": 280,
}

# Inter-destination flight durations (dest-to-dest, minutes)
# Used for multi-leg middle segments
INTER_DEST_DURATIONS = {
    # Benelux / Germany
    ("AMS", "FRA"): 65,
    ("AMS", "HAM"): 60,
    ("FRA", "MUC"): 60,
    ("CPH", "MUC"): 80,
    # France / Germany
    ("CDG", "FRA"): 70,
    ("CDG", "MUC"): 75,
    ("CDG", "ZRH"): 70,
    ("FRA", "ZRH"): 60,
    ("MUC", "ZRH"): 60,
    # Spain / Portugal
    ("MAD", "BCN"): 65,
    ("LIS", "MAD"): 75,
    ("BCN", "LIS"): 120,
    # Italy
    ("MXP", "VCE"): 60,
    ("FCO", "NAP"): 60,
    ("MXP", "FCO"): 70,
    ("VCE", "FCO"): 65,
    # Eastern Med
    ("ATH", "IST"): 90,
    ("ATH", "LCA"): 105,
    ("IST", "LCA"): 90,
    # Northern Europe
    ("CPH", "AMS"): 75,
    ("CPH", "HAM"): 60,
}

NUM_PLANES = 5
NUM_ACTIVE_CREW = 30
NUM_STANDBY_CREW = 0
NUM_DAYS = 28
START_DATE = datetime(2026, 1, 5)
MAX_DUTY_MINUTES = (
    12 * 60
    - 60  # pre-duty briefing
    - 30  # post-duty debrief
)

# Staggered departure offsets per plane (minutes after shift start)
PLANE_OFFSETS = [i * 30 for i in range(NUM_PLANES)]  # e.g., [0, 30, 60, 90, 120]

# Shift base hours — each plane gets PLANE_OFFSETS[i] added
SHIFT_BASE_HOURS = [6, 14]  # AM and PM shifts

TURNAROUND_MINUTES = 45  # Minimum ground time between flights
SEED = 99  # For reproducible duty type selection

# ============================================================
# EXPORT SWITCHES — auto-configured per environment, override if needed
# ============================================================

# Delta export only makes sense (and is only possible) where pyspark/Unity
# Catalog are actually available. Default: on when running on Databricks
# with pyspark importable, off otherwise (e.g. local VS Code).
EXPORT_TABLES_TO_DELTA = IS_DATABRICKS and HAS_PYSPARK

# Excel export is useful everywhere, so it stays on by default in both
# environments. Set to False if you only want the Delta tables on Databricks.
EXPORT_TABLES_TO_EXCEL = True

EXCEL_PATH = _DEFAULT_EXCEL_PATH

DELTA_CATALOG = "ctl-sandbox-dev"
DELTA_SCHEMA = "simulation_paper"

# ============================================================
# MULTI-LEG ROUTE DEFINITIONS
# ============================================================

# Predefined multi-leg routes: (dest1, dest2) — plane goes BASE→dest1→dest2→BASE
MULTI_LEG_ROUTES = [
    # Benelux / Germany
    ("AMS", "FRA"),
    ("AMS", "HAM"),
    ("CDG", "MUC"),
    ("FRA", "ZRH"),
    ("HAM", "CPH"),
    # Spain / Portugal
    ("MAD", "BCN"),
    ("LIS", "MAD"),
    # Italy
    ("MXP", "VCE"),
    ("FCO", "NAP"),
    ("MXP", "FCO"),
    # Eastern Med
    ("ATH", "IST"),
    ("ATH", "LCA"),
]

# ============================================================
# GENERATION LOGIC
# ============================================================


def get_duration(origin: str, destination: str) -> int:
    """Get flight duration between two airports."""
    if origin == BASE_AIRPORT:
        return DESTINATIONS[destination]
    if destination == BASE_AIRPORT:
        return DESTINATIONS[origin]
    if (origin, destination) in INTER_DEST_DURATIONS:
        return INTER_DEST_DURATIONS[(origin, destination)]
    if (destination, origin) in INTER_DEST_DURATIONS:
        return INTER_DEST_DURATIONS[(destination, origin)]
    return 50  # Estimate based on average


def is_feasible_return(destination: str) -> bool:
    """Whether a BASE→destination→BASE return trip fits within duty limits."""
    duration = DESTINATIONS[destination]
    total_duty = duration + TURNAROUND_MINUTES + duration
    return total_duty <= MAX_DUTY_MINUTES


def get_feasible_multi_leg_routes() -> list[tuple[str, str]]:
    """Return only multi-leg routes that fit within the maximum duty duration."""
    feasible_routes: list[tuple[str, str]] = []

    for dest1, dest2 in MULTI_LEG_ROUTES:
        dur1 = get_duration(BASE_AIRPORT, dest1)
        dur2 = get_duration(dest1, dest2)
        dur3 = get_duration(dest2, BASE_AIRPORT)

        total_duty_minutes = dur1 + TURNAROUND_MINUTES + dur2 + TURNAROUND_MINUTES + dur3

        if total_duty_minutes <= MAX_DUTY_MINUTES:
            feasible_routes.append((dest1, dest2))

    return feasible_routes


def generate_flights() -> pd.DataFrame:
    """Generate a realistic flight schedule with staggered departures and multi-leg duties."""
    random.seed(SEED)
    flights: list[dict[str, str | datetime | int]] = []
    flight_counter = 100
    duty_counter = 1

    plane_ids = [f"G-XLE{chr(65 + i)}" for i in range(NUM_PLANES)]
    feasible_routes = get_feasible_multi_leg_routes()
    feasible_destinations = [d for d in DESTINATIONS if is_feasible_return(d)]
    dest_list = feasible_destinations

    for day_offset in range(NUM_DAYS):
        current_date = START_DATE + timedelta(days=day_offset)

        for plane_idx, tail in enumerate(plane_ids):
            for shift_idx, shift_hour in enumerate(SHIFT_BASE_HOURS):
                duty_id = f"D{str(duty_counter).zfill(3)}"

                # Staggered departure: shift hour + plane-specific offset
                offset_min = PLANE_OFFSETS[plane_idx]
                dep_time = current_date.replace(hour=shift_hour, minute=0, second=0) + timedelta(
                    minutes=offset_min
                )

                # Decide duty type: ~40% multi-leg, 60% return trip
                is_multi_leg = random.random() < 0.4

                if is_multi_leg and feasible_routes:
                    # Pick a multi-leg route
                    route = random.choice(feasible_routes)
                    dest1, dest2 = route

                    # Leg 1: BASE → DEST1
                    dur1 = get_duration(BASE_AIRPORT, dest1)
                    arr1 = dep_time + timedelta(minutes=dur1)
                    flights.append(
                        {
                            "Flight Number": f"BA{flight_counter}",
                            "Duty ID": duty_id,
                            "Departure datetime": dep_time,
                            "Arrival datetime": arr1,
                            "Duration": str(dur1),
                            "Dep Airport": BASE_AIRPORT,
                            "Arr Airport": dest1,
                            "Tail Number": tail,
                        }
                    )
                    flight_counter += 1

                    # Leg 2: DEST1 → DEST2
                    dep2 = arr1 + timedelta(minutes=TURNAROUND_MINUTES)
                    dur2 = get_duration(dest1, dest2)
                    arr2 = dep2 + timedelta(minutes=dur2)
                    flights.append(
                        {
                            "Flight Number": f"BA{flight_counter}",
                            "Duty ID": duty_id,
                            "Departure datetime": dep2,
                            "Arrival datetime": arr2,
                            "Duration": str(dur2),
                            "Dep Airport": dest1,
                            "Arr Airport": dest2,
                            "Tail Number": tail,
                        }
                    )
                    flight_counter += 1

                    # Leg 3: DEST2 → BASE
                    dep3 = arr2 + timedelta(minutes=TURNAROUND_MINUTES)
                    dur3 = get_duration(dest2, BASE_AIRPORT)
                    arr3 = dep3 + timedelta(minutes=dur3)
                    flights.append(
                        {
                            "Flight Number": f"BA{flight_counter}",
                            "Duty ID": duty_id,
                            "Departure datetime": dep3,
                            "Arrival datetime": arr3,
                            "Duration": str(dur3),
                            "Dep Airport": dest2,
                            "Arr Airport": BASE_AIRPORT,
                            "Tail Number": tail,
                        }
                    )
                    flight_counter += 1
                else:
                    # Standard return trip: BASE → DEST → BASE
                    dest_idx = (plane_idx + shift_idx + day_offset) % len(dest_list)
                    destination = dest_list[dest_idx]
                    duration_min = DESTINATIONS[destination]

                    # Outbound: BASE → DESTINATION
                    arr_time = dep_time + timedelta(minutes=duration_min)
                    flights.append(
                        {
                            "Flight Number": f"BA{flight_counter}",
                            "Duty ID": duty_id,
                            "Departure datetime": dep_time,
                            "Arrival datetime": arr_time,
                            "Duration": str(duration_min),
                            "Dep Airport": BASE_AIRPORT,
                            "Arr Airport": destination,
                            "Tail Number": tail,
                        }
                    )
                    flight_counter += 1

                    # Return: DESTINATION → BASE
                    return_dep = arr_time + timedelta(minutes=TURNAROUND_MINUTES)
                    return_arr = return_dep + timedelta(minutes=duration_min)
                    flights.append(
                        {
                            "Flight Number": f"BA{flight_counter}",
                            "Duty ID": duty_id,
                            "Departure datetime": return_dep,
                            "Arrival datetime": return_arr,
                            "Duration": str(duration_min),
                            "Dep Airport": destination,
                            "Arr Airport": BASE_AIRPORT,
                            "Tail Number": tail,
                        }
                    )
                    flight_counter += 1

                duty_counter += 1

    return pd.DataFrame(flights)


def generate_crew(num_active_crew: int, num_standby_crew: int) -> pd.DataFrame:
    """Generate crew roster with active and standby crew."""
    crew: list[dict[str, str | int]] = []
    for i in range(num_active_crew):
        crew.append(
            {
                "Crew ID": f"C{str(i + 1).zfill(3)}",
                "Base": BASE_AIRPORT,
                "Status": "Available",
                "Years of Experience": random.randint(3, 15),
            }
        )
    for i in range(num_standby_crew):
        crew.append(
            {
                "Crew ID": f"C{str(num_active_crew + i + 1).zfill(3)}",
                "Base": BASE_AIRPORT,
                "Status": "Standby",
                "Years of Experience": random.randint(3, 15),
            }
        )
    return pd.DataFrame(crew)


def _to_string(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def build_structured_dataframes(flights_raw: pd.DataFrame, crew_raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build DFS-schema tables used by rostering and simulation."""
    flights = flights_raw.rename(
        columns={
            "Flight Number": "flight_no",
            "Duty ID": "duty_id",
            "Departure datetime": "departure_skd_ts",
            "Arrival datetime": "arrival_skd_ts",
            "Duration": "duration_min",
            "Dep Airport": "origin",
            "Arr Airport": "destination",
            "Tail Number": "tail_no",
        }
    ).copy()

    flights["flight_no"] = _to_string(flights["flight_no"])
    flights["duty_id"] = _to_string(flights["duty_id"])
    flights["origin"] = _to_string(flights["origin"])
    flights["destination"] = _to_string(flights["destination"])
    flights["tail_no"] = _to_string(flights["tail_no"])
    flights["duration_min"] = _to_int(flights["duration_min"])
    flights["departure_skd_ts"] = pd.to_datetime(flights["departure_skd_ts"], errors="coerce")
    flights["arrival_skd_ts"] = pd.to_datetime(flights["arrival_skd_ts"], errors="coerce")

    aircraft_lookup = (
        flights[["tail_no"]]
        .drop_duplicates()
        .reset_index(drop=True)
        .assign(aircraft_id=lambda d: d.index.map(lambda i: f"A{i + 1:03d}"))
    )
    aircraft_dim = aircraft_lookup[["aircraft_id", "tail_no"]].copy()
    aircraft_dim["aircraft_id"] = _to_string(aircraft_dim["aircraft_id"])
    aircraft_dim["tail_no"] = _to_string(aircraft_dim["tail_no"])

    sectors_dim = (
        flights.groupby(["origin", "destination"], dropna=False, as_index=False)["duration_min"]
        .median()
        .rename(columns={"duration_min": "base_duration_min"})
    )
    sectors_dim["sector_id"] = sectors_dim.index.map(lambda i: f"S{i + 1:03d}")
    sectors_dim = sectors_dim[["sector_id", "origin", "destination", "base_duration_min"]].copy()
    sectors_dim["sector_id"] = _to_string(sectors_dim["sector_id"])
    sectors_dim["origin"] = _to_string(sectors_dim["origin"])
    sectors_dim["destination"] = _to_string(sectors_dim["destination"])
    sectors_dim["base_duration_min"] = _to_int(sectors_dim["base_duration_min"])

    flight_schedule_fact = flights.merge(aircraft_dim, on="tail_no", how="left").merge(
        sectors_dim[["sector_id", "origin", "destination"]],
        on=["origin", "destination"],
        how="left",
    )
    flight_schedule_fact["flight_id"] = flight_schedule_fact.index.map(lambda i: f"F{i + 1:04d}")
    flight_schedule_fact["departure_est_ts"] = flight_schedule_fact["departure_skd_ts"]
    flight_schedule_fact["arrival_est_ts"] = flight_schedule_fact["arrival_skd_ts"]
    flight_schedule_fact["departure_act_ts"] = pd.NaT
    flight_schedule_fact["arrival_act_ts"] = pd.NaT
    flight_schedule_fact["status"] = "Scheduled"
    flight_schedule_fact["act_duration_min"] = flight_schedule_fact["duration_min"]
    flight_schedule_fact["est_delay_min"] = 0
    flight_schedule_fact["dep_delay_min"] = 0
    flight_schedule_fact["arr_delay_min"] = 0

    flight_schedule_fact = flight_schedule_fact[
        [
            "flight_id",
            "flight_no",
            "duty_id",
            "aircraft_id",
            "sector_id",
            "departure_skd_ts",
            "arrival_skd_ts",
            "departure_est_ts",
            "arrival_est_ts",
            "departure_act_ts",
            "arrival_act_ts",
            "status",
            "act_duration_min",
            "est_delay_min",
            "dep_delay_min",
            "arr_delay_min",
        ]
    ].copy()
    flight_schedule_fact["flight_id"] = _to_string(flight_schedule_fact["flight_id"])
    flight_schedule_fact["flight_no"] = _to_string(flight_schedule_fact["flight_no"])
    flight_schedule_fact["duty_id"] = _to_string(flight_schedule_fact["duty_id"])
    flight_schedule_fact["aircraft_id"] = _to_string(flight_schedule_fact["aircraft_id"])
    flight_schedule_fact["sector_id"] = _to_string(flight_schedule_fact["sector_id"])
    flight_schedule_fact["status"] = _to_string(flight_schedule_fact["status"])
    flight_schedule_fact["act_duration_min"] = _to_int(flight_schedule_fact["act_duration_min"])
    flight_schedule_fact["est_delay_min"] = _to_int(flight_schedule_fact["est_delay_min"])
    flight_schedule_fact["dep_delay_min"] = _to_int(flight_schedule_fact["dep_delay_min"])
    flight_schedule_fact["arr_delay_min"] = _to_int(flight_schedule_fact["arr_delay_min"])

    duty_fact = (
        flights.groupby("duty_id", as_index=False)
        .agg(duty_skd_start_ts=("departure_skd_ts", "min"), duty_skd_end_ts=("arrival_skd_ts", "max"))
        .sort_values("duty_id")
        .reset_index(drop=True)
    )
    duty_fact["duty_est_start_ts"] = duty_fact["duty_skd_start_ts"]
    duty_fact["duty_est_end_ts"] = duty_fact["duty_skd_end_ts"]
    duty_fact["duty_act_start_ts"] = pd.NaT
    duty_fact["duty_act_end_ts"] = pd.NaT
    duty_fact["duty_id"] = _to_string(duty_fact["duty_id"])

    crew_dim = crew_raw.rename(
        columns={
            "Crew ID": "crew_id",
            "Status": "status",
            "Years of Experience": "experience_yrs",
            "Base": "base_airport",
        }
    )[["crew_id", "status", "experience_yrs", "base_airport"]].copy()
    crew_dim["crew_id"] = _to_string(crew_dim["crew_id"])
    crew_dim["status"] = _to_string(crew_dim["status"])
    crew_dim["experience_yrs"] = _to_int(crew_dim["experience_yrs"])
    crew_dim["base_airport"] = _to_string(crew_dim["base_airport"])

    return {
        "flight_schedule_fact": flight_schedule_fact,
        "sectors_dim": sectors_dim,
        "duty_fact": duty_fact,
        "aircraft_dim": aircraft_dim,
        "crew_dim": crew_dim,
    }


def export_tables_to_excel(
    structured_tables: dict[str, pd.DataFrame],
    output_path: Path,
) -> Path:
    """Export tables to one workbook using fixed sheet names/order."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        structured_tables["flight_schedule_fact"].to_excel(writer, sheet_name="flight_schedule_fact", index=False)
        structured_tables["duty_fact"].to_excel(writer, sheet_name="duty_fact", index=False)
        structured_tables["sectors_dim"].to_excel(writer, sheet_name="sectors_dim", index=False)
        structured_tables["aircraft_dim"].to_excel(writer, sheet_name="aircraft_dim", index=False)
        structured_tables["crew_dim"].to_excel(writer, sheet_name="crew_dim", index=False)
    return output_path


def write_delta_tables(structured_tables: dict[str, pd.DataFrame]) -> None:
    """Write generated tables to Unity Catalog Delta tables.

    Only callable when pyspark is available. Guarded at the call site in
    main(), but also guards itself so it fails loudly rather than silently
    if invoked directly in an environment without pyspark.
    """
    if not HAS_PYSPARK:
        raise RuntimeError(
            "write_delta_tables() requires pyspark, which isn't available in "
            "this environment. Set EXPORT_TABLES_TO_DELTA = False when running "
            "locally, or run this on a Databricks cluster."
        )

    spark = SparkSession.getActiveSession()
    if spark is None:
        spark = SparkSession.builder.getOrCreate()

    for table_name, pdf in structured_tables.items():
        sdf = spark.createDataFrame(pdf)
        (
            sdf.write.format("delta")
            .mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(f"`{DELTA_CATALOG}`.{DELTA_SCHEMA}.{table_name}")
        )


def main(active_crew: int, standby_crew: int) -> None:
    """Generate schedule data and export tables."""
    flights_df = generate_flights()
    crew_df = generate_crew(active_crew, standby_crew)

    # Duty summary
    duty_flights = flights_df.groupby("Duty ID").size()
    multi_leg = (duty_flights == 3).sum()
    return_trip = (duty_flights == 2).sum()

    env_label = "Databricks" if IS_DATABRICKS else "local"
    print(f"Environment detected: {env_label} (pyspark available: {HAS_PYSPARK})")
    print(f"Generated {len(flights_df)} flights across {NUM_DAYS} days")
    print(f"  Planes: {NUM_PLANES} | Active crew: {active_crew} | Standby: {standby_crew} | Base: {BASE_AIRPORT}")
    print(f"  Destinations: {', '.join(DESTINATIONS.keys())}")
    print(f"  Duties: {flights_df['Duty ID'].nunique()} ({return_trip} return trips, {multi_leg} multi-leg)")

    # Duty hours breakdown
    print("\nDuty duration summary (flight time only, excl. buffers):")
    duty_spans = flights_df.groupby("Duty ID").agg(
        start=("Departure datetime", "min"),
        end=("Arrival datetime", "max"),
        legs=("Flight Number", "count"),
    )
    duty_spans["span_h"] = (duty_spans["end"] - duty_spans["start"]).dt.total_seconds() / 3600
    for legs in sorted(duty_spans["legs"].unique()):
        subset = duty_spans[duty_spans["legs"] == legs]
        print(
            f"  {legs}-leg duties: {len(subset)} | avg {subset['span_h'].mean():.1f}h | "
            f"range {subset['span_h'].min():.1f}\u2013{subset['span_h'].max():.1f}h"
        )

    structured = build_structured_dataframes(flights_df, crew_df)

    if EXPORT_TABLES_TO_DELTA:
        write_delta_tables(structured_tables=structured)
        print(f"\nExported tables to Unity Catalog Delta tables ({DELTA_CATALOG}.{DELTA_SCHEMA})")
    elif IS_DATABRICKS and not HAS_PYSPARK:
        # Shouldn't normally happen, but surfaces a clear message if it does
        print("\nRunning on Databricks but pyspark import failed — skipping Delta export.")

    if EXPORT_TABLES_TO_EXCEL:
        exported_path = export_tables_to_excel(
            structured_tables=structured,
            output_path=EXCEL_PATH,
        )
        print(f"Writing Excel file to: {exported_path.resolve()}")


if __name__ == "__main__":
    main(active_crew=NUM_ACTIVE_CREW, standby_crew=NUM_STANDBY_CREW)