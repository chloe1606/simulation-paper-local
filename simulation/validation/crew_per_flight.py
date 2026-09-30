"""
Crew complement validation.
"""


def check_crew_per_flight(
    assigned,
    pilots_per_duty,
    violations_found,
):
    """
    Check each flight has exactly the required
    number of crew assigned.
    """

    print(
        f"\n[CHECK 5] EXACTLY "
        f"{pilots_per_duty} CREW PER FLIGHT"
    )

    print("-" * 40)

    crew_count = (
        assigned.groupby("Flight Number")
        ["Crew ID"]
        .nunique()
        .reset_index(name="crew_count")
    )

    violations = crew_count[
        crew_count["crew_count"]
        != pilots_per_duty
    ]

    if violations.empty:

        print(
            f"PASS - All flights have exactly "
            f"{pilots_per_duty} crew assigned"
        )

    else:

        violations_found = True

        print(
            f"FAIL - {len(violations)} flight(s)"
        )

        for _, row in violations.iterrows():

            print(
                f"    Flight "
                f"{row['Flight Number']} | "
                f"{row['crew_count']} crew assigned"
            )

    return violations_found