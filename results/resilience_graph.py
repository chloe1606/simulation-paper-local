# --------------------------------------------------
# PURPOSE
# --------------------------------------------------
# This script automates the analysis and visualisation of crew
# resilience simulation results stored in Excel workbooks.
#
# Unified features:
# 1. Supports COLOUR, GREYSCALE, or BOTH plotting modes.
# 2. Automatically locates and loads the simulation results file with a
#    'Results' sheet (or accepts a specific path via CLI / config).
# 3. Cleans and converts metric values into a consistent numeric format.
# 4. Aggregates results across simulation runs/seeds, calculating
#    mean performance and standard deviation for each scenario.
# 5. Produces publication-quality error-bar charts with distinct
#    markers and line styles for key operational metrics.
# 6. Generates individual figures for each metric and active crew level,
#    saving them directly into output/plots_ac_rc/ (or custom dir).
# 7. Creates combined multi-panel dashboard figures for each active crew.
# 8. Produces summary statistics tables, paper-ready Mean ± Std tables,
#    and duty coverage comparison tables.
#
# Configuration:
#   STYLE_MODE can be set to: "both", "color", or "greyscale"
#   X_AXIS can be set to: "reserve_crew", "absence_prob", or "delay_prob"
# --------------------------------------------------

import re
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# --------------------------------------------------
# GLOBAL FONT SETTINGS
# --------------------------------------------------
plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["axes.titlesize"] = 18      # Main title size
plt.rcParams["xtick.labelsize"] = 18
plt.rcParams["ytick.labelsize"] = 18
plt.rcParams["legend.fontsize"] = 14
plt.rcParams["legend.title_fontsize"] = 16
plt.rcParams["axes.linewidth"] = 2

# --------------------------------------------------
# USER CONFIGURATION: STYLES, AXIS & OUTPUT
# --------------------------------------------------
# Plotting mode: "both", "color", or "greyscale"
STYLE_MODE = "both"

# Default independent variable for the X axis: "reserve_crew", "absence_prob", or "delay_prob"
X_AXIS = "delay_prob"

# Default directory for output charts
OUTPUT_DIR = Path("C:/Users/ChloeHaffenden/Documents/simulation_paper/output/plots_delay")
if not OUTPUT_DIR.parent.exists():
    OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "plots_delay"

# --------------------------------------------------
# STYLING PALETTES (COLOUR & GREYSCALE)
# --------------------------------------------------
# Colour palettes and styles
COLOUR_PALETTE = [
    "#1f77b4",  # Blue
    "#ff7f0e",  # Orange
    "#2ca02c",  # Green
    "#d62728",  # Red
    "#9467bd",  # Purple
    "#8c564b",  # Brown
    "#e377c2",  # Pink
    "#17becf",  # Cyan/Teal
]

MARKERS = ["o", "s", "^", "D", "v", "p", "P", "X"]
LINESTYLES = ["-", "--", "-.", ":", "-", "--", "-.", ":"]

COLOUR_STYLE_MAP = {
    0.01: {"color": "#1f77b4", "linestyle": "-",  "marker": "o"},
    0.02: {"color": "#ff7f0e", "linestyle": "--", "marker": "s"},
    0.03: {"color": "#2ca02c", "linestyle": "-.", "marker": "^"},
    0.04: {"color": "#d62728", "linestyle": ":",  "marker": "D"},
    0.05: {"color": "#9467bd", "linestyle": "-",  "marker": "v"},
}

GREYSCALE_STYLE_MAP = {
    0.01: {"color": "0", "linestyle": "-",  "marker": "o"},
    0.02: {"color": "0", "linestyle": "--", "marker": "s"},
    0.03: {"color": "0.33", "linestyle": "-", "marker": "^"},
    0.04: {"color": "0.33", "linestyle": "--",  "marker": "D"},
    0.05: {"color": "0.67", "linestyle": "-",  "marker": "v"},
}


def get_style(value, index=0, is_colour=True):
    """Return styling dict with color, linestyle, marker for a given line value."""
    try:
        val_round = round(float(value), 2)
        if is_colour and val_round in COLOUR_STYLE_MAP:
            return COLOUR_STYLE_MAP[val_round].copy()
        elif not is_colour and val_round in GREYSCALE_STYLE_MAP:
            return GREYSCALE_STYLE_MAP[val_round].copy()
    except (ValueError, TypeError):
        pass

    marker = MARKERS[index % len(MARKERS)]
    linestyle = LINESTYLES[index % len(LINESTYLES)]
    if is_colour:
        color = COLOUR_PALETTE[index % len(COLOUR_PALETTE)]
    else:
        greys = ["0", "0.25", "0.5", "0.7"]
        color = greys[index % len(greys)]
    return {"color": color, "linestyle": linestyle, "marker": marker}


# --------------------------------------------------
# LOAD RESULTS HELPER
# --------------------------------------------------
def find_results_file(custom_file=None):
    """Find the most recent simulation results Excel file containing a 'Results' sheet."""
    if custom_file:
        p = Path(custom_file)
        if p.is_file():
            return p
        raise FileNotFoundError(f"Specified file does not exist: {custom_file}")

    folder = Path("C:/Users/ChloeHaffenden/Documents/simulation_paper/output")
    if not folder.exists():
        folder = Path(__file__).resolve().parent.parent / "output"

    candidates = []
    for f in folder.rglob("*.xlsx"):
        if f.is_file():
            try:
                xls = pd.ExcelFile(f)
                if "Results" in xls.sheet_names:
                    candidates.append((f, f.stat().st_mtime))
            except Exception:
                pass

    if not candidates:
        raise FileNotFoundError(f"No Excel file with a 'Results' sheet found in {folder}")

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


# --------------------------------------------------
# CLI PARSER
# --------------------------------------------------
def parse_arguments():
    """Parse command line options."""
    parser = argparse.ArgumentParser(
        description="Generate colour and/or greyscale resilience graphs across operational metrics."
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
        "--data",
        help="Path to the simulation results Excel file."
    )
    parser.add_argument(
        "--x-axis",
        choices=["reserve_crew", "absence_prob", "delay_prob"],
        default=X_AXIS,
        help=f"Variable for the X axis (default: {X_AXIS})."
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help=f"Directory to save output plots (default: {OUTPUT_DIR})."
    )
    parser.add_argument(
        "--skip-combined",
        action="store_true",
        help="Skip generating combined multi-panel figures."
    )
    return parser.parse_args()


# --------------------------------------------------
# MAIN EXECUTION
# --------------------------------------------------
def main():
    args = parse_arguments()

    if args.color:
        mode = "color"
    elif args.greyscale:
        mode = "greyscale"
    elif args.both:
        mode = "both"
    else:
        mode = args.mode

    x_axis_key = args.x_axis.strip().lower()
    plot_dir = Path(args.output_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    file_path = find_results_file(args.data)
    print(f"Loaded results file: {file_path}")
    print(f"Output directory: {plot_dir}")

    df = pd.read_excel(file_path, sheet_name="Results")

    # Clean Value column
    df["Value"] = df["Value"].astype(str).str.replace(" h", "", regex=False)
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")

    # Metrics to plot & summarise
    key_metrics = [
        "Duty coverage (%)",
        "Flight completion rate (%)",
        "Delay propagation factor",
        "Flight reassignments",
        "Schedule recovery time (min/flt)",
        "Crew block hours (avg / crew)",
        "On-time rate (%)",
        "Total delay (min)",
        "Recovered duties",
        "Cancelled flights",
        "Rolling window rejections",
        "Flights with rolling window rejections",
        "Uncovered duties",
        "Primary delay (mins)",
        "Secondary delay (mins)"
    ]

    metrics_to_plot = [m for m in key_metrics if m in df["Metric"].unique()]

    axis_labels = {
        "reserve_crew": "Number of Reserve Crew",
        "absence_prob": "Absence Probability",
        "delay_prob": "Delay Probability",
    }

    if x_axis_key in ["reserve_crew", "absence_prob", "delay_prob"]:
        line_var = "absence_prob"
    else:
        raise ValueError(f"Unsupported X_AXIS: {args.x_axis}")

    modes_to_run = []
    if mode in ["color", "both"]:
        modes_to_run.append(("colour", True))
    if mode in ["greyscale", "both"]:
        modes_to_run.append(("bw", False))

    # Generate charts for each mode
    for label_suffix, is_colour in modes_to_run:
        style_title = "Colour" if is_colour else "Greyscale"
        print(f"\n--- Generating {style_title} Charts ({label_suffix}) ---")

        # --------------------------------------------------
        # Individual Metric Charts
        # --------------------------------------------------
        for metric_name in metrics_to_plot:
            metric_df = df[df["Metric"] == metric_name].copy()

            group_cols = ["active_crew", "reserve_crew", "absence_prob"]
            if "delay_prob" in metric_df.columns:
                group_cols.append("delay_prob")

            summary = (
                metric_df
                .groupby(group_cols)["Value"]
                .agg(mean="mean", std="std")
                .reset_index()
            )

            for active_crew in sorted(summary["active_crew"].unique()):
                plt.figure(figsize=(9.5, 6))

                subset_ac = summary[summary["active_crew"] == active_crew]
                unique_line_values = sorted(subset_ac[line_var].unique())

                for i, line_value in enumerate(unique_line_values):
                    subset = subset_ac[subset_ac[line_var] == line_value].sort_values(x_axis_key)
                    if len(subset) == 0:
                        continue

                    if line_var == "absence_prob":
                        legend_label = f"{line_value:.0%}"
                    elif line_var == "delay_prob":
                        legend_label = f"{line_var} = {line_value:.0%}"
                    else:
                        legend_label = f"{line_var} = {line_value}"

                    style = get_style(line_value, index=i, is_colour=is_colour)
                    color = style["color"]
                    linestyle = style["linestyle"]
                    marker = style["marker"]

                    plt.errorbar(
                        subset[x_axis_key],
                        subset["mean"],
                        yerr=subset["std"],
                        fmt=marker,
                        linestyle=linestyle,
                        color=color,
                        markerfacecolor="white",
                        markeredgecolor=color,
                        linewidth=3,
                        capsize=5,
                        label=legend_label,
                    )

                ax = plt.gca()
                ax.set_title(
                    metric_name.title(),
                    fontsize=16,
                    fontname="Times New Roman",
                    pad=25,
                )

                ax.text(
                    0.5,
                    1.02,
                    f"Active Crew = {active_crew}",
                    transform=ax.transAxes,
                    ha="center",
                    va="bottom",
                    fontsize=12,
                    fontname="Times New Roman",
                )

                ax.grid(which="major", linestyle="--", alpha=0.5)
                ax.grid(which="minor", linestyle=":", alpha=0.25)
                ax.minorticks_on()

                for spine in ax.spines.values():
                    spine.set_linewidth(2)

                ax.tick_params(axis="both", width=2, length=6)

                plt.xlabel(axis_labels.get(x_axis_key, x_axis_key), fontsize=20)
                plt.ylabel(metric_name.title(), fontsize=20)

                # Format probability x-axis
                if x_axis_key in ["absence_prob", "delay_prob"]:
                    x_vals = sorted(subset_ac[x_axis_key].dropna().unique())
                    ax.set_xticks(x_vals)
                    ax.set_xticklabels([f"{x:.0%}" for x in x_vals])
                else:
                    plt.xticks(sorted(subset_ac[x_axis_key].dropna().unique()))

                legend_titles = {
                    "absence_prob": "Absence Probability",
                    "delay_prob": "Delay Probability",
                    "reserve_crew": "Reserve Crew",
                }

                legend = plt.legend(
                    title=legend_titles.get(line_var, line_var.replace("_", " ").title()),
                    loc="lower center",
                    bbox_to_anchor=(0.5, -0.4),
                    frameon=True,
                    framealpha=1,
                    fancybox=False,
                    edgecolor="black",
                    borderpad=0.6,
                    fontsize=14,
                    title_fontsize=18,
                    ncol=len(unique_line_values),
                )

                legend.get_title().set_ha("center")
                legend._legend_box.align = "center"
                for text in legend.get_texts():
                    text.set_ha("center")

                plt.tight_layout()
                plt.subplots_adjust(bottom=0.2)

                filename = re.sub(r"[^A-Za-z0-9]+", "_", metric_name).strip("_")
                save_path = plot_dir / f"{filename}_{x_axis_key}_AC{active_crew}_{label_suffix}.png"
                plt.savefig(save_path, dpi=300, bbox_inches="tight")
                print(f"Saved: {save_path}")

                # Save original un-suffixed filename for greyscale compatibility
                if not is_colour:
                    compat_path = plot_dir / f"{filename}_{x_axis_key}_AC{active_crew}.png"
                    plt.savefig(compat_path, dpi=300, bbox_inches="tight")

                plt.close()

        # --------------------------------------------------
        # Combined Multi-Panel Figures
        # --------------------------------------------------
        if not args.skip_combined:
            active_crew_values = sorted(df["active_crew"].unique())
            for active_crew in active_crew_values:
                n_metrics = len(metrics_to_plot)
                n_cols = 3
                n_rows = (n_metrics + n_cols - 1) // n_cols

                fig, axes = plt.subplots(n_rows, n_cols, figsize=(18, 4 * n_rows))

                if n_rows == 1:
                    axes = [axes]
                if n_cols == 1:
                    axes = [[ax] for ax in axes]
                else:
                    axes = axes.flatten()

                for idx, metric_name in enumerate(metrics_to_plot):
                    ax = axes[idx]
                    metric_df = df[df["Metric"] == metric_name].copy()

                    group_cols = ["active_crew", "reserve_crew", "absence_prob"]
                    if "delay_prob" in metric_df.columns:
                        group_cols.append("delay_prob")

                    summary = (
                        metric_df
                        .groupby(group_cols)["Value"]
                        .agg(mean="mean", std="std")
                        .reset_index()
                    )

                    subset_ac = summary[summary["active_crew"] == active_crew]
                    unique_line_values = sorted(subset_ac[line_var].unique())

                    for i, line_value in enumerate(unique_line_values):
                        subset = subset_ac[subset_ac[line_var] == line_value].sort_values(x_axis_key)
                        if len(subset) == 0:
                            continue

                        if line_var == "absence_prob":
                            legend_label = f"{line_value:.0%}"
                        elif line_var == "delay_prob":
                            legend_label = f"{line_var} = {line_value:.0%}"
                        else:
                            legend_label = f"{line_var} = {line_value}"

                        style = get_style(line_value, index=i, is_colour=is_colour)
                        color = style["color"]
                        linestyle = style["linestyle"]
                        marker = style["marker"]

                        ax.errorbar(
                            subset[x_axis_key],
                            subset["mean"],
                            yerr=subset["std"],
                            fmt=marker,
                            linestyle=linestyle,
                            color=color,
                            markerfacecolor="white",
                            markeredgecolor=color,
                            linewidth=3,
                            capsize=5,
                            label=legend_label,
                        )

                    ax.set_title(metric_name.title(), fontsize=12, fontname="Times New Roman")
                    ax.grid(which="major", linestyle="--", alpha=0.5)
                    ax.grid(which="minor", linestyle=":", alpha=0.25)
                    ax.minorticks_on()

                    ax.set_xlabel(axis_labels.get(x_axis_key, x_axis_key), fontsize=10)
                    ax.set_ylabel(metric_name.title(), fontsize=10)

                    if x_axis_key in ["absence_prob", "delay_prob"]:
                        x_vals = sorted(subset_ac[x_axis_key].dropna().unique())
                        ax.set_xticks(x_vals)
                        ax.set_xticklabels([f"{x:.0%}" for x in x_vals])
                    else:
                        ax.set_xticks(sorted(subset_ac[x_axis_key].dropna().unique()))

                    if idx == 0:
                        legend_titles = {
                            "absence_prob": "Absence\nProbability",
                            "delay_prob": "Delay\nProbability",
                            "reserve_crew": "Reserve Crew",
                        }
                        ax.legend(
                            title=legend_titles.get(line_var, line_var.replace("_", " ").title()),
                            loc="best",
                            fontsize=8,
                        )

                # Remove empty subplots
                for idx in range(len(metrics_to_plot), len(axes)):
                    fig.delaxes(axes[idx])

                fig.suptitle(
                    f"All Metrics - Active Crew = {active_crew}",
                    fontsize=16,
                    fontname="Times New Roman",
                    y=0.995,
                )

                plt.tight_layout()
                combined_save_path = plot_dir / f"combined_all_metrics_{x_axis_key}_AC{active_crew}_{label_suffix}.png"
                plt.savefig(combined_save_path, dpi=300, bbox_inches="tight")
                print(f"Saved combined: {combined_save_path}")

                if not is_colour:
                    compat_combined = plot_dir / f"combined_all_metrics_{x_axis_key}_AC{active_crew}.png"
                    plt.savefig(compat_combined, dpi=300, bbox_inches="tight")

                plt.close()

    # --------------------------------------------------
    # SUMMARY TABLES
    # --------------------------------------------------
    summary_tables = []
    for metric_name in metrics_to_plot:
        metric_df = df[df["Metric"] == metric_name].copy()
        group_cols = ["active_crew", "reserve_crew", "absence_prob"]
        if "delay_prob" in metric_df.columns:
            group_cols.append("delay_prob")

        summary = (
            metric_df
            .groupby(group_cols)["Value"]
            .agg(Mean="mean", StdDev="std")
            .reset_index()
        )
        summary["Metric"] = metric_name
        summary_tables.append(summary)

    summary_df = pd.concat(summary_tables, ignore_index=True)

    print("\n" + "=" * 60)
    print("DUTY COVERAGE SUMMARY PIVOT TABLE")
    print("=" * 60)
    coverage_table = (
        summary_df[summary_df["Metric"] == "Duty coverage (%)"]
        .pivot_table(
            index=("delay_prob" if "delay_prob" in summary_df.columns else "reserve_crew"),
            columns="absence_prob",
            values="Mean",
        )
        .round(2)
    )
    print(coverage_table)

    summary_df["Mean ± Std"] = (
        summary_df["Mean"].round(2).astype(str)
        + " ± "
        + summary_df["StdDev"].round(2).astype(str)
    )

    paper_table = summary_df[
        [col for col in ["Metric", "active_crew", "reserve_crew", "absence_prob", "delay_prob", "Mean ± Std"]
         if col in summary_df.columns]
    ]

    print("\n" + "=" * 60)
    print("PAPER-READY TABLE (First 15 rows)")
    print("=" * 60)
    print(paper_table.head(15))

    print(f"\nAll plots and tables generated in {plot_dir}")


if __name__ == "__main__":
    main()
