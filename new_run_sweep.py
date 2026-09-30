import argparse
import io
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

import pandas as pd

from simulation import SimulationEngine
from simulation.config import Config
import utility.metrics as metrics

ACTIVE_CREW_LEVELS = [28, 29, 30, 31, 32]

RESERVE_CREW_LEVELS = [
    0,
    2,
    4,
    6,
]

ABSENCE_PROBABILITIES = [
    0.01,
    0.02,
    0.03,
]

DELAY_PROBABILITIES = [
    0.2,
]

SEEDS = list(range(1, 6))

OUTPUT_STRING = "crew_resilience_seed_sweep_" + datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_DIR = Path("output")
OUTPUT_CSV = OUTPUT_DIR / f"{OUTPUT_STRING}.csv"
OUTPUT_XLSX = OUTPUT_DIR / f"{OUTPUT_STRING}.xlsx"

SWEEP_INPUT_FILE = str(Config.input_file)


def parse_crew_pairs(raw_pairs: str | None) -> list[tuple[int, int]] | None:
    """Parse values like '28+4,30+2,32+0' into [(28, 4), (30, 2), (32, 0)]."""
    if not raw_pairs:
        return None

    parsed_pairs: list[tuple[int, int]] = []

    for token in raw_pairs.split(","):
        pair_str = token.strip()
        if not pair_str:
            continue

        if "+" not in pair_str:
            raise ValueError(
                f"Invalid pair '{pair_str}'. Use format active+reserve, e.g. 30+2."
            )

        active_str, reserve_str = [part.strip() for part in pair_str.split("+", 1)]

        try:
            active_crew = int(active_str)
            reserve_crew = int(reserve_str)
        except ValueError as exc:
            raise ValueError(
                f"Invalid pair '{pair_str}'. Active and reserve crew must be integers."
            ) from exc

        if active_crew < 0 or reserve_crew < 0:
            raise ValueError(
                f"Invalid pair '{pair_str}'. Crew counts must be non-negative."
            )

        parsed_pairs.append((active_crew, reserve_crew))

    if not parsed_pairs:
        return None

    unique_pairs = list(dict.fromkeys(parsed_pairs))
    return unique_pairs


def run_seed(args: tuple[int, int, int, float, float]) -> pd.DataFrame:
    (
        seed,
        active_crew,
        reserve_crew,
        absence_prob,
        delay_prob,
    ) = args

    print(
        f"Running "
        f"AC={active_crew}, "
        f"RC={reserve_crew}, "
        f"Abs={absence_prob:.1%}, "
        f"Delay={delay_prob:.1%}, "
        f"Seed={seed}"
    )

    config = Config()
    config.seed = seed
    config.absence_probability = absence_prob
    config.delay_probability = delay_prob
    config.input_file = SWEEP_INPUT_FILE

    engine = SimulationEngine(config=config)

    buf = io.StringIO()

    with redirect_stdout(buf):
        engine.run()

    sim_log = buf.getvalue()

    metrics_df = metrics.compute_key_metrics(engine, sim_log)[1].copy()

    metrics_df["active_crew"] = active_crew
    metrics_df["reserve_crew"] = reserve_crew
    metrics_df["absence_prob"] = absence_prob
    metrics_df["delay_prob"] = delay_prob
    metrics_df["seed"] = seed

    return metrics_df


def append_to_csv(df: pd.DataFrame, csv_file: str | Path) -> None:
    file_exists = Path(csv_file).exists()

    df.to_csv(
        csv_file,
        mode="a",
        header=not file_exists,
        index=False,
    )

# --------------------------------------------------
# MAIN
# --------------------------------------------------

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Run resilience sweep across crew scenarios and stochastic probabilities."
    )
    parser.add_argument(
        "--crew-pairs",
        type=str,
        default="",
        help=(
            "Optional explicit active+reserve crew pairs, comma-separated. "
            "Example: 28+4,30+2,32+0"
        ),
    )
    args = parser.parse_args()

    selected_crew_pairs = parse_crew_pairs(args.crew_pairs)
    if selected_crew_pairs is None:
        selected_crew_pairs = [
            (active_crew, reserve_crew)
            for active_crew in ACTIVE_CREW_LEVELS
            for reserve_crew in RESERVE_CREW_LEVELS
        ]

    print("Crew scenarios:", ", ".join(f"{ac}+{rc}" for ac, rc in selected_crew_pairs))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------
    # CLEAN OLD OUTPUTS
    # ----------------------------------------------

    for file in [
        OUTPUT_CSV,
        OUTPUT_XLSX,
    ]:
        if Path(file).exists():
            Path(file).unlink()

    total_scenarios = (
        len(selected_crew_pairs)
        * len(ABSENCE_PROBABILITIES)
        * len(DELAY_PROBABILITIES)
    )

    scenario_counter = 0

    print("============ Start of Sweep " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")+ " ============")

    # ----------------------------------------------
    # SINGLE PROCESS POOL
    # ----------------------------------------------

    with ProcessPoolExecutor(
        max_workers=5
    ) as executor:

        for active_crew, reserve_crew in selected_crew_pairs:

            print(
                f"\n{'=' * 80}\n"
                f"ACTIVE CREW = {active_crew}\n"
                f"RESERVE CREW = {reserve_crew}\n"
                f"{'=' * 80}"
            )

            # ----------------------------------
            # GENERATE DATA ONCE
            # ----------------------------------

            fdg(
                active_crew,
                reserve_crew,
            )

            # ----------------------------------
            # OPTIMISE ONCE
            # ----------------------------------

            optimize_roster_max_coverage(
                Config(input_file=SWEEP_INPUT_FILE)
            )

            # ----------------------------------
            # SCENARIOS
            # ----------------------------------

            for absence_prob in ABSENCE_PROBABILITIES:

                for delay_prob in DELAY_PROBABILITIES:

                    scenario_counter += 1

                    print(
                        f"\n"
                        f"Scenario "
                        f"{scenario_counter}"
                        f"/{total_scenarios}\n"
                        f"Absence = {absence_prob:.1%}\n"
                        f"Delay   = {delay_prob:.1%}"
                    )

                    args_list = [
                        (
                            seed,
                            active_crew,
                            reserve_crew,
                            absence_prob,
                            delay_prob,
                        )
                        for seed in SEEDS
                    ]

                    # --------------------------
                    # RUN ALL SEEDS
                    # --------------------------

                    scenario_results = list(
                        executor.map(
                            run_seed,
                            args_list,
                        )
                    )

                    scenario_df = pd.concat(
                        scenario_results,
                        ignore_index=True,
                    )

                    # --------------------------
                    # SAVE IMMEDIATELY
                    # --------------------------

                    append_to_csv(
                        scenario_df,
                        OUTPUT_CSV,
                    )

                    print(
                        f"Saved "
                        f"{len(scenario_df)} rows "
                        f"to CSV."
                    )

    # --------------------------------------------------
    # CONVERT CSV TO EXCEL ONCE
    # --------------------------------------------------

    print("\nCreating final Excel file...")

    results_df = pd.read_csv(
        OUTPUT_CSV
    )

    with pd.ExcelWriter(
        OUTPUT_XLSX,
        engine="openpyxl",
    ) as writer:

        results_df.to_excel(
            writer,
            sheet_name="Results",
            index=False,
        )

        # -------------------------------------
        # BUILD SUMMARY
        # -------------------------------------

        results_pivot = (
            results_df
            .pivot_table(
                index=[
                    "active_crew",
                    "reserve_crew",
                    "absence_prob",
                    "delay_prob",
                    "seed",
                ],
                columns="Metric",
                values="Value",
                aggfunc="first",
            )
            .reset_index()
        )

        id_cols = [
            "active_crew",
            "reserve_crew",
            "absence_prob",
            "delay_prob",
            "seed",
        ]

        metric_cols = [
            c
            for c in results_pivot.columns
            if c not in id_cols
        ]

        results_pivot[metric_cols] = (
            results_pivot[metric_cols]
            .apply(pd.to_numeric, errors="coerce")
        )

        summary_mean = (
            results_pivot
            .groupby(
                [
                    "active_crew",
                    "reserve_crew",
                    "absence_prob",
                    "delay_prob",
                ]
            )
            .mean(numeric_only=True)
            .reset_index()
        )

        summary_std = (
            results_pivot
            .groupby(
                [
                    "active_crew",
                    "reserve_crew",
                    "absence_prob",
                    "delay_prob",
                ]
            )
            .std(numeric_only=True)
            .reset_index()
        )

        summary_mean = summary_mean.drop(
            columns=["seed"],
            errors="ignore",
        )

        summary_std = summary_std.drop(
            columns=["seed"],
            errors="ignore",
        )

        summary_std = summary_std.rename(
            columns={
                c: f"{c} (std)"
                for c in summary_std.columns
                if c not in [
                    "active_crew",
                    "reserve_crew",
                    "absence_prob",
                    "delay_prob",
                ]
            }
        )

        summary_df = summary_mean.merge(
            summary_std,
            on=[
                "active_crew",
                "reserve_crew",
                "absence_prob",
                "delay_prob",
            ],
        )

        # -------------------------------------
        # WRITE SUMMARY
        # -------------------------------------

        summary_df.to_excel(
            writer,
            sheet_name="Summary",
            index=False,
        )
        
        print("\nExperiment complete.")
        print(f"CSV saved   : {OUTPUT_CSV}")
        print(f"Excel saved : {OUTPUT_XLSX}")
        print("\nSummary:")
        print(summary_df)
        print("============ End of Sweep " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ============")