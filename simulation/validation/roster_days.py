"""
Roster work pattern validation.
"""
from datetime import timedelta

import pandas as pd


def check_roster_days(
    assigned,
    max_days_on,
    window_days,
    violations_found,
):
    """
    Maximum days worked in a rolling window.
    """

    print(
        f"\n[CHECK 7] MAX "
        f"{max_days_on} WORK DAYS "
        f"IN ANY {window_days}-DAY WINDOW"
    )

    print("-" * 40)

    workday_violations = []

    crew_workdays = (
        assigned.assign(
            work_day=assigned[
                "Actual Departure datetime"
            ].dt.floor("D")
        )
        .groupby("Crew ID")
        ["work_day"]
        .unique()
    )

    for crew_id, days in crew_workdays.items():

        days = sorted(
            pd.to_datetime(days)
        )

        for start_day in days:

            end_day = (
                start_day
                + timedelta(days=window_days)
            )

            days_worked = sum(
                1
                for day in days
                if start_day <= day < end_day
            )

            if days_worked > max_days_on:

                workday_violations.append(
                    {
                        "Crew ID": crew_id,
                        "Start Day": start_day,
                        "Days Worked": days_worked,
                    }
                )

                break

    if not workday_violations:

        print(
            f"PASS - No crew worked more than "
            f"{max_days_on} days in any "
            f"{window_days}-day window"
        )

    else:

        violations_found = True

        print(
            f"FAIL - "
            f"{len(workday_violations)} "
            f"crew violation(s)"
        )

        for v in workday_violations:

            print(
                f"    {v['Crew ID']} | "
                f"{v['Days Worked']} days worked"
            )
        
        for v in workday_violations:

            print(
                f"\nCrew: {v['Crew ID']}"
            )

            crew_days = (
                assigned[
                    assigned["Crew ID"]
                    == v["Crew ID"]
                ]["Actual Departure datetime"]
                .dt.floor("D")
                .drop_duplicates()
                .sort_values()
            )

            print(crew_days.tolist())

    return violations_found