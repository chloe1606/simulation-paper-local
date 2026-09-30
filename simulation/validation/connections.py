"""
Connection time validation checks.
"""


def check_connections(
    assigned,
    min_connection_minutes,
    violations_found,
):
    """
    Check minimum connection times between flights.
    """

    print(
        f"\n[CHECK 4] MIN_CONNECTION_MINUTES >= "
        f"{min_connection_minutes}min"
    )

    print("-" * 40)

    connection_violations = []

    for (crew_id, duty_id), group in (
        assigned.groupby(
            ["Crew ID", "Duty ID"]
        )
    ):

        group = group.sort_values(
            "Actual Departure datetime"
        )

        for i in range(
            1,
            len(group),
        ):

            prev_arr = (
                group.iloc[i - 1]
                ["Actual Arrival datetime"]
            )

            next_dep = (
                group.iloc[i]
                ["Actual Departure datetime"]
            )

            connection_min = (
                next_dep - prev_arr
            ).total_seconds() / 60

            if (
                connection_min
                < min_connection_minutes
            ):

                connection_violations.append(
                    {
                        "Crew ID": crew_id,
                        "Duty ID": duty_id,
                        "From Flight":
                            group.iloc[i - 1]["Flight Number"],
                        "To Flight":
                            group.iloc[i]["Flight Number"],
                        "Connection Min":
                            connection_min,
                    }
                )

    if not connection_violations:

        print(
            f"PASS - All connections >= "
            f"{min_connection_minutes}min"
        )

    else:

        violations_found = True

        print(
            f"FAIL - "
            f"{len(connection_violations)} "
            f"violation(s):"
        )

        for v in connection_violations:

            print(
                f"    {v['Crew ID']} | "
                f"Duty {v['Duty ID']} | "
                f"{v['From Flight']} -> "
                f"{v['To Flight']} | "
                f"{v['Connection Min']:.0f}min"
            )

    return violations_found