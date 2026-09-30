"""Entry point: heuristic rostering + delay simulation + validation."""

from simulation import SimulationEngine, Config, create_heuristic_roster
from utility.validation import validate_roster


def main(debug: bool = False) -> SimulationEngine:
    config = Config(
        input_file="input/airline inputs.xlsx",
        output_file="output/post_simulation_roster.xlsx",
        debug=debug,
    )

    # Step 1: Create heuristic roster from raw flight/crew data
    print("=" * 60)
    print("STEP 1: HEURISTIC ROSTERING")
    print("=" * 60)
    create_heuristic_roster(config)

    # Step 2: Run delay simulation
    print("\n" + "=" * 60)
    print("STEP 2: DELAY SIMULATION")
    print("=" * 60)
    engine = SimulationEngine(config)
    engine.run()
    engine.save()

    # Step 3: Validate constraints
    print("\n")
    validate_roster(engine.roster_df)

    return engine


if __name__ == "__main__":
    main()
