"""Load and prepare simulation data from Excel."""

import pandas as pd


def load_data(file_path: str) -> dict:
    """
    Load flights, crew, and roster from Excel and prepare columns.

    Returns a dict with keys: flights, crew, roster_df
    """

    flights = pd.read_excel(file_path, sheet_name="Flights")
    crew = pd.read_excel(file_path, sheet_name="Crew")
    roster_df = pd.read_excel(file_path, sheet_name="Crew Rostering")

    # Remove any stale datetime columns
    stale_cols = [
        "Scheduled Departure datetime",
        "Scheduled Arrival datetime",
        "Actual Departure datetime",
        "Actual Arrival datetime",
    ]
    roster_cols = roster_df.columns
    for col in stale_cols:
        if col in roster_cols:
            roster_df.drop(columns=col, inplace=True)

    # Rebuild schedule columns from Flights
    roster_df = roster_df.merge(
        flights[["Flight Number", "Departure datetime", "Arrival datetime",
                 "Dep Airport", "Arr Airport", "Tail Number"]],
        on="Flight Number",
        how="left",
    )
    roster_df.rename(
        columns={
            "Departure datetime": "Scheduled Departure datetime",
            "Arrival datetime": "Scheduled Arrival datetime",
        },
        inplace=True,
    )

    # Parse datetimes
    flights["Departure datetime"] = pd.to_datetime(flights["Departure datetime"])
    flights["Arrival datetime"] = pd.to_datetime(flights["Arrival datetime"])

    # Initialise actual times (will be mutated during simulation)
    flights["Actual Departure datetime"] = flights["Departure datetime"]
    flights["Actual Arrival datetime"] = flights["Arrival datetime"]
    flights["Cancelled"] = 0

    roster_df["Actual Departure datetime"] = roster_df["Scheduled Departure datetime"]
    roster_df["Actual Arrival datetime"] = roster_df["Scheduled Arrival datetime"]
    roster_df["Flight Delayed"] = 0
    roster_df["Crew Change"] = 0
    roster_df["Flight Cancelled"] = 0

    return {
        "flights": flights,
        "crew": crew,
        "roster_df": roster_df,
    }
