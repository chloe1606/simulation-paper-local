"""Run end-to-end simulation using DFS workbook and crew_assignment_fact."""

import io
from pathlib import Path
import utility.metrics as metrics
from simulation import SimulationEngine, Config, optimize_roster_max_coverage
from utility.flight_data_generator import main as generate_input_data
from utility.validation import validate_roster


def run_simulation(debug: bool = False) -> SimulationEngine:
    """Generate input, build heuristic assignments, run simulation, and validate output."""

    config = Config(
        output_file="output/post_simulation_roster.xlsx",
        debug=debug,
    )

    print("=" * 60)
    print("STEP 1: GENERATE FLIGHT INPUT DATA")
    print("=" * 60)
    input_workbook = Config.input_file
    try:
        generate_input_data(
            active_crew=24,
            standby_crew=0
        )
    except PermissionError as exc:
        if Path(input_workbook).exists():
            print(f"Input workbook locked for overwrite, reusing existing file: {input_workbook}")
            print(f"  Details: {exc}")
        else:
            raise

    print("\n" + "=" * 60)
    print("STEP 2: BUILD ILP CREW ASSIGNMENT FACT")
    print("=" * 60)
    optimize_roster_max_coverage(config)  # Optimize roster for maximum coverage

    # Load the optimized assignment workbook into a roster-shaped dataframe
    # before running disruptions, so validation can check baseline constraints.
    engine = SimulationEngine(config)

    print("\n" + "=" * 60)
    print("VALIDATE OUTPUT AFTER OPTIMIZATION")
    print("=" * 60)
    validate_roster(engine.roster_df)

    # print("\n" + "=" * 60)
    # print("STEP 3: RUN DELAY SIMULATION")
    # print("=" * 60)
    # buf = io.StringIO()
    # engine.run()
    # engine.save()
    # sim_log = buf.getvalue()

    # print("\n" + "=" * 60)
    # print("VALIDATE OUTPUT AFTER SIMULATION")
    # print("=" * 60)
    # validate_roster(engine.roster_df)

    # metrics.compute_key_metrics(engine, sim_log)
    # print(metrics.compute_key_metrics(engine, sim_log)[0])
    # print(metrics.compute_key_metrics(engine, sim_log)[1])

    # return engine


def main() -> None:
    run_simulation(debug=False)


if __name__ == "__main__":
    main()
