"""SimulationEngine: runs delay/absence simulation using delta tables (Databricks) or an Excel workbook (local)."""

import os
import random
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import simpy

from .config import Config
from .models import Crew
from .flight_ops import (
    refresh_roster_actual_times,
    propagate_connection_delays,
    propagate_rest_delays,
    check_duty_overrun,
    check_excessive_delay,
    check_curfew,
)
from .crew_ops import add_unavailable_records

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

VERBOSE = False
# Delta export is opt-in and requires pyspark - only flip this on when
# actually running on Databricks. Locally it's simply unavailable.
SAVE_TO_DELTA = False
TO_BE_ARCHIVED = False
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The tables actually needed to build flights/crew/roster. (duty_fact isn't
# read here - every flight row already carries its own Duty ID - so it's
# intentionally left out of this list even though the source workbook/tables
# may include it.)
REQUIRED_TABLES = [
    "flight_schedule_fact",
    "sectors_dim",
    "aircraft_dim",
    "crew_dim",
    "crew_assignment_fact",
]


def _to_string(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def _to_pandas(df):
    """Convert a Spark DataFrame to pandas; pass pandas DataFrames through untouched."""
    if isinstance(df, pd.DataFrame):
        return df
    if HAS_PYSPARK and isinstance(df, SparkDataFrame):
        return df.toPandas()
    if hasattr(df, "toPandas"):
        # Duck-typed fallback in case the SparkDataFrame import path differs.
        return df.toPandas()
    return df


def _load_tables(delta_tables: dict | None = None, input_file: str | Path | None = None) -> dict[str, pd.DataFrame]:
    """Load the required delta tables from in-memory delta_tables (Spark or pandas -
    typically Databricks/Delta) or from an Excel workbook (typically local)."""

    if delta_tables is not None:
        missing = [table for table in REQUIRED_TABLES if table not in delta_tables]
        if missing:
            raise ValueError(f"Missing required tables: {missing}")
        return {name: _to_pandas(delta_tables[name]) for name in REQUIRED_TABLES}

    if input_file is None:
        raise ValueError(
            "No delta_tables provided and no input_file configured to load from "
            "(expected on Databricks - pass delta_tables directly from Delta tables)."
        )

    input_path = Path(input_file)
    workbook = pd.ExcelFile(input_path)
    missing = [table for table in REQUIRED_TABLES if table not in workbook.sheet_names]
    if missing:
        raise ValueError(f"Missing required sheets in {input_path}: {missing}")

    return {name: pd.read_excel(input_path, sheet_name=name) for name in REQUIRED_TABLES}


def load_data(delta_tables: dict | None = None, input_file: str | Path | None = None) -> dict:
    """Build runtime flights, crew and roster tables from delta tables (dict of
    Spark/pandas DataFrames) or an Excel workbook - whichever is provided."""

    tables = _load_tables(delta_tables=delta_tables, input_file=input_file)

    flight_schedule_fact = tables["flight_schedule_fact"]
    sectors_dim = tables["sectors_dim"]
    aircraft_dim = tables["aircraft_dim"]
    crew_dim = tables["crew_dim"]
    crew_assignment_fact = tables["crew_assignment_fact"]

    fs = flight_schedule_fact.merge(
        sectors_dim[["sector_id", "origin", "destination"]],
        on="sector_id",
        how="left",
    ).merge(
        aircraft_dim[["aircraft_id", "tail_no"]],
        on="aircraft_id",
        how="left",
    )

    flights = pd.DataFrame(
        {
            "Flight Number": _to_string(fs["flight_no"]),
            "Duty ID": _to_string(fs["duty_id"]),
            "Departure datetime": pd.to_datetime(fs["departure_skd_ts"], errors="coerce"),
            "Arrival datetime": pd.to_datetime(fs["arrival_skd_ts"], errors="coerce"),
            "Dep Airport": _to_string(fs["origin"]),
            "Arr Airport": _to_string(fs["destination"]),
            "Tail Number": _to_string(fs["tail_no"]),
        }
    )
    flights["Actual Departure datetime"] = flights["Departure datetime"]
    flights["Actual Arrival datetime"] = flights["Arrival datetime"]
    flights["Cancelled"] = 0

    crew = pd.DataFrame(
        {
            "Crew ID": _to_string(crew_dim["crew_id"]),
            "Base": _to_string(crew_dim["base_airport"]),
            "Status": _to_string(crew_dim["status"]),
            "Years of Experience": pd.to_numeric(crew_dim["experience_yrs"], errors="coerce").astype("Int64"),
        }
    )

    assignments = crew_assignment_fact.copy()
    assignments["crew_id"] = _to_string(assignments["crew_id"])
    assignments["duty_id"] = _to_string(assignments["duty_id"])
    assignments = assignments.rename(columns={"crew_id": "Crew ID", "duty_id": "Duty ID"})

    roster_df = assignments.merge(
        flights[
            [
                "Duty ID",
                "Flight Number",
                "Dep Airport",
                "Arr Airport",
                "Tail Number",
                "Departure datetime",
                "Arrival datetime",
            ]
        ],
        on="Duty ID",
        how="left",
    )
    roster_df = roster_df.rename(
        columns={
            "Departure datetime": "Scheduled Departure datetime",
            "Arrival datetime": "Scheduled Arrival datetime",
        }
    )

    roster_df["Role"] = "Pilot"
    roster_df["Assignment Status"] = "Assigned"
    roster_df["Actual Departure datetime"] = pd.to_datetime(roster_df["Scheduled Departure datetime"], errors="coerce")
    roster_df["Actual Arrival datetime"] = pd.to_datetime(roster_df["Scheduled Arrival datetime"], errors="coerce")
    roster_df["Flight Delayed"] = 0
    roster_df["Crew Change"] = 0
    roster_df["Flight Cancelled"] = 0

    if "assignment_id" in roster_df.columns:
        roster_df["assignment_id"] = _to_string(roster_df["assignment_id"])
    else:
        roster_df["assignment_id"] = pd.Series(pd.NA, index=roster_df.index, dtype="string")

    roster_df = roster_df[
        [
            "Crew ID",
            "Duty ID",
            "Flight Number",
            "Dep Airport",
            "Arr Airport",
            "Tail Number",
            "Scheduled Departure datetime",
            "Scheduled Arrival datetime",
            "Actual Departure datetime",
            "Actual Arrival datetime",
            "Role",
            "Assignment Status",
            "Flight Delayed",
            "Crew Change",
            "Flight Cancelled",
            "assignment_id",
        ]
    ].copy()

    roster_df["Crew ID"] = _to_string(roster_df["Crew ID"])
    roster_df["Duty ID"] = _to_string(roster_df["Duty ID"])
    roster_df["Flight Number"] = _to_string(roster_df["Flight Number"])
    roster_df.sort_values(["Scheduled Departure datetime", "Duty ID", "Crew ID"], inplace=True)
    roster_df.reset_index(drop=True, inplace=True)

    return {
        "flights": flights,
        "crew": crew,
        "roster_df": roster_df,
    }


class SimulationEngine:
    """Encapsulates simulation state so each run is independent."""

    def __init__(
        self,
        delta_tables: dict | None = None,
        seed=None,
        delay_prob=None,
        absence_prob=None,
        config: Config | None = None,
        input_file: str | Path | None = None,
    ):
        # Guard against the local calling convention `SimulationEngine(config)`,
        # where config is passed as the first positional argument. Since that
        # slot is normally `delta_tables` (the Databricks-style convention), a
        # bare Config instance would otherwise be mistaken for a tables dict.
        if isinstance(delta_tables, Config):
            config = delta_tables
            delta_tables = None

        self.config = config or Config()

        # seed / delay_prob / absence_prob can be overridden explicitly (e.g.
        # from a notebook sweeping values); otherwise fall back to config.
        self.seed = seed if seed is not None else self.config.seed
        self.delay_prob = delay_prob if delay_prob is not None else self.config.delay_probability
        self.absence_prob = absence_prob if absence_prob is not None else self.config.absence_probability

        if delta_tables is not None:
            data = load_data(delta_tables=delta_tables)
        else:
            if IS_DATABRICKS:
                raise ValueError(
                    "No delta_tables provided on Databricks - pass delta_tables directly "
                    "from Delta tables (there's no Excel input file to fall back to here)."
                )
            chosen_input = Path(input_file or self.config.input_file)
            if not chosen_input.is_absolute():
                chosen_input = PROJECT_ROOT / chosen_input
            data = load_data(input_file=chosen_input)

        self.flights = data["flights"]
        self.crew = data["crew"]
        self.roster_df = data["roster_df"]

        self.crew_pool = [Crew(row) for _, row in self.crew.iterrows()]

        standby = [c for c in self.crew_pool if c.is_standby]
        active = [c for c in self.crew_pool if not c.is_standby]
        self.replacement_queue = deque(standby + active)

        self.flight_schedule = self.flights.sort_values("Departure datetime")
        self.simulation_start = self.flights["Departure datetime"].min()

        self.duty_overrun_cancellations = 0
        self.excessive_delay_cancellations = 0
        self.curfew_cancellations = 0
        self.primary_delay_minutes = 0
        self.secondary_delay_minutes = 0
        self.total_delay_minutes = 0

        # === Search-time rejection counters ===
        self.num_rejections_6in9 = 0                   # candidate-level 6-in-9 rejections (find_replacement)
        self.flights_with_rolling_window_issues = 0    # flight-level: eligible candidate found only after
                                                        # passing over >=1 rolling-window rejection

        # === Successful-recovery stats (committed only after verify_minimum_crew confirms) ===
        self.crew_reassignments = 0                    # count of confirmed successful replacements
        self.reassigned_duties = set()                 # unique Duty IDs that got a confirmed replacement
        self.num_recoveries_by_reserve = 0             # of the above, how many used standby/reserve crew
        self.crew_found_delays = []                    # delay_hours for every confirmed resolution (incl. 0h)
                                                        # -> feeds compute_avg_time_before_crew_found()
        self.assignment_delays = []                    # delay_hours only where > 0
                                                        # -> feeds compute_avg_assignment_delay()

        # === Uncovered-duty reason counters (contributing-factor style - NOT mutually exclusive
        #     a single duty can increment more than one of these) ===
        self.uncovered_due_to_rest = 0
        self.uncovered_due_to_no_reserve = 0
        self.uncovered_due_to_absence = 0
        self.uncovered_due_to_duty_limit = 0
        self.uncovered_due_to_rolling_window = 0
        self.uncovered_due_to_connection = 0
        self.uncovered_due_to_airport_mismatch = 0
        self.uncovered_due_to_schedule_conflict = 0
        self.uncovered_due_to_curfew = 0
        self.uncovered_due_to_excessive_delay = 0
        self.uncovered_due_to_duty_overrun = 0
        self.uncovered_due_to_delay_limit = 0           # fallback only - should stay 0 in practice
        self.uncovered_due_to_insufficient_crew = 0

        # === Mutually-exclusive top-line breakdown (sums to 100% of uncovered duties) ===
        self.primary_uncovered_reasons = {}             # dict: reason string -> count, populated lazily

    def sim_time_to_datetime(self, env):
        return self.simulation_start + timedelta(hours=env.now)

    def _sample_delay(self):
        cfg = self.config
        raw = random.gauss(cfg.delay_mean_minutes, cfg.delay_std_minutes)
        clamped = max(cfg.delay_min_minutes, min(cfg.delay_max_minutes, raw))
        rounded = round(clamped / cfg.delay_step_minutes) * cfg.delay_step_minutes
        return int(rounded)

    def _check_overrun(self, duty_id):
        cancelled = check_duty_overrun(self.flights, self.roster_df, duty_id, self.config)
        self.duty_overrun_cancellations += cancelled

    def _check_excessive_delay(self, duty_id):
        cancelled = check_excessive_delay(self.flights, self.roster_df, duty_id, self.config)
        self.excessive_delay_cancellations += cancelled

    def _check_curfew(self, duty_id):
        cancelled = check_curfew(self.flights, self.roster_df, duty_id, self.config)
        self.curfew_cancellations += cancelled

    def _absence_process(self, env, crew_member, absence_prob):
        while True:
            yield env.timeout(24)

            current_dt = self.sim_time_to_datetime(env)
            p_absent = absence_prob

            if random.random() < p_absent:
                crew_member.absent = True

                if VERBOSE:
                    print(f"[SICK] {current_dt:%Y-%m-%d %H:%M} | {crew_member.id} became unavailable")

                add_unavailable_records(
                    crew_member,
                    current_dt,
                    self.flights,
                    self.roster_df,
                    self.replacement_queue,
                    self.config,
                    self,
                )

                sick_duration = random.choice(self.config.sick_duration_options)
                yield env.timeout(sick_duration)

                crew_member.absent = False
                if VERBOSE:
                    print(
                        f"[RETURN] {self.sim_time_to_datetime(env):%Y-%m-%d %H:%M} | "
                        f"{crew_member.id} returned to service"
                    )

    def _delay_process(self, env):
        previous_hours = 0

        for _, flight in self.flight_schedule.iterrows():
            departure_hours = (flight["Departure datetime"] - self.simulation_start).total_seconds() / 3600

            yield env.timeout(max(0, departure_hours - previous_hours))
            previous_hours = departure_hours

            if random.random() < self.delay_prob:
                delay = self._sample_delay()

                self.primary_delay_minutes += delay

                flight_number = flight["Flight Number"]
                duty_id = flight["Duty ID"]
                idx = self.flights[self.flights["Flight Number"] == flight_number].index

                if self.flights.loc[idx, "Cancelled"].iloc[0] == 1:
                    continue

                self.flights.loc[idx, "Actual Departure datetime"] += timedelta(minutes=delay)
                self.flights.loc[idx, "Actual Arrival datetime"] += timedelta(minutes=delay)

                refresh_roster_actual_times(self.flights, self.roster_df)
                self.roster_df.loc[self.roster_df["Flight Number"] == flight_number, "Flight Delayed"] = 1
                if VERBOSE:
                    print(
                        f"[DELAY] {flight['Departure datetime']:%Y-%m-%d %H:%M} | "
                        f"{flight_number} delayed by {delay} mins | reason: random delay"
                    )

                propagate_connection_delays(self.flights, self.roster_df, flight_number, self.config, self)
                propagate_rest_delays(self.flights, self.roster_df, duty_id, self.config, self)

                self._check_overrun(duty_id)
                self._check_excessive_delay(duty_id)
                self._check_curfew(duty_id)

    def run(self):
        random.seed(self.seed)

        env = simpy.Environment()

        for crew_member in self.crew_pool:
            env.process(self._absence_process(env, crew_member, self.absence_prob))

        env.process(self._delay_process(env))

        simulation_end = (
            (self.flights["Actual Arrival datetime"].max() - self.simulation_start).total_seconds() / 3600
        ) + 72

        env.run(until=simulation_end)

        total_cancelled = (self.flights["Cancelled"] == 1).sum()
        total_delay_minutes = self.primary_delay_minutes + self.secondary_delay_minutes
        self.total_delay_minutes = total_delay_minutes

        if VERBOSE:
            print(f"\nSimulation complete. Total roster records: {len(self.roster_df)}")
            if self.duty_overrun_cancellations > 0:
                print(f"  Duty overrun cancellations: {self.duty_overrun_cancellations} flights")
            if self.excessive_delay_cancellations > 0:
                print(f"  Excessive delay cancellations: {self.excessive_delay_cancellations} flights")
            if self.curfew_cancellations > 0:
                print(f"  Curfew cancellations: {self.curfew_cancellations} flights")
            if total_cancelled > 0:
                print(f"  Total cancelled: {total_cancelled} flights")

    def _build_export_tables(self) -> dict[str, pd.DataFrame]:
        """Build delta-style export tables from simulated state."""

        flights = self.flights.copy().sort_values(["Departure datetime", "Flight Number"]).reset_index(drop=True)
        flights["Flight Number"] = _to_string(flights["Flight Number"])
        flights["Duty ID"] = _to_string(flights["Duty ID"])
        flights["Dep Airport"] = _to_string(flights["Dep Airport"])
        flights["Arr Airport"] = _to_string(flights["Arr Airport"])
        flights["Tail Number"] = _to_string(flights["Tail Number"])

        aircraft_dim = (
            flights[["Tail Number"]]
            .drop_duplicates()
            .sort_values("Tail Number")
            .reset_index(drop=True)
            .rename(columns={"Tail Number": "tail_no"})
        )
        aircraft_dim["aircraft_id"] = aircraft_dim.index.map(lambda i: f"A{i + 1:03d}")
        aircraft_dim = aircraft_dim[["aircraft_id", "tail_no"]]
        aircraft_dim["aircraft_id"] = _to_string(aircraft_dim["aircraft_id"])
        aircraft_dim["tail_no"] = _to_string(aircraft_dim["tail_no"])

        flight_durations = (
            pd.to_datetime(flights["Arrival datetime"]) - pd.to_datetime(flights["Departure datetime"])
        ).dt.total_seconds() / 60
        sectors_dim = (
            flights.assign(_duration_min=flight_durations)
            .groupby(["Dep Airport", "Arr Airport"], dropna=False, as_index=False)["_duration_min"]
            .median()
            .rename(
                columns={
                    "Dep Airport": "origin",
                    "Arr Airport": "destination",
                    "_duration_min": "base_duration_min",
                }
            )
            .sort_values(["origin", "destination"])
            .reset_index(drop=True)
        )
        sectors_dim["sector_id"] = sectors_dim.index.map(lambda i: f"S{i + 1:03d}")
        sectors_dim = sectors_dim[["sector_id", "origin", "destination", "base_duration_min"]]
        sectors_dim["sector_id"] = _to_string(sectors_dim["sector_id"])
        sectors_dim["origin"] = _to_string(sectors_dim["origin"])
        sectors_dim["destination"] = _to_string(sectors_dim["destination"])
        sectors_dim["base_duration_min"] = _to_int(sectors_dim["base_duration_min"])

        flights_export = flights.merge(
            aircraft_dim.rename(columns={"tail_no": "Tail Number"}),
            on="Tail Number",
            how="left",
        ).merge(
            sectors_dim.rename(columns={"origin": "Dep Airport", "destination": "Arr Airport"})[
                ["sector_id", "Dep Airport", "Arr Airport"]
            ],
            on=["Dep Airport", "Arr Airport"],
            how="left",
        )

        dep_delay_min = (
            pd.to_datetime(flights_export["Actual Departure datetime"])
            - pd.to_datetime(flights_export["Departure datetime"])
        ).dt.total_seconds() / 60
        arr_delay_min = (
            pd.to_datetime(flights_export["Actual Arrival datetime"])
            - pd.to_datetime(flights_export["Arrival datetime"])
        ).dt.total_seconds() / 60
        act_duration_min = (
            pd.to_datetime(flights_export["Actual Arrival datetime"])
            - pd.to_datetime(flights_export["Actual Departure datetime"])
        ).dt.total_seconds() / 60

        flight_schedule_fact = pd.DataFrame(
            {
                "flight_id": flights_export.index.map(lambda i: f"F{i + 1:04d}"),
                "flight_no": _to_string(flights_export["Flight Number"]),
                "duty_id": _to_string(flights_export["Duty ID"]),
                "aircraft_id": _to_string(flights_export["aircraft_id"]),
                "sector_id": _to_string(flights_export["sector_id"]),
                "departure_skd_ts": pd.to_datetime(flights_export["Departure datetime"], errors="coerce"),
                "arrival_skd_ts": pd.to_datetime(flights_export["Arrival datetime"], errors="coerce"),
                "departure_est_ts": pd.to_datetime(flights_export["Actual Departure datetime"], errors="coerce"),
                "arrival_est_ts": pd.to_datetime(flights_export["Actual Arrival datetime"], errors="coerce"),
                "departure_act_ts": pd.to_datetime(flights_export["Actual Departure datetime"], errors="coerce"),
                "arrival_act_ts": pd.to_datetime(flights_export["Actual Arrival datetime"], errors="coerce"),
                "status": (
                    flights_export["Cancelled"].eq(1).map({True: "Cancelled", False: "Operating"})
                ),
                "act_duration_min": _to_int(act_duration_min),
                "est_delay_min": _to_int(dep_delay_min),
                "dep_delay_min": _to_int(dep_delay_min),
                "arr_delay_min": _to_int(arr_delay_min),
            }
        )
        flight_schedule_fact["flight_id"] = _to_string(flight_schedule_fact["flight_id"])
        flight_schedule_fact["status"] = _to_string(flight_schedule_fact["status"])

        duty_fact = (
            flight_schedule_fact.groupby("duty_id", as_index=False)
            .agg(
                duty_skd_start_ts=("departure_skd_ts", "min"),
                duty_skd_end_ts=("arrival_skd_ts", "max"),
                duty_est_start_ts=("departure_est_ts", "min"),
                duty_est_end_ts=("arrival_est_ts", "max"),
                duty_act_start_ts=("departure_act_ts", "min"),
                duty_act_end_ts=("arrival_act_ts", "max"),
            )
            .sort_values("duty_id")
            .reset_index(drop=True)
        )
        duty_fact["duty_id"] = _to_string(duty_fact["duty_id"])

        crew_dim = self.crew.rename(
            columns={
                "Crew ID": "crew_id",
                "Status": "status",
                "Years of Experience": "experience_yrs",
                "Base": "base_airport",
            }
        )[["crew_id", "status", "experience_yrs", "base_airport"]].copy()
        crew_dim["crew_id"] = _to_string(crew_dim["crew_id"])
        crew_dim["status"] = _to_string(crew_dim["status"])
        crew_dim["base_airport"] = _to_string(crew_dim["base_airport"])
        crew_dim["experience_yrs"] = _to_int(crew_dim["experience_yrs"])

        assigned = self.roster_df[self.roster_df["Assignment Status"] == "Assigned"].copy()
        crew_assignment_fact = (
            assigned[["assignment_id", "Crew ID", "Duty ID"]]
            .drop_duplicates()
            .rename(columns={"Crew ID": "crew_id", "Duty ID": "duty_id"})
            .reset_index(drop=True)
        )

        missing_ids = crew_assignment_fact["assignment_id"].isna() | crew_assignment_fact["assignment_id"].eq("")
        if missing_ids.any():
            start_idx = 1
            generated = [f"ASGPOST{i:06d}" for i in range(start_idx, start_idx + int(missing_ids.sum()))]
            crew_assignment_fact.loc[missing_ids, "assignment_id"] = generated

        duty_duration = duty_fact.set_index("duty_id")
        duty_skd_duration_min = (
            duty_duration["duty_skd_end_ts"] - duty_duration["duty_skd_start_ts"]
        ).dt.total_seconds() / 60
        duty_est_duration_min = (
            duty_duration["duty_est_end_ts"] - duty_duration["duty_est_start_ts"]
        ).dt.total_seconds() / 60
        duty_act_duration_min = (
            duty_duration["duty_act_end_ts"] - duty_duration["duty_act_start_ts"]
        ).dt.total_seconds() / 60

        crew_assignment_fact["duty_skd_duration_min"] = _to_int(
            crew_assignment_fact["duty_id"].map(duty_skd_duration_min)
        )
        crew_assignment_fact["duty_est_duration_min"] = _to_int(
            crew_assignment_fact["duty_id"].map(duty_est_duration_min)
        )
        crew_assignment_fact["duty_act_duration_min"] = _to_int(
            crew_assignment_fact["duty_id"].map(duty_act_duration_min)
        )

        crew_assignment_fact = crew_assignment_fact[
            [
                "assignment_id",
                "crew_id",
                "duty_id",
                "duty_skd_duration_min",
                "duty_est_duration_min",
                "duty_act_duration_min",
            ]
        ].copy()
        crew_assignment_fact["assignment_id"] = _to_string(crew_assignment_fact["assignment_id"])
        crew_assignment_fact["crew_id"] = _to_string(crew_assignment_fact["crew_id"])
        crew_assignment_fact["duty_id"] = _to_string(crew_assignment_fact["duty_id"])

        return {
            "flight_schedule_fact": flight_schedule_fact,
            "duty_fact": duty_fact,
            "sectors_dim": sectors_dim,
            "aircraft_dim": aircraft_dim,
            "crew_dim": crew_dim,
            "crew_assignment_fact": crew_assignment_fact,
        }

    def _save_delta_table(self, tables):
        if not HAS_PYSPARK:
            raise RuntimeError(
                "_save_delta_table() requires pyspark, which isn't available in this "
                "environment. Set SAVE_TO_DELTA = False when running locally, or run "
                "this on a Databricks cluster."
            )

        catalog = "ctl-sandbox-dev"
        schema = "simulation_paper"

        timestamp = pd.Timestamp.now().strftime("%Y%m%d%H%M%S")

        spark = SparkSession.getActiveSession()
        if spark is None:
            spark = SparkSession.builder.getOrCreate()

        for table_name, pdf in tables.items():
            # Only export fact tables
            if "_fact" not in table_name:
                continue

            if TO_BE_ARCHIVED:
                target_table = f"`{catalog}`.{schema}.{timestamp}_post_sim_{table_name}"
            else:
                target_table = f"`{catalog}`.{schema}.{table_name}"

            sdf = spark.createDataFrame(pdf)
            (
                sdf.write.format("delta")
                .mode("overwrite")
                .option("overwriteSchema", "true")
                .saveAsTable(target_table)
            )

            row_count = sdf.count()
            if VERBOSE:
                print(f"Written {row_count:,} rows to {target_table}")

    def save(self):
        # Output filename is timestamp-distinguished rather than driven by
        # config, so repeated runs never clobber each other's results.
        output_path = PROJECT_ROOT / "output" / f"simulation_results_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        tables = self._build_export_tables()

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            tables["flight_schedule_fact"].to_excel(writer, sheet_name="flight_schedule_fact", index=False)
            tables["duty_fact"].to_excel(writer, sheet_name="duty_fact", index=False)
            tables["sectors_dim"].to_excel(writer, sheet_name="sectors_dim", index=False)
            tables["aircraft_dim"].to_excel(writer, sheet_name="aircraft_dim", index=False)
            tables["crew_dim"].to_excel(writer, sheet_name="crew_dim", index=False)
            tables["crew_assignment_fact"].to_excel(writer, sheet_name="crew_assignment_fact", index=False)

        if VERBOSE:
            print(f"\n{output_path} saved successfully")

        if SAVE_TO_DELTA:
            self._save_delta_table(tables)