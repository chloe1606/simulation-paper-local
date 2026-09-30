# Airline Crew Scheduling Simulation

##### v5 | 11 August 2026 | Eleanor Krige

A discrete-event simulation for testing crew scheduling resilience under operational disruptions (flight delays, crew sickness).

## Project Structure

```
v5 - 20260806/
├── main.py                         # CLI entry point
├── seed_sweep.py                   # Multi-scenario parameter sweep runner
├── README.md
├── utility/
│   ├── flight_data_generator.py    # Generates flights and crew input data
│   ├── validation.py               # Post-simulation constraint checker
│   └── metrics.py                  # Post-simulation KPI/metrics calculations
├── simulation/
│   ├── __init__.py
│   ├── config.py                   # Tuneable parameters
│   ├── models.py                   # Crew data model
│   ├── optimize_roster.py          # ILP-based optimal roster (PuLP/CBC)
│   ├── flight_ops.py               # Delay propagation and cancellation
│   ├── crew_ops.py                 # Replacement logic and rest enforcement
│   └── engine.py                   # SimPy-based simulation engine
├── input/
│   └── airline_inputs_<datetime>.xlsx
└── output/
    └── simulation_results_<datetime>.xlsx
```

---

## Data Generation

**File:** `utility/flight_data_generator.py`

Generates a realistic short-haul flight schedule from a single base airport (LHR) to 23 European destinations.

## Data Model
The flight schedule delta tables are structured as follows:
![mermaid-diagram-2026-08-17T15-05-21.png](./mermaid-diagram-2026-08-17T15-05-21.png "mermaid-diagram-2026-08-17T15-05-21.png")

### Parameters

| Parameter | Value | Description |
| --- | --- | --- |
| Base airport | LHR | All duties depart from and return to LHR |
| Destinations | British Isles: DUB, EDI, GLA, ABZ <br> Short Haul Europe: AMS, CDG, BRU, FRA, ZRH, MUC, CPH, HAM <br> Medium Haul Europe: BCN, MAD, LIS, FCO, NAP, VCE, MXP <br> Longer Europe: ATH, IST, HER, LCA | 23 airports with fixed flight durations and multi-leg option |
| Planes | 5 | Each with a tail number (G-XLEA to G-XLEE) |
| Active crew | 30 | Status: Available |
| Days | 28 | Starting 2026-01-05 |
| Shifts per day | 2 (06:00 and 14:00) | AM and PM shifts |
| Plane offsets | [0, 30, 60, 90, 120] min | Staggered departures per plane within each shift |
| Turnaround | 45 min | Minimum ground time between legs |
| Multi-leg probability | 40% | Remaining 60% are simple return trips |
| Seed | 99 | Reproducible duty-type selection |

### Duty Types

- **Return trip (2 flights):** LHR → DEST → LHR
- **Multi-leg (3 flights):** LHR → DEST1 → DEST2 → LHR, using predefined inter-destination routes

### Output

Updates delta tables in `ctl-sandbox-dev.simulation_paper`:
- "flight_schedule_fact"
- "sectors_dim
- "aircraft_dim
- "crew_dim"
- "crew_assignment_fact"
- "duty_fact"

`input/airline_inputs_<datetime>.xlsx` with sheets with the same name as the delta tables, just for reference or export.

After optimal rostering, `crew_assignment_fact` is written to the same workbook/table.

---

## Optimal Roster (ILP — Max Coverage)

**File:** `simulation/optimize_roster.py`

Integer Linear Program solved with PuLP (CBC solver). Maximises the number of fully covered duties. Allows partial coverage, so the problem is always feasible even when crew numbers cannot cover every duty.

### Decision Variables

- `x[c, d]` ∈ {0, 1} — crew `c` assigned to duty `d`
- `cov[d]` ∈ {0, 1} — duty `d` is fully covered (receives exactly 2 pilots)

### Objective

Maximise Σ cov[d] (number of fully covered duties)

### Constraints

1. **Coverage:** Σ_c x[c,d] = 2 × cov[d] — each duty receives exactly 0 or 2 pilots
2. **Rest conflicts:** If two duties have < 10h gap (including pre/post buffers), same crew cannot do both
3. **Standby reserved:** x[c, d] = 0 for all standby crew (keeps them free for replacements)
4. **Total duty time:** Total duty over 28-day time horizon cannot exceed 190 hours for any crew member.

### Note on Feasibility

With 30 active crew and 10 duties/day (5 planes × 2 shifts), the daily pilot-slot demand is 20. Under the 10h rest constraint and the 6-on-in-9-day rolling window, effective daily supply is tight (~20 slots). The MaxCoverage ILP may leave some duties uncovered but distributes assignments optimally.

### Output

Writes `crew_assignment_fact` delta table

## Simulation

**File:** `simulation/engine.py`

SimPy discrete-event simulation that applies random flight delays and crew absences to a rostered schedule, then attempts crew replacements.

### Disruption Processes

#### Random Flight Delays

- **Timing:** The delay process iterates flights in scheduled departure order. A SimPy timeout fires at each flight's **scheduled** departure time — not its actual (already-delayed) time. A flight pushed forward by connection propagation therefore does **not** receive a second independent delay roll.
- **Cancelled flights skipped:** If a flight has already been cancelled (by overrun, curfew, excessive delay, or crew shortage), the delay roll is skipped entirely — no delay is applied or propagated.
- Each flight has a 20% probability of delay (independent per flight — no weather or route correlation)
- Delay sampled from N(45, 15) minutes, clamped to [15, 60], rounded to nearest 5 min
- **Delay absorption:** if the available turnaround slack (actual gap − 45 min minimum) is larger than the incoming delay, the delay is fully absorbed and does not cascade to subsequent legs
- **No recovery:** once applied, actual departure time is never moved back — there is no turnaround compression, block-time shortening, or schedule slip recovery mechanism
- **Connection propagation:** if a delay reduces the turnaround below 45 min, subsequent legs in the same duty are pushed forward by the exact shortfall
- **Rest propagation:** after connection propagation, the engine checks whether any crew member assigned to this duty now has insufficient rest (< 10h) before their next duty. If so, that next duty's first flight is pushed forward by the exact shortfall, which can cascade across duty boundaries recursively.

#### Crew Absence

- Each crew member (including standby) runs an independent SimPy absence process
- Every 24 simulated hours, each crew member has a 2% probability of becoming sick
- Sick duration is randomly chosen as either 24h or 48h
- When a crew member goes sick, all their future assigned flights are immediately affected — the replacement logic attempts to find substitutes for each
- After the sick duration elapses, the crew member returns to service automatically

### Safeguards

#### Duty Overrun Detection
- After every delay (random or rest-induced), the engine checks whether the actual duty span now exceeds 12h
- Duty span = (actual last arrival + 30 min post-buffer) − (actual first departure − 60 min pre-buffer)
- If exceeded: remaining flights in that duty from the overrun point onwards are cancelled
- Logged as `[DUTY_OVERRUN]`

#### Excessive Delay Auto-Cancellation
- After every delay, checks if total accumulated delay (actual departure − scheduled departure) ≥ 5 hours (300 min)
- If exceeded: the flight is automatically cancelled regardless of crew availability
- Models real-world operational cut-off (slot loss, passenger rebooking threshold)
- Logged as `[EXCESSIVE_DELAY]`

#### Airport Curfew
- After every delay, checks if actual departure time falls at or after 23:00
- If breached: the flight is cancelled — no aircraft movements permitted after curfew
- Models real-world airport operating restrictions (e.g. LHR night-flight ban)
- Logged as `[CURFEW]`

#### Minimum Crew Verification
- After all replacement attempts resolve for a sick crew member, verifies that each affected flight still has ≥ 2 pilots assigned
- Catches edge cases where both pilots on the same flight go sick and only one gets replaced
- If fewer than 2 pilots remain: the flight is cancelled
- Logged as `[CREW_SHORT]`

### Replacement Logic (`simulation/crew_ops.py`)

1. Rotating deque: standby crew first, then active crew. Successful candidates rotate to the back of the queue for fair distribution.
2. For each candidate, check (in order):
   - Not already assigned to this flight 
   - Not currently absent
   - Standby crew: must have ≥ 90 min notification lead time before departure
   - Airport: candidate's current location matches flight departure airport
   - No time conflict: candidate's existing assigned flights must not temporally overlap the target flight
   - Passes connection check (≥ 45 min between candidate's consecutive flights after hypothetical assignment)
   - Passes duty-hour check (duty ≤ 12h including pre/post buffers)
   - Passes rest check (≥ 10h since last duty end)
3. If rest violation: apply rest delay to the flight, propagate connections and rest cascades
4. If remaining delay after rest fix still violates constraints: reject candidate
5. If no valid replacement found: cancel the flight

### Output

- Writes `output/simulation_results_<datetime>.xlsx` with columns for actual times, delay flags, crew changes, cancellations
- Overwrites `crew_assignment_fact` delta table

## Assumptions

### Regulatory & Operational

1. **Pilots per duty:** 2 (hardcoded, not tuneable — treated as a regulatory requirement)
2. **Maximum duty hours:** 12h (measured from 60 min before first departure to 30 min after last arrival)
3. **Minimum rest between duties:** 10h (measured from post-duty release to next pre-duty report)
4. **Pre-duty buffer:** 60 min (crew reports 1h before first flight)
5. **Post-duty buffer:** 30 min (crew released 30 min after last flight lands)
6. **Minimum connection time:** 45 min between consecutive flights in a multi-leg duty
7. **Duty overrun enforced at runtime:** If delays push actual duty hours beyond 12h, remaining flights are cancelled (not just a scheduling constraint)
8. **28-day duty:** Flight schedule is optimized with the constraint of not exceeding 190h duty time per pilot in the 28 day time horizon. In the simulation this may be violated.
9. **Excessive delay cancellation:** Flights with ≥ 5h total accumulated delay are automatically cancelled (operational cut-off)
10. **Airport curfew:** No aircraft movements at or after 23:00 — flights delayed past curfew are cancelled
11. **Minimum crew enforced post-replacement:** If fewer than 2 pilots remain assigned after all replacements resolve, the flight is cancelled

### Schedule Design

12. **Single base:** All crew are based at LHR; no positioning flights or hotel layovers
13. **Fixed fleet:** 5 aircraft, each doing 2 shifts per day (no maintenance downtime modelled)
14. **Symmetric durations:** Outbound and return flight times are identical for a given route
15. **No passenger demand:** Duty selection is random, not demand-driven
16. **Turnaround = connection minimum:** The 45 min turnaround equals the minimum connection time, so any first-leg delay immediately cascades

### Crew Model

17. **Single base, no positioning:** Replacements are always available at LHR (no deadheading cost)
18. **Standby crew have no assigned duties:** They exist solely as reserves
19. **Standby can go sick:** Absence process runs for all crew including standby
20. **Standby notification lead time:** 90 min — standby crew cannot be assigned to flights departing within 90 min of the disruption event
21. **No partial crew:** If one of two pilots goes sick, only their flights need replacement (the other continues)
22. **Homogeneous crew:** All pilots are interchangeable (no type ratings, seniority, or pairing rules)

### Delay Model

23. **Independent delays:** Each flight's delay probability is independent (no weather correlation)
24. **No delay recovery:** Once delayed, a flight cannot depart earlier than its new time
25. **Delays are departure-only:** Flight duration is fixed; delays shift both departure and arrival equally
26. **Cancellation is immediate:** When no replacement is found or a threshold is exceeded, cancellation happens instantly (no advance notice to passengers)
27. **Delay assessed at scheduled departure:** The delay roll fires at the original scheduled slot; flights already pushed forward by connection propagation do not receive a second roll
28. **Partial absorption:** Delays smaller than available turnaround slack are absorbed and do not cascade; delays exceeding the slack propagate only the excess

### Simulation Scope

29. **Simplified fatigue model:** Absence probability is constant
30. **No crew preferences or bidding:** Assignment is purely algorithmic
31. **No cost model:** The simulation tracks cancellations and delays but not revenue impact
32. **Deterministic flight times:** No wind/ATC variability on duration (only departure delays)
33. **28-day horizon:** Aligns with EASA rolling rest window; no longer-cycle requirements (annual leave, recurrent training) modelled
34. **No aircraft rotation:** Cancelled/delayed flights do not affect subsequent flights on the same tail number (aircraft positioning not enforced)

---
## Important Metrics

| Metric |Description |
| ------ | ------------ |
Flight completion rate (%)	| Percentage of scheduled flights that operated (assigned and not cancelled).
Scheduled flights | Total number of flights in the timetable before disruptions.
Operated flights | Flights that were assigned crew and completed without cancellation.
Cancelled flights	| Scheduled flights that did not operate.
Total delay (min)	| Sum of departure delays across all flights in minutes.
Delay propagation factor	| Total delay divided by primary random delay, indicating knock-on effects.
Duty coverage (%)	| Covered duties over total duties
On-time flights	| Number of operated flights departing on or before schedule.
On-time rate (%) |	Percentage of operated flights that depart on time.
Flight reassignments	| Successful crew replacements performed at flight level.
Recovered duties	| Duties where at lease one crew replacement occurred.
Duty-time violation flights	| Flights cancelled because duty-time limits were exceeded.
Schedule recovery time (min/flt)	| Average departure delay per operated flight in minutes.
Flights with rolling window rejections	| Number of flights that were rejected due to rolling window constraints.
Recoveries by reserve crew	| Of all successful recoveries, how many used standby/reserve crew.
Avg assignment delay (h)	| Average delay imposed on a flight, counted only over duties that actually required one.



---
## Quick Start

#### Single Scenario

1. For the flight generator:
```python
# In the run_simulation notebook (v5 folder):
from utility.flight_data_generator import main
main()  # Generates input/airline_inputs_<datetime>.xlsx
```

2. For the optimised roster:
```python
from simulation.optimize_roster import optimize_roster_max_coverage
optimize_roster_max_coverage()  # Generates output/optimized_roster_<datetime>.xlsx
```

3. For the simulation:
``` python
from simulation import SimulationEngine, Config
config = Config()
config.seed = 42
engine = SimulationEngine(config)
engine.run()
engine.save()  # Generates output/simulation_results_<datetime>.xlsx
```

#### Multiple Scenarios
1. Ensure seed_sweep.py is updated with scenario grid parameters: absence probability, number of active crew, number of reserve crew and delay probability
2. The data generator, optimizer and simulation run in one function:
```python
from seed_sweep import run_sweep

results_df = run_sweep() # generates an incremental csv during and 
                         # crew_resilience_seed_sweep_<datetime>.xlsx when it finishes
```
3. To plot the results, you can use the dataframe generated in the seed sweep:
```python
from seed_sweep import plot_results
import matplotlib.pyplot as plt

plot_results(results_df, "delay_prob") # the second argument is the metric you want
                                       # on the x-axis. Plots are saved in 
                                       # plots folder
```
