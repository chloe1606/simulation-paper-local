"""
Rolling and cumulative duty hour checks.
"""

from datetime import timedelta


def check_28_day_limit(
    duty_windows,
    max_hours,
    violations_found,
):
    """
    Check total duty hours over the roster period.
    """

    print(
        f"\n[CHECK 2A] TOTAL DUTY HOURS <= "
        f"{max_hours}h (28-day horizon)"
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

    violations = crew_totals[
        crew_totals["total_duty_hours"] > max_hours
    ]

    if violations.empty:

        print(
            f"PASS - All crew within "
            f"{max_hours}h limit"
        )

        print(
            f"Maximum crew total = "
            f"{crew_totals['total_duty_hours'].max():.1f}h"
        )

    else:

        violations_found = True

        print(
            f"WARNING - {len(violations)} "
            f"violation(s):"
        )

        for _, row in violations.iterrows():

            print(
                f"    {row['Crew ID']} | "
                f"{row['total_duty_hours']:.1f}h "
                f"(exceeds by "
                f"{row['total_duty_hours'] - max_hours:.1f}h)"
            )

    return violations_found


def check_7_day_limit(
    duty_windows,
    max_hours,
    violations_found,
):
    """
    Check duty hours in any rolling 7-day period.
    """

    print(
        f"\n[CHECK 2B] TOTAL DUTY HOURS <= "
        f"{max_hours}h (any 7 consecutive days)"
    )

    print("-" * 40)

    violations = []

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

            if hours > max_hours:

                violations.append(
                    {
                        "Crew ID": crew_id,
                        "Hours": hours,
                    }
                )

    if not violations:

        print(
            f"PASS - No crew exceeded "
            f"{max_hours}h in any "
            f"7-day period"
        )

    else:

        violations_found = True

        print(
            f"WARNING - {len(violations)} "
            f"violation(s)"
        )

        for v in violations[:10]:

            print(
                f"    {v['Crew ID']} | "
                f"{v['Hours']:.1f}h"
            )

    return violations_found


def check_14_day_limit(
    duty_windows,
    max_hours,
    violations_found,
):
    """
    Check duty hours in any rolling 14-day period.
    """

    print(
        f"\n[CHECK 2C] TOTAL DUTY HOURS <= "
        f"{max_hours}h (any 14 consecutive days)"
    )

    print("-" * 40)

    violations = []

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

            if hours > max_hours:

                violations.append(
                    {
                        "Crew ID": crew_id,
                        "Hours": hours,
                    }
                )

    if not violations:

        print(
            f"PASS - No crew exceeded "
            f"{max_hours}h in any "
            f"14-day period"
        )

    else:

        violations_found = True

        print(
            f"WARNING - {len(violations)} "
            f"violation(s)"
        )

        for v in violations[:10]:

            print(
                f"    {v['Crew ID']} | "
                f"{v['Hours']:.1f}h"
            )

    return violations_found