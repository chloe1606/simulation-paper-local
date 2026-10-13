# ------------------------------------------------------------------
# PURPOSE
# ------------------------------------------------------------------
# This script analyses crew resilience simulation results and produces
# publication-quality plots showing how Flight Completion Rate (FCR)
# changes as crew absence probability increases.
#
# Unified features:
# 1. Supports COLOUR, GREYSCALE, or BOTH plotting modes.
# 2. Allows selecting which active crew (AC) and reserve crew (RC)
#    mixes are displayed:
#      - Edit SELECTED_CONFIGS at top of script, OR
#      - Use CLI arguments (--configs, --preset, --interactive, --list).
# 3. Supports preset comparisons:
#      - Plot 1: Constant total crew (28 AC + 4 RC, 30 AC + 2 RC, 32 AC + 0 RC)
#      - Plot 2: Fixed active crew (32 AC + 0 RC, 32 AC + 2 RC, 32 AC + 4 RC)
#      - Custom combinations of any configurations in the dataset.
# 4. Saves all figures directly to output/plots_ac_rc/ (or custom dir).
# ------------------------------------------------------------------

import sys
import argparse
from pathlib import Path
from matplotlib.lines import Line2D
import pandas as pd
import matplotlib.pyplot as plt

# ------------------------------------------------------------------
# GLOBAL FONT SETTINGS
# ------------------------------------------------------------------
plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["xtick.labelsize"] = 18
plt.rcParams["ytick.labelsize"] = 18
plt.rcParams["axes.linewidth"] = 2

# --------------------------------------------------
# USER CONFIGURATION: CREW MIXES & PLOT MODE
# --------------------------------------------------
# Select which configurations to plot.
# Choose any combination from available dataset:
#   26 AC: "26 AC + 0 RC", "26 AC + 2 RC", "26 AC + 4 RC"
#   28 AC: "28 AC + 0 RC", "28 AC + 2 RC", "28 AC + 4 RC"
#   30 AC: "30 AC + 0 RC", "30 AC + 2 RC", "30 AC + 4 RC"
#   32 AC: "32 AC + 0 RC", "32 AC + 2 RC", "32 AC + 4 RC"
SELECTED_CONFIGS = [
    "28 AC + 4 RC",
    "30 AC + 2 RC",
    "32 AC + 0 RC",
]

# Plot style mode: "both", "color", or "greyscale"
STYLE_MODE = "both"

# Whether to also generate standard comparison plots (Plot 1 & Plot 2)
GENERATE_PRESETS = True

# Output directory for saved plots
OUTPUT_DIR = Path("C:/Users/ChloeHaffenden/Documents/simulation_paper/output/plots_ac_rc")
if not OUTPUT_DIR.parent.exists():
    OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "plots_ac_rc"

# Available presets
PRESETS = {
    "plot1_varying_ac": [
        "28 AC + 4 RC",
        "30 AC + 2 RC",
        "32 AC + 0 RC",
    ],
    "plot2_varying_rc": [
        "32 AC + 0 RC",
        "32 AC + 2 RC",
        "32 AC + 4 RC",
    ],
    "total_crew_32": [
        "28 AC + 4 RC",
        "30 AC + 2 RC",
        "32 AC + 0 RC",
    ],
    "total_crew_30": [
        "26 AC + 4 RC",
        "28 AC + 2 RC",
        "30 AC + 0 RC",
    ],
    "all_ac28": [
        "28 AC + 0 RC",
        "28 AC + 2 RC",
        "28 AC + 4 RC",
    ],
    "all_ac30": [
        "30 AC + 0 RC",
        "30 AC + 2 RC",
        "30 AC + 4 RC",
    ],
    "all_ac32": [
        "32 AC + 0 RC",
        "32 AC + 2 RC",
        "32 AC + 4 RC",
    ],
}

# ------------------------------------------------------------------
# STYLING PALETTES (COLOUR & GREYSCALE)
# ------------------------------------------------------------------
# Publication-quality colour palette
COLOR_PALETTE = [
    "#1f77b4",  # Blue
    "#ff7f0e",  # Orange
    "#2ca02c",  # Green
    "#d62728",  # Crimson / Red
    "#9467bd",  # Purple
    "#8c564b",  # Brown
    "#e377c2",  # Pink
    "#17becf",  # Teal / Cyan
    "#7f7f7f",  # Slate Gray
    "#bcbd22",  # Olive
    "#0072b2",  # Deep Blue
    "#d55e00",  # Vermilion
]

MARKERS = ["o", "s", "^", "D", "v", "p", "P", "X", "h", "*", "<", ">"]
LINESTYLES = ["-", "--", ":", "-."]

# Master colour styles for known configurations
CONFIG_COLOR_STYLES = {
    "26 AC + 0 RC": {"color": "#8c564b", "lw": 2.0, "ls": ":",  "marker": "v"},
    "26 AC + 2 RC": {"color": "#e377c2", "lw": 2.0, "ls": "--", "marker": "p"},
    "26 AC + 4 RC": {"color": "#17becf", "lw": 2.0, "ls": "-",  "marker": "h"},

    "28 AC + 0 RC": {"color": "#7f7f7f", "lw": 2.0, "ls": ":",  "marker": "<"},
    "28 AC + 2 RC": {"color": "#bcbd22", "lw": 2.0, "ls": "--", "marker": ">"},
    "28 AC + 4 RC": {"color": "#1f77b4", "lw": 2.0, "ls": "-",  "marker": "o"},

    "30 AC + 0 RC": {"color": "#9467bd", "lw": 2.0, "ls": ":",  "marker": "D"},
    "30 AC + 2 RC": {"color": "#ff7f0e", "lw": 2.0, "ls": "--", "marker": "s"},
    "30 AC + 4 RC": {"color": "#d62728", "lw": 2.0, "ls": "-",  "marker": "P"},

    "32 AC + 0 RC": {"color": "#2ca02c", "lw": 2.0, "ls": ":",  "marker": "^"},
    "32 AC + 2 RC": {"color": "#0072b2", "lw": 2.0, "ls": "--", "marker": "s"},
    "32 AC + 4 RC": {"color": "#d55e00", "lw": 2.0, "ls": "-",  "marker": "o"},
}

# Master greyscale styles for known configurations
CONFIG_GREYSCALE_STYLES = {
    "28 AC + 4 RC": {"color": "black", "lw": 2.0, "ls": "-",  "marker": "o"},
    "30 AC + 2 RC": {"color": "black", "lw": 2.0, "ls": "--", "marker": "s"},
    "32 AC + 0 RC": {"color": "black", "lw": 2.0, "ls": ":",  "marker": "^"},
    "32 AC + 2 RC": {"color": "black", "lw": 2.0, "ls": "--", "marker": "s"},
    "32 AC + 4 RC": {"color": "black", "lw": 2.0, "ls": "-.", "marker": "D"},
}


def get_style_for_config(config_name, index=0, is_colour=True):
    """Return styling dict (color, lw, ls, marker) for a configuration."""
    if is_colour:
        if config_name in CONFIG_COLOR_STYLES:
            return CONFIG_COLOR_STYLES[config_name].copy()
        color = COLOR_PALETTE[index % len(COLOR_PALETTE)]
        marker = MARKERS[index % len(MARKERS)]
        ls = LINESTYLES[index % len(LINESTYLES)]
        return {"color": color, "lw": 2.0, "ls": ls, "marker": marker}
    else:
        if config_name in CONFIG_GREYSCALE_STYLES:
            return CONFIG_GREYSCALE_STYLES[config_name].copy()
        marker = MARKERS[index % len(MARKERS)]
        ls = LINESTYLES[index % len(LINESTYLES)]
        return {"color": "black", "lw": 2.0, "ls": ls, "marker": marker}


# ------------------------------------------------------------------
# DATA LOADING & AGGREGATION
# ------------------------------------------------------------------
def load_and_aggregate_data(file_path=None):
    """Load simulation results and aggregate flight completion rate."""
    if file_path is None:
        file_path = Path("C:/Users/ChloeHaffenden/Documents/simulation_paper/output/crew_resilience_seed_sweep_20260819_105245_acrc.xlsx")
        if not file_path.exists():
            file_path = Path(__file__).resolve().parent.parent / "output" / "crew_resilience_seed_sweep_20260819_105245_acrc.xlsx"

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Simulation results file not found: {file_path}")

    df = pd.read_excel(file_path)

    # Keep only flight completion rate results
    fcr = df[df["Metric"] == "Flight completion rate (%)"].copy()

    # Aggregate across seeds
    summary = (
        fcr.groupby(
            ["active_crew", "reserve_crew", "absence_prob"],
            as_index=False
        )["Value"]
        .agg(["mean", "std"])
        .reset_index()
    )

    # Configuration label
    summary["Configuration"] = (
        summary["active_crew"].astype(int).astype(str)
        + " AC + "
        + summary["reserve_crew"].astype(int).astype(str)
        + " RC"
    )

    return summary


# ------------------------------------------------------------------
# PLOTTING FUNCTION
# ------------------------------------------------------------------
def plot_fcr_comparison(
    summary,
    configs_to_plot,
    output_filename,
    output_dir=OUTPUT_DIR,
    is_colour=True,
    title=None,
    custom_styles=None
):
    """
    Generate and save an error-bar chart comparing Flight Completion Rate
    against Absence Probability in either colour or greyscale.
    """
    out_path = Path(output_filename)
    if output_dir and not out_path.is_absolute():
        out_path = Path(output_dir) / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    available_configs = summary["Configuration"].unique().tolist()
    missing = [c for c in configs_to_plot if c not in available_configs]
    if missing:
        print(f"Warning: Configuration(s) not found in dataset:\n  {missing}")
        configs_to_plot = [c for c in configs_to_plot if c in available_configs]
        if not configs_to_plot:
            raise ValueError("None of the specified configurations exist in the dataset.")

    fig, ax = plt.subplots(figsize=(9, 6))

    handles = []
    for i, config in enumerate(configs_to_plot):
        if custom_styles and config in custom_styles:
            style = custom_styles[config]
        else:
            style = get_style_for_config(config, index=i, is_colour=is_colour)

        d = summary[summary["Configuration"] == config].sort_values("absence_prob")
        ax.errorbar(
            d["absence_prob"] * 100,
            d["mean"],
            yerr=d["std"],
            capsize=4,
            label=config,
            **style
        )

        handles.append(
            Line2D(
                [0], [0],
                color=style["color"],
                lw=style.get("lw", 2.0),
                ls=style.get("ls", "-"),
                marker=style.get("marker", "o"),
                label=config
            )
        )

    ax.set_xlabel("Absence Probability (%)", fontsize=18)
    ax.set_ylabel("Flight Completion Rate (%)", fontsize=18)
    if title:
        ax.set_title(title, fontsize=18, fontweight="bold")

    ax.grid(True, linestyle="--", alpha=0.4)

    # Calculate optimal legend column layout
    num_items = len(handles)
    if num_items <= 3:
        ncol = num_items
    elif num_items == 4:
        ncol = 2
    else:
        ncol = min(3, num_items)

    legend = ax.legend(
        title="Crew Configuration",
        loc="upper center",
        bbox_to_anchor=(0.5, -0.15),
        frameon=True,
        fontsize=14,
        title_fontsize=16,
        handles=handles,
        ncol=ncol,
        columnspacing=1.5,
        handletextpad=0.5,
        borderaxespad=0.0
    )

    legend.get_frame().set_linewidth(1.0)
    legend.get_frame().set_edgecolor("black")
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    plt.subplots_adjust(bottom=0.30)
    plt.tight_layout()

    plt.savefig(
        out_path,
        dpi=300,
        bbox_inches="tight"
    )
    print(f"Saved: {out_path}")
    plt.close()


# ------------------------------------------------------------------
# INTERACTIVE PROMPT HELPER
# ------------------------------------------------------------------
def prompt_interactive_selection(available_configs):
    """Interactively prompt user to select configurations."""
    print("\n" + "=" * 60)
    print("AVAILABLE CREW CONFIGURATIONS:")
    print("=" * 60)
    for idx, cfg in enumerate(available_configs, start=1):
        print(f"  [{idx:2d}] {cfg}")
    print("=" * 60)
    print("Available presets:")
    for name, cfgs in PRESETS.items():
        print(f"  - {name}: {', '.join(cfgs)}")
    print("=" * 60)

    user_input = input("\nEnter numbers (e.g. 1, 3, 5) or preset name: ").strip()
    if not user_input:
        print("Using default selection.")
        return SELECTED_CONFIGS

    if user_input in PRESETS:
        return PRESETS[user_input]

    chosen = []
    for part in user_input.replace(",", " ").split():
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(available_configs):
                chosen.append(available_configs[idx])
        elif part in available_configs:
            chosen.append(part)

    return chosen if chosen else SELECTED_CONFIGS


# ------------------------------------------------------------------
# CLI PARSER & MAIN
# ------------------------------------------------------------------
def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate colour and/or greyscale plots of Flight Completion Rate."
    )
    parser.add_argument(
        "--mode",
        choices=["both", "color", "greyscale"],
        default=STYLE_MODE,
        help=f"Styling mode: 'both' (default), 'color', or 'greyscale'."
    )
    parser.add_argument(
        "--color",
        action="store_true",
        help="Generate only colour plots (shortcut for --mode color)."
    )
    parser.add_argument(
        "--greyscale",
        action="store_true",
        help="Generate only greyscale plots (shortcut for --mode greyscale)."
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="Generate both colour and greyscale plots (shortcut for --mode both)."
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        help="Active/reserve mixes to display (e.g. --configs '28 AC + 4 RC' '30 AC + 2 RC')"
    )
    parser.add_argument(
        "--preset",
        choices=list(PRESETS.keys()),
        help="Predefined comparison preset (e.g. plot1_varying_ac, plot2_varying_rc, total_crew_32)."
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactively select configurations from a numbered list."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available crew configurations and exit."
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help=f"Directory to save output plots (default: {OUTPUT_DIR})."
    )
    parser.add_argument(
        "--no-presets",
        action="store_true",
        help="Skip automatic generation of Plot 1 and Plot 2."
    )
    parser.add_argument(
        "--data",
        help="Path to the simulation results Excel file."
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    summary = load_and_aggregate_data(args.data)
    available_configs = summary["Configuration"].unique().tolist()

    if args.list:
        print("\nAvailable configurations in dataset:")
        for cfg in available_configs:
            print(f"  - {cfg}")
        return

    # Determine mode
    if args.color:
        mode = "color"
    elif args.greyscale:
        mode = "greyscale"
    elif args.both:
        mode = "both"
    else:
        mode = args.mode

    # Determine configurations to plot
    if args.interactive:
        configs_to_plot = prompt_interactive_selection(available_configs)
    elif args.configs:
        configs_to_plot = args.configs
    elif args.preset:
        configs_to_plot = PRESETS[args.preset]
    else:
        configs_to_plot = SELECTED_CONFIGS

    modes_to_run = []
    if mode in ["color", "both"]:
        modes_to_run.append(("colour", True))
    if mode in ["greyscale", "both"]:
        modes_to_run.append(("bw", False))

    output_dir = Path(args.output_dir)
    print(f"\nTarget output directory: {output_dir}")
    print(f"Selected crew mixes: {configs_to_plot}")

    for label_suffix, is_colour in modes_to_run:
        style_title = "Colour" if is_colour else "Greyscale"
        print(f"\n--- Generating {style_title} Plots ---")

        # 1. Selected configurations plot
        plot_fcr_comparison(
            summary=summary,
            configs_to_plot=configs_to_plot,
            output_filename=f"flight_completion_vs_absence_selected_{label_suffix}.png",
            output_dir=output_dir,
            is_colour=is_colour
        )

        # 2. Preset plots (Plot 1 & Plot 2) if requested
        if GENERATE_PRESETS and not args.no_presets and not args.configs and not args.preset:
            # Plot 1: Varying AC with constant total crew
            plot_fcr_comparison(
                summary=summary,
                configs_to_plot=PRESETS["plot1_varying_ac"],
                output_filename=f"flight_completion_vs_absence_plot1_varying_ac_{label_suffix}.png",
                output_dir=output_dir,
                is_colour=is_colour
            )

            # Also maintain backward compatibility for original filenames when greyscale
            if not is_colour:
                plot_fcr_comparison(
                    summary=summary,
                    configs_to_plot=PRESETS["plot1_varying_ac"],
                    output_filename="flight_completion_vs_absence_plot1_varying_ac.png",
                    output_dir=output_dir,
                    is_colour=False
                )

            # Plot 2: Varying RC with fixed AC
            plot_fcr_comparison(
                summary=summary,
                configs_to_plot=PRESETS["plot2_varying_rc"],
                output_filename=f"flight_completion_vs_absence_plot2_varying_rc_{label_suffix}.png",
                output_dir=output_dir,
                is_colour=is_colour
            )

            if not is_colour:
                plot_fcr_comparison(
                    summary=summary,
                    configs_to_plot=PRESETS["plot2_varying_rc"],
                    output_filename="flight_completion_vs_absence_plot2_varying_rc.png",
                    output_dir=output_dir,
                    is_colour=False
                )

    print("\nDone! All plots generated successfully.")


if __name__ == "__main__":
    main()
