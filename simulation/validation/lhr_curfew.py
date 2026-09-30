"""
LHR curfew validation.
"""


def check_lhr_curfew(
    assigned,
    curfew_hour,
    violations_found,
):
    """
    No departures from LHR after curfew.
    """

    print(
        f"\n[CHECK 6] NO DEPARTURES "
        f"FROM LHR AFTER "
        f"{curfew_hour:02d}:00"
    )

    print("-" * 40)

    violations = assigned[
        (assigned["Dep Airport"] == "LHR")
        &
        (
            assigned[
                "Actual Departure datetime"
            ].dt.hour >= curfew_hour
        )
    ]

    if violations.empty:

        print(
            "PASS - No departures from "
            "LHR after curfew"
        )

    else:

        violations_found = True

        print(
            f"FAIL - {len(violations)} "
            f"departure(s)"
        )

        for _, row in (
            violations
            .sort_values(
                "Actual Departure datetime"
            )
            .iterrows()
        ):

            print(
                f"    Flight "
                f"{row['Flight Number']} | "
                f"{row['Actual Departure datetime']}"
            )

    return violations_found