"""Simulation configuration constants."""

from dataclasses import dataclass


@dataclass
class Config:
    """All tuneable simulation parameters in one place."""

    absence_probability: float = 0.02           # Flat absence probability for each crew member (can be 0.0 to 1.0)
    absence_probability_equation: bool = False  # True: fatigue-adjusted p(t); False: flat absence_probability
    max_duty_hours: int = 12                    # EASA max regulation
    min_rest_hours: int = 12                    # EASA min regulation
    min_connection_minutes: int = 45            # Minimum connection time between flights (for crew to change aircraft)
    pre_duty_minutes: int = 60                  # Report 1h before first flight
    post_duty_minutes: int = 30                 # Release 30min after last flight
    pilots_per_duty: int = 2                    # Minimum crew required per flight
    max_delay_minutes: int = 300                # Auto-cancel threshold (5 hours)
    curfew_hour: int = 23                       # Airport curfew: no movements after this hour (Heathrow)
    curfew_minute: int = 0                      # Airport curfew: minute component (23:00)
    standby_notification_minutes: int = 90      # Lead time for standby crew to report
    delay_probability: float = 0.2              # Delay probability is 20% (1 in 5 flights delayed)
    delay_mean_minutes: float = 45.0            # Normal distribution centre for delays
    delay_std_minutes: float = 15.0             # Normal distribution spread
    delay_min_minutes: int = 15                 # Clamp lower bound
    delay_max_minutes: int = 60                 # Clamp upper bound
    delay_step_minutes: int = 5                 # Round to nearest multiple
    sick_duration_options: tuple = (24, 48)     # Crew are sick for either 1 day or 2 days at random
    duty_days_on: int = 6                       # Number of consecutive duty days before a rest period is required
    duty_days_off: int = 3                      # Number of consecutive rest days after a duty period
    max_duty_hours_7D = 60                      # EASA max regulation for 7-day period
    max_duty_hours_14D = 110                    # EASA max regulation for 14-day period     
    max_duty_hours_28D = 190                    # EASA max regulation for 28-day period
    rolling_window_days: int = 9                # Rolling calendar-day window for work-day limits
    max_work_days_in_window: int = 6            # Maximum duty days within rolling_window_days

    seed: int = None  
    debug: bool = True

    # --- Fatigue model (absence probability) ---
    fatigue_consecutive_threshold: int = 3       # Consecutive duty days: penalty applies above this threshold
    fatigue_consecutive_weight: float = 0.003    # Probability added per consecutive day above the threshold
    fatigue_rest_deficit_threshold: int = 8      # EASA-style rest expectation: ~2 days off per 7 ≈ 8 per 28
    fatigue_rest_deficit_weight: float = 0.001   # Probability added per day < the rest threshold in 28-day window
    fatigue_max_probability: float = 0.05        # Hard cap on the adjusted absence probability

    input_file: str = "input/airline_inputs.xlsx"
    roster_file: str = "input/heuristic_roster.xlsx"
    output_file: str = "output/post_simulation_roster.xlsx"
