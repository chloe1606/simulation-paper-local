"""Crew replacement logic: find replacements, validate rules, handle unavailability.

Sections:
    1. Rule validation          - passes_6_in_9_rule, passes_duty_and_rest_rules
    2. Metrics helpers          - derived stats + uncovered-reason recording
    3. Replacement search       - find_replacement
    4. Unavailability handling  - add_unavailable_records
"""

import pandas as pd
from datetime import timedelta

from .flight_ops import (
    apply_flight_delay_hours,
    propagate_rest_delays,
    cancel_flight,
    verify_minimum_crew,
)


# ============================================================
# 1. RULE VALIDATION
# ============================================================

def passes_6_in_9_rule(candidate_flights, flight_row, config):
    """Ensure crew do not work more than config.max_work_days_in_window days in any
    config.rolling_window_days rolling window."""

    work_days = set()
    if not candidate_flights.empty:
        work_days.update(
            pd.to_datetime(candidate_flights["Actual Departure datetime"]).dt.floor("D").tolist()
        )

    proposed_day = pd.to_datetime(flight_row["Actual Departure datetime"]).floor("D")
    work_days.add(proposed_day)
    work_days = sorted(work_days)

    for start_day in work_days:
        end_day = start_day + timedelta(days=config.rolling_window_days)
        days_worked = sum(1 for day in work_days if start_day <= day < end_day)
        if days_worked > config.max_work_days_in_window:
            return False, f"worked {days_worked} days in {config.rolling_window_days}-day window"

    return True, "ok"


def passes_duty_and_rest_rules(candidate_flights, flight_row, candidate_id, config):
    """Validate a hypothetical assignment against:
    - connection rules
    - duty limits
    - rest limits
    - max days worked in rolling window
    """

    sim = candidate_flights[
        [
            "Crew ID",
            "Duty ID",
            "Flight Number",
            "Tail Number",
            "Actual Departure datetime",
            "Actual Arrival datetime",
        ]
    ].copy()

    sim.loc[len(sim)] = {
        "Crew ID": candidate_id,
        "Duty ID": flight_row["Duty ID"],
        "Flight Number": flight_row["Flight Number"],
        "Tail Number": flight_row["Tail Number"],
        "Actual Departure datetime": pd.to_datetime(flight_row["Actual Departure datetime"]),
        "Actual Arrival datetime": pd.to_datetime(flight_row["Actual Arrival datetime"]),
    }
    sim = sim.drop_duplicates(subset=["Flight Number"])

    # --- Connection check ---
    sim_sorted = sim.sort_values("Actual Departure datetime").reset_index(drop=True)

    for i in range(1, len(sim_sorted)):
        same_crew = sim_sorted.loc[i - 1, "Crew ID"] == sim_sorted.loc[i, "Crew ID"]
        same_tail = sim_sorted.loc[i - 1, "Tail Number"] == sim_sorted.loc[i, "Tail Number"]
        if not (same_crew or same_tail):
            continue

        prev_arr = pd.to_datetime(sim_sorted.loc[i - 1, "Actual Arrival datetime"])
        next_dep = pd.to_datetime(sim_sorted.loc[i, "Actual Departure datetime"])
        conn_min = (next_dep - prev_arr).total_seconds() / 60

        if conn_min < config.min_connection_minutes:
            return False, f"connection {conn_min:.0f}m < {config.min_connection_minutes}m", {}

    # --- Duty hours check ---
    pre_buffer = timedelta(minutes=config.pre_duty_minutes)
    post_buffer = timedelta(minutes=config.post_duty_minutes)

    duty_windows = (
        sim.groupby("Duty ID", dropna=True)
        .agg(
            duty_start=("Actual Departure datetime", "min"),
            duty_end=("Actual Arrival datetime", "max"),
        )
        .sort_values("duty_start")
    )

    target_duty = flight_row["Duty ID"]
    if target_duty in duty_windows.index:
        target = duty_windows.loc[target_duty]
        duty_hours = (
            (target["duty_end"] + post_buffer) - (target["duty_start"] - pre_buffer)
        ).total_seconds() / 3600

        if duty_hours > config.max_duty_hours:
            return False, f"duty {duty_hours:.2f}h > {config.max_duty_hours}h", {}

    # --- Rest check ---
    for i in range(1, len(duty_windows)):
        prev_end = duty_windows.iloc[i - 1]["duty_end"] + post_buffer
        next_start = duty_windows.iloc[i]["duty_start"] - pre_buffer
        rest_hours = (next_start - prev_end).total_seconds() / 3600

        if rest_hours < config.min_rest_hours:
            return False, f"rest {rest_hours:.2f}h < {config.min_rest_hours}h", {"rest_hours": rest_hours}

    # --- Rolling 6-in-9 check ---
    passes_window_rule, reason = passes_6_in_9_rule(candidate_flights, flight_row, config)
    if not passes_window_rule:
        return False, reason, {"rule": "rolling_window"}

    return True, "ok", {}


# ============================================================
# 2. METRICS HELPERS
# ============================================================

# Maps an internal rejection category (collected during find_replacement) to
# the engine attribute it feeds. Extend this dict - and the categorization
# logic inside find_replacement - if new uncovered-reason categories are
# ever needed.
UNCOVERED_REASON_ATTRS = {
    "rest": "uncovered_due_to_rest",
    "no_reserve": "uncovered_due_to_no_reserve",
    "duty_limit": "uncovered_due_to_duty_limit",
    "rolling_window": "uncovered_due_to_rolling_window",
    "absence": "uncovered_due_to_absence",
    "connection": "uncovered_due_to_connection",
    "airport_mismatch": "uncovered_due_to_airport_mismatch",
    "schedule_conflict": "uncovered_due_to_schedule_conflict",
    "curfew": "uncovered_due_to_curfew",
    "excessive_delay": "uncovered_due_to_excessive_delay",
    "duty_overrun": "uncovered_due_to_duty_overrun",
    "delay_limit": "uncovered_due_to_delay_limit",
    "insufficient_crew": "uncovered_due_to_insufficient_crew",
}

# flight_ops.py's check_curfew / check_excessive_delay / check_duty_overrun each
# write one of these exact Assignment Status strings when they cancel a flight.
# Reading the status back lets us attribute the precise cause instead of
# lumping every post-delay cancellation into one generic bucket. Any status
# NOT in this table (e.g. a future check function) falls back to the generic
# "delay_limit" category rather than being silently dropped.
STATUS_TO_DELAY_CATEGORY = {
    "Cancelled - Curfew": "curfew",
    "Cancelled - Excessive Delay": "excessive_delay",
    "Cancelled - Duty Overrun": "duty_overrun",
}

# Priority order used to pick a single "primary" reason per uncovered duty,
# from most to least fundamental. A duty can fail for several reasons at
# once (see UNCOVERED_REASON_ATTRS, which counts all of them); this list
# instead answers "if we had to name ONE cause, which is it?" so that a
# top-line breakdown sums to the total number of uncovered duties.
#
# Rationale for the ordering:
#   1. no_reserve         - nobody was even in play; the most fundamental gap.
#   2. absence            - crew existed on paper but were unavailable.
#   3. insufficient_crew  - a replacement WAS found, but the flight still
#                           didn't meet the minimum pilot count overall; a
#                           supply-side gap discovered only at the final
#                           checkpoint (verify_minimum_crew), so it's grouped
#                           with the other "not enough people" reasons.
#   4. duty_limit         - hard regulatory ceiling; delaying only makes it worse.
#   5. rolling_window     - also a hard regulatory cap, but depends on the
#                           crew member's multi-day history rather than just
#                           this one duty, so it's ranked just below duty_limit.
#   6. duty_overrun       - the rest-delay itself pushed the ACTUAL flight past
#                           the duty-hours limit; a real, already-realized
#                           regulatory breach, ranked just below the candidate-
#                           time duty_limit/rolling_window checks.
#   7. curfew             - operational hard-stop (aircraft can't legally move);
#                           severe, but airport/time-of-day specific rather
#                           than a crew regulation.
#   8. excessive_delay    - a business/ops policy cutoff rather than a
#                           regulation - the softest of the three delay-
#                           triggered cancellations.
#   9. delay_limit        - generic fallback for any post-delay cancellation
#                           whose Assignment Status doesn't match a known
#                           curfew/excessive-delay/duty-overrun string.
#  10. connection         - could in principle be solved by re-sequencing/tails.
#  11. airport_mismatch   - could in principle be solved by deadheading crew in.
#  12. schedule_conflict  - could in principle be solved by a cascading reassignment.
#  13. rest               - ranked last on purpose: the system already tries to
#                           resolve this automatically via delay, so if "rest"
#                           is still the answer, no harder constraint was found.
UNCOVERED_REASON_PRIORITY = [
    "no_reserve",
    "absence",
    "insufficient_crew",
    "duty_limit",
    "rolling_window",
    "duty_overrun",
    "curfew",
    "excessive_delay",
    "delay_limit",
    "connection",
    "airport_mismatch",
    "schedule_conflict",
    "rest",
]


def pick_primary_uncovered_reason(categories):
    """Choose the single most fundamental reason from a set of contributing
    categories, per UNCOVERED_REASON_PRIORITY. Returns None if categories is
    empty (shouldn't happen in practice, but callers should guard for it)."""
    for reason in UNCOVERED_REASON_PRIORITY:
        if reason in categories:
            return reason
    return None


def record_uncovered_reasons(engine, categories):
    """Record why a duty ended up with no replacement, two ways:

    1. Contributing-factor counters (uncovered_due_to_*) - one increment per
       category that played a role. A single duty can increment several of
       these, so they should NOT be expected to sum to the total number of
       uncovered duties, and should not be used as a percentage denominator
       against each other.
    2. A single "primary" reason (engine.primary_uncovered_reasons), chosen
       via UNCOVERED_REASON_PRIORITY, so you also have a mutually-exclusive
       breakdown that sums to 100% of uncovered duties for top-line reporting.
    """
    for category in categories:
        attr = UNCOVERED_REASON_ATTRS.get(category)
        if attr:
            setattr(engine, attr, getattr(engine, attr, 0) + 1)

    primary = pick_primary_uncovered_reason(categories)
    if primary:
        engine.primary_uncovered_reasons[primary] = (
            engine.primary_uncovered_reasons.get(primary, 0) + 1
        )


def compute_avg_time_before_crew_found(engine):
    """Average hours a duty stayed uncovered before a legal replacement was
    confirmed. Includes duties resolved instantly (0h) alongside those that
    required a rest-driven delay, so this reflects overall system responsiveness."""
    if not engine.crew_found_delays:
        return 0.0
    return sum(engine.crew_found_delays) / len(engine.crew_found_delays)


def compute_avg_assignment_delay(engine):
    """Average size of the delay imposed on a flight, counted only over duties
    that actually required a delay (delay_hours > 0). Answers 'when a delay
    happens, how big is it?' as opposed to the overall average above."""
    if not engine.assignment_delays:
        return 0.0
    return sum(engine.assignment_delays) / len(engine.assignment_delays)


# ============================================================
# 3. REPLACEMENT SEARCH
# ============================================================

def _check_candidate(candidate, sick_crew_id, flight_row, roster_df, config, engine, current_dt):
    """Evaluate ONE candidate against ONE flight, applying every check
    find_replacement's search loop applies. Factored out so the same rules
    can be used both for the normal queue search and for testing a specific
    "preferred" candidate (e.g. duty continuity) without duplicating logic.

    Returns (status, delay_hours, reason_categories):
      - "eligible": candidate can be assigned as-is; delay_hours is 0.
      - "rest_shortfall": candidate needs delay_hours of additional rest
        before they could legally take this flight.
      - "rejected": candidate cannot be used; reason_categories explains why.
    """
    debug_prefix = f"[DEBUG] {flight_row['Flight Number']} | {candidate.id} | "
    reason_categories = set()

    fl_dep = pd.to_datetime(flight_row["Actual Departure datetime"])
    fl_arr = pd.to_datetime(flight_row["Actual Arrival datetime"])

    if candidate.id == sick_crew_id:
        if config.debug:
            print(debug_prefix + "same crew as sick")
        return "rejected", 0, reason_categories

    # Skip candidates already assigned to this flight (avoids duplicate rows)
    already_assigned = (
        (roster_df["Crew ID"] == candidate.id)
        & (roster_df["Flight Number"] == flight_row["Flight Number"])
        & (roster_df["Assignment Status"] == "Assigned")
    ).any()
    if already_assigned:
        if config.debug:
            print(debug_prefix + "already assigned to this flight")
        return "rejected", 0, reason_categories

    if candidate.absent:
        reason_categories.add("absence")
        if config.debug:
            print(debug_prefix + "candidate absent")
        return "rejected", 0, reason_categories

    # Standby notification lead time check
    if candidate.is_standby and current_dt is not None:
        time_until_departure = (fl_dep - current_dt).total_seconds() / 60
        if time_until_departure < config.standby_notification_minutes:
            reason_categories.add("no_reserve")
            if config.debug:
                print(
                    debug_prefix
                    + f"standby needs {config.standby_notification_minutes}min notice, "
                    f"only {time_until_departure:.0f}min until departure"
                )
            return "rejected", 0, reason_categories

    candidate_flights = roster_df[
        (roster_df["Crew ID"] == candidate.id) & (roster_df["Assignment Status"] == "Assigned")
    ]

    # Airport check
    prior = candidate_flights[pd.to_datetime(candidate_flights["Actual Departure datetime"]) < fl_dep]
    if not prior.empty:
        latest = prior.loc[pd.to_datetime(prior["Actual Departure datetime"]).idxmax()]
        current_airport = latest["Arr Airport"]
    else:
        current_airport = candidate.base

    if current_airport != flight_row["Dep Airport"]:
        reason_categories.add("airport_mismatch")
        if config.debug:
            print(debug_prefix + f"airport mismatch ({current_airport} != {flight_row['Dep Airport']})")
        return "rejected", 0, reason_categories

    # Time conflict check
    conflict = False
    for _, cf in candidate_flights.iterrows():
        if pd.isna(cf["Actual Departure datetime"]):
            continue
        cf_dep = pd.to_datetime(cf["Actual Departure datetime"])
        cf_arr = pd.to_datetime(cf["Actual Arrival datetime"])
        if cf_dep < fl_arr and cf_arr > fl_dep:
            conflict = True
            break
    if conflict:
        reason_categories.add("schedule_conflict")
        if config.debug:
            print(debug_prefix + "time overlap conflict")
        return "rejected", 0, reason_categories

    # Rules check
    ok, reason, reason_data = passes_duty_and_rest_rules(candidate_flights, flight_row, candidate.id, config)

    if not ok and reason_data.get("rule") == "rolling_window":
        engine.num_rejections_6in9 += 1
        reason_categories.add("rolling_window")

    if not ok:
        if reason.startswith("rest "):
            reason_categories.add("rest")
            rest_hours = reason_data.get("rest_hours", 0)
            shortfall = max(0, config.min_rest_hours - rest_hours)
            if config.debug:
                print(debug_prefix + reason)
            if shortfall > 0:
                return "rest_shortfall", shortfall, reason_categories
            return "rejected", 0, reason_categories
        elif reason.startswith("duty "):
            reason_categories.add("duty_limit")
        elif reason.startswith("connection "):
            reason_categories.add("connection")
        if config.debug:
            print(debug_prefix + reason)
        return "rejected", 0, reason_categories

    if config.debug:
        print(debug_prefix + "eligible")
    return "eligible", 0, reason_categories


def find_replacement(
    sick_crew, flight_row, roster_df, replacement_queue, config, engine, current_dt=None, preferred_candidate=None
):
    """Find best available replacement crew for a flight.

    If current_dt is provided, standby crew are subject to the notification
    lead time constraint (standby_notification_minutes). They cannot be used
    if the flight departs within that window.

    If preferred_candidate is given (typically whoever covered an earlier leg
    of the same duty), they are checked FIRST via _check_candidate, before
    the normal queue search:
      - If they're eligible outright, they're used immediately - no further
        search - so duty continuity is a deliberate preference rather than
        an accidental side effect of airport positioning + queue rotation.
      - If they only have a rest shortfall, they become the initial
        best_rest_candidate, but the queue search still continues - an
        immediately-eligible alternative elsewhere in the queue is preferred
        over forcing a flight delay purely for continuity's sake.
      - If they're rejected outright (duty limit, rolling window, etc.), the
        search proceeds normally without re-checking them twice.

    Returns (candidate_or_None, delay_hours, reason_categories). reason_categories
    is a set drawn from {"rest", "no_reserve", "duty_limit", "rolling_window",
    "absence", "connection", "airport_mismatch", "schedule_conflict"} describing
    why candidates were rejected during this search; it's only meaningful to
    the caller when no candidate is ultimately returned. ("delay_limit" and
    "insufficient_crew" are categories the caller, add_unavailable_records,
    can add after this function returns - they never originate here.)
    """

    best_rest_candidate = None
    rolling_window_failure_found = False
    reason_categories = set()

    if preferred_candidate is not None:
        status, shortfall, cand_categories = _check_candidate(
            preferred_candidate, sick_crew.id, flight_row, roster_df, config, engine, current_dt
        )
        reason_categories |= cand_categories
        if "rolling_window" in cand_categories:
            rolling_window_failure_found = True

        if status == "eligible":
            if preferred_candidate in replacement_queue:
                replacement_queue.remove(preferred_candidate)
                replacement_queue.append(preferred_candidate)
            return preferred_candidate, 0, reason_categories

        if status == "rest_shortfall":
            best_rest_candidate = {"candidate": preferred_candidate, "delay_hours": shortfall}
        # else "rejected" - fall through to the normal search below, skipping
        # this candidate again since we already have their result.

    for candidate in replacement_queue:
        if preferred_candidate is not None and candidate.id == preferred_candidate.id:
            continue  # already evaluated above

        status, shortfall, cand_categories = _check_candidate(
            candidate, sick_crew.id, flight_row, roster_df, config, engine, current_dt
        )
        reason_categories |= cand_categories
        if "rolling_window" in cand_categories:
            rolling_window_failure_found = True

        if status == "rejected":
            continue

        if status == "rest_shortfall":
            if best_rest_candidate is None or shortfall < best_rest_candidate["delay_hours"]:
                best_rest_candidate = {"candidate": candidate, "delay_hours": shortfall}
            continue

        # status == "eligible"
        replacement_queue.remove(candidate)
        replacement_queue.append(candidate)
        if rolling_window_failure_found:
            engine.flights_with_rolling_window_issues += 1
        return candidate, 0, reason_categories

    if best_rest_candidate:
        return best_rest_candidate["candidate"], best_rest_candidate["delay_hours"], reason_categories

    if not reason_categories:
        # Nobody in the queue offered a substantive rejection reason - there
        # was simply no reserve crew available to evaluate in the first place.
        reason_categories.add("no_reserve")

    return None, 0, reason_categories


# ============================================================
# 4. UNAVAILABILITY HANDLING
# ============================================================

def add_unavailable_records(crew_member, current_dt, flights, roster_df, replacement_queue, config, engine):
    """Mark crew as unavailable and attempt replacements for their flights."""

    affected = roster_df[
        (roster_df["Crew ID"] == crew_member.id)
        & (roster_df["Assignment Status"] == "Assigned")
        & (pd.to_datetime(roster_df["Actual Departure datetime"]) >= current_dt)
    ].sort_values("Actual Departure datetime")

    processed_flights = []
    # Replacements that looked successful mid-loop, but whose stats we defer
    # committing until verify_minimum_crew (below) confirms the flight
    # actually survives - see the note at that call site.
    pending_successes = {}

    # Maps Duty ID -> the candidate currently covering that duty. Once
    # someone takes over the sick crew's first leg of a duty, later legs of
    # the SAME duty try that same person first (see find_replacement's
    # preferred_candidate param) instead of searching the full queue from
    # scratch, which previously only achieved continuity by accident (via
    # the airport-positioning check happening to eliminate everyone else,
    # combined with queue rotation).
    duty_continuation = {}

    for _, row in affected.iterrows():
        flight_number = row["Flight Number"]
        duty_id = row["Duty ID"]

        # Skip if flight was already cancelled (e.g. by excessive delay or overrun)
        if flights.loc[flights["Flight Number"] == flight_number, "Cancelled"].iloc[0] == 1:
            continue

        roster_df.loc[
            (roster_df["Crew ID"] == row["Crew ID"])
            & (roster_df["Flight Number"] == flight_number)
            & (roster_df["Assignment Status"] == "Assigned"),
            ["Assignment Status", "Crew Change"],
        ] = ["Unavailable", 1]
        roster_df.loc[roster_df["Flight Number"] == flight_number, "Crew Change"] = 1

        preferred_candidate = duty_continuation.get(duty_id)
        replacement, delay_hours, reason_categories = find_replacement(
            crew_member, row, roster_df, replacement_queue, config, engine, current_dt,
            preferred_candidate=preferred_candidate,
        )

        if replacement and delay_hours > 0:
            # Snapshot which flights are already cancelled before we touch
            # anything, so we can detect ANY newly-cancelled flight below -
            # not just flight_number itself. propagate_rest_delays can cascade
            # a delay onto a completely different crew member's next duty,
            # and that duty can get cancelled by the same overrun/excessive-
            # delay/curfew checks several calls deep - invisible if we only
            # re-check flight_number.
            already_cancelled = set(flights.loc[flights["Cancelled"] == 1, "Flight Number"])

            apply_flight_delay_hours(flights, roster_df, flight_number, delay_hours, config, engine, source="REST")
            propagate_rest_delays(flights, roster_df, row["Duty ID"], config, engine=engine)

            newly_cancelled = (
                set(flights.loc[flights["Cancelled"] == 1, "Flight Number"]) - already_cancelled
            )
            for cancelled_flight in newly_cancelled:
                # Covers both flight_number (if the rest-delay pushed it past
                # a limit) and any other duty cancelled as a side effect of
                # cascading rest-delay propagation to a different crew member.
                # Read back the actual status string so we attribute the
                # precise cause (curfew / excessive delay / duty overrun)
                # rather than a single generic bucket.
                status_matches = roster_df.loc[
                    (roster_df["Flight Number"] == cancelled_flight)
                    & (roster_df["Assignment Status"].isin(STATUS_TO_DELAY_CATEGORY)),
                    "Assignment Status",
                ]
                status = status_matches.iloc[0] if not status_matches.empty else None
                category = STATUS_TO_DELAY_CATEGORY.get(status, "delay_limit")
                record_uncovered_reasons(engine, {category})

            # Re-check: flight may have been cancelled by excessive delay or curfew check
            if flight_number in newly_cancelled:
                processed_flights.append(flight_number)
                continue

            updated = roster_df[
                (roster_df["Crew ID"] == row["Crew ID"])
                & (roster_df["Flight Number"] == flight_number)
                & (roster_df["Assignment Status"] == "Unavailable")
            ]
            if not updated.empty:
                row = updated.iloc[0]

            replacement, remaining_delay, reason_categories = find_replacement(
                crew_member, row, roster_df, replacement_queue, config, engine, current_dt,
                preferred_candidate=preferred_candidate,
            )
            # If replacement still needs additional delay after the first adjustment, reject them
            if remaining_delay > 0:
                if config.debug:
                    print(
                        f"[DEBUG] {flight_number} | {replacement.id} | "
                        f"still needs {remaining_delay:.2f}h rest after delay — rejected"
                    )
                replacement = None
                reason_categories = reason_categories | {"rest"}

        if replacement:
            roster_df.loc[len(roster_df)] = {
                "Crew ID": replacement.id,
                "Duty ID": row["Duty ID"],
                "Flight Number": flight_number,
                "Dep Airport": row["Dep Airport"],
                "Arr Airport": row["Arr Airport"],
                "Tail Number": row["Tail Number"],
                "Scheduled Departure datetime": row["Scheduled Departure datetime"],
                "Scheduled Arrival datetime": row["Scheduled Arrival datetime"],
                "Actual Departure datetime": row["Actual Departure datetime"],
                "Actual Arrival datetime": row["Actual Arrival datetime"],
                "Role": row["Role"],
                "Assignment Status": "Assigned",
                "Flight Delayed": row["Flight Delayed"],
                "Crew Change": 1,
                "Flight Cancelled": 0,
                "assignment_id": row["assignment_id"],
            }
            print(f"[REPLACE] {flight_number} | {replacement.id} replaced {crew_member.id}")

            # Don't commit success stats yet - verify_minimum_crew (below) can
            # still cancel this flight if the total assigned pilot count
            # falls short, and we don't want to have already counted a
            # reassignment/delay/standby-recovery for a flight that ends up
            # cancelled anyway.
            pending_successes[flight_number] = {
                "replacement": replacement,
                "delay_hours": delay_hours,
                "duty_id": row["Duty ID"],
            }
            duty_continuation[duty_id] = replacement
        else:
            record_uncovered_reasons(engine, reason_categories)
            cancel_flight(flights, roster_df, flight_number, crew_member.id)

        processed_flights.append(flight_number)

    # After all replacements resolve, verify minimum crew on each affected flight.
    # This can still cancel a flight we thought we'd successfully covered above
    # (e.g. total assigned pilots below config.pilots_per_duty) - so we only
    # commit the deferred success stats for flights that actually survive this
    # check, and record the rest as uncovered_due_to_insufficient_crew instead.
    pre_verify_cancelled = set(flights.loc[flights["Cancelled"] == 1, "Flight Number"])
    for flight_number in processed_flights:
        verify_minimum_crew(flights, roster_df, flight_number, config)
    newly_cancelled_by_verify = (
        set(flights.loc[flights["Cancelled"] == 1, "Flight Number"]) - pre_verify_cancelled
    )

    for flight_number, info in pending_successes.items():
        if flight_number in newly_cancelled_by_verify:
            record_uncovered_reasons(engine, {"insufficient_crew"})
            continue

        engine.crew_found_delays.append(info["delay_hours"])
        if info["delay_hours"] > 0:
            engine.assignment_delays.append(info["delay_hours"])
        if getattr(info["replacement"], "is_standby", False):
            engine.num_recoveries_by_reserve += 1
        engine.crew_reassignments += 1
        engine.reassigned_duties.add(info["duty_id"])