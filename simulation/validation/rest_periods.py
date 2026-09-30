"""
Minimum rest checks.
"""


def check_min_rest(
    duty_windows,
    pre_buffer,
    post_buffer,
    min_rest_hours,
    violations_found,
):
    """
    Check minimum rest between duties.
    """

    print(
        f"\n[CHECK 3] MIN_REST_HOURS >= "
        f"{min_rest_hours}h"
    )

    print("-" * 40)

    rest_violations = []

    for crew_id, crew_duties in (
        duty_windows.groupby("Crew ID")
    ):

        crew_duties = (
            crew_duties
            .sort_values("duty_start")
        )

        for i in range(
            1,
            len(crew_duties),
        ):

            prev_end = (
                crew_duties.iloc[i - 1]["duty_end"]
                + post_buffer
            )

            next_start = (
                crew_duties.iloc[i]["duty_start"]
                - pre_buffer
            )

            rest_hours = (
                next_start - prev_end
            ).total_seconds() / 3600

            if rest_hours < min_rest_hours:

                rest_violations.append(
                    {
                        "Crew ID": crew_id,
                        "Previous Duty":
                            crew_duties.iloc[i - 1]["Duty ID"],
                        "Next Duty":
                            crew_duties.iloc[i]["Duty ID"],
                        "Rest Hours":
                            rest_hours,
                    }
                )

    if not rest_violations:

        print(
            f"PASS - All crew have >= "
            f"{min_rest_hours}h rest "
            f"between duties"
        )

    else:

        violations_found = True

        print(
            f"FAIL - {len(rest_violations)} "
            f"violation(s):"
        )

        for v in rest_violations:

            print(
                f"    {v['Crew ID']} | "
                f"Duty {v['Previous Duty']} -> "
                f"{v['Next Duty']} | "
                f"{v['Rest Hours']:.2f}h rest"
            )

    return violations_found