"""
Duty hour validation checks.
"""

def check_max_duty_hours(
    duty_windows,
    max_duty_hours,
    violations_found,
):
    """
    Check no duty exceeds the maximum duty hours.
    """

    print(
        f"\n[CHECK 1] MAX_DUTY_HOURS <= {max_duty_hours}h"
    )

    print("-" * 40)

    duty_violations = duty_windows[
        duty_windows["duty_hours"] > max_duty_hours
    ]

    if duty_violations.empty:

        print(
            f"PASS - All {len(duty_windows)} duties "
            f"within {max_duty_hours}h limit"
        )

    else:

        violations_found = True

        print(
            f"FAIL - {len(duty_violations)} "
            f"violation(s):"
        )

        for _, row in duty_violations.iterrows():

            print(
                f"    {row['Crew ID']} | "
                f"Duty {row['Duty ID']} | "
                f"{row['duty_hours']:.2f}h "
                f"(exceeds by "
                f"{row['duty_hours'] - max_duty_hours:.2f}h)"
            )

    return violations_found