# --------------------------------------------------
# PURPOSE
# --------------------------------------------------
# This script automates the analysis and visualisation of crew
# resilience simulation results stored in Excel workbooks.
#
# The script:
# 1. Automatically locates and loads the most recently generated
#    simulation results file from the output directory.
# 2. Cleans and converts metric values into a consistent numeric format.
# 3. Aggregates results across simulation runs/seeds, calculating
#    mean performance and standard deviation for each scenario.
# 4. Produces publication-quality error-bar charts for key operational
#    metrics, including:
#       - Duty coverage
#       - Flight completion rate
#       - Delay propagation factor
#       - Schedule recovery time
#       - Total delay
#       - Flight cancellations
#       - Crew utilisation metrics
#       - Other resilience and recovery indicators
# 5. Generates individual figures for each metric and active crew
#    configuration, showing performance against a selected independent
#    variable (reserve crew, absence probability, or delay probability).
# 6. Creates combined multi-panel figures that display all key metrics
#    in a single dashboard view for each active crew level.
# 7. Exports all figures as high-resolution PNG files suitable for
#    academic publications, reports, and presentations.
# 8. Produces summary statistics tables containing mean and standard
#    deviation results for all scenarios.
# 9. Generates publication-ready tables using the format:
#       Mean ± Standard Deviation
# 10. Creates a pivot table summary for duty coverage to support
#     scenario comparison and reporting.
#
# Key outputs:
#   - Individual metric charts (.png)
#   - Combined multi-metric dashboard figures (.png)
#   - Scenario summary statistics
#   - Paper-ready summary tables
#   - Duty coverage comparison tables
#
# Configuration:
#   X_AXIS can be changed to:
#     - reserve_crew
#     - absence_prob
#     - delay_prob
#
# allowing the same analysis framework to compare resilience
# performance across different experimental factors.
# --------------------------------------------------

import re
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
plt.style.use("seaborn-v0_8-colorblind")

STYLE_MAP = {
    0.01: {"color": "0", "linestyle": "-",  "marker": "o"},
    0.02: {"color": "0", "linestyle": "--", "marker": "s"},
    0.03: {"color": "0.33", "linestyle": "-", "marker": "^"},
    0.04: {"color": "0.33", "linestyle": "--",  "marker": "D"},
    0.05: {"color": "0.67", "linestyle": "-",  "marker": "v"},
}

# --------------------------------------------------
# PLOT CONFIGURATION
# --------------------------------------------------

X_AXIS = "delay_prob"  # reserve_crew | absence_prob | delay_prob
X_AXIS_KEY = X_AXIS.strip().lower()

# --------------------------------------------------
# LOAD RESULTS
# --------------------------------------------------

folder = Path(r"C:/Users/ChloeHaffenden/Documents/simulation_paper/output")

latest_file = max(
    (f for f in folder.rglob("*.xlsx") if f.is_file()),
    key=lambda f: f.stat().st_mtime
)

print("Latest file:", latest_file)
print("Last modified:", latest_file.stat().st_mtime)

file_name = latest_file.name
file_path = latest_file

df = pd.read_excel(
    file_path,
    sheet_name="Results"
)

# --------------------------------------------------
# CLEAN VALUE COLUMN
# --------------------------------------------------

df["Value"] = (
    df["Value"]
    .astype(str)
    .str.replace(" h", "", regex=False)
)

df["Value"] = pd.to_numeric(
    df["Value"],
    errors="coerce"
)

# --------------------------------------------------
# METRICS TO PLOT & SUMMARISE
# --------------------------------------------------

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

# Only keep metrics that exist in the dataset
metrics_to_plot = [
    m for m in key_metrics
    if m in df["Metric"].unique()
]

# --------------------------------------------------
# OUTPUT FOLDER
# --------------------------------------------------

PLOT_DIR = Path("plots_" + file_name.split(".")[0] + "_v2")
PLOT_DIR.mkdir(exist_ok=True)

# --------------------------------------------------
# CREATE AND SAVE CHARTS
# --------------------------------------------------

axis_labels = {
    "reserve_crew": "Number of Reserve Crew",
    "absence_prob": "Absence Probability",
    "delay_prob": "Delay Probability",
}

for metric_name in metrics_to_plot:

    metric_df = df[
        df["Metric"] == metric_name
    ].copy()

    # Build grouping columns dynamically
    group_cols = [
        "active_crew",
        "reserve_crew",
        "absence_prob",
    ]

    if "delay_prob" in metric_df.columns:
        group_cols.append("delay_prob")

    summary = (
        metric_df
        .groupby(group_cols)["Value"]
        .agg(
            mean="mean",
            std="std",
        )
        .reset_index()
    )

    for active_crew in sorted(
        summary["active_crew"].unique()
    ):

        plt.figure(figsize=(9.5, 6))

        subset_ac = summary[
            summary["active_crew"] == active_crew
        ]

        # ----------------------------
        # Select legend variable
        # ----------------------------

        if X_AXIS_KEY in [
            "reserve_crew",
            "absence_prob",
            "delay_prob",
        ]:
            line_var = "absence_prob"

        else:
            raise ValueError(
                f"Unsupported X_AXIS: {X_AXIS}"
            )

        for i, line_value in enumerate(
            sorted(subset_ac[line_var].unique())
        ):

            subset = (
                subset_ac[
                    subset_ac[line_var]
                    == line_value
                ]
                .sort_values(X_AXIS_KEY)
            )

            if len(subset) == 0:
                continue

            if line_var == "absence_prob":
                legend_label = (
                    f"{line_value:.0%}"
                )
            elif line_var == "delay_prob":
                legend_label = (
                    f"{line_var} = "
                    f"{line_value:.0%}"
                )
            else:
                legend_label = (
                    f"{line_var} = "
                    f"{line_value}"
                )

            style = STYLE_MAP.get(
                round(float(line_value), 2),
                {"color": "0.5", "linestyle": "-", "marker": "o"}
            )

            color = style["color"]
            linestyle = style["linestyle"]
            marker = style["marker"]

            plt.errorbar(
                subset[X_AXIS_KEY],
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

        ax.grid(
            which="major",
            linestyle="--",
            alpha=0.5,
        )

        ax.grid(
            which="minor",
            linestyle=":",
            alpha=0.25,
        )

        ax.minorticks_on()

        for spine in ax.spines.values():
            spine.set_linewidth(2)

        ax.tick_params(
            axis="both",
            width=2,
            length=6
        )

        plt.xlabel(
            axis_labels.get(
                X_AXIS_KEY,
                X_AXIS_KEY,
            ),
            fontsize=20,
        )

        plt.ylabel(
            metric_name.title(),
            fontsize=20,
        )

        # Format probability x-axis
        if X_AXIS_KEY in [
            "absence_prob",
            "delay_prob",
        ]:
            ax.set_xticks(
                sorted(
                    subset_ac[X_AXIS_KEY]
                    .dropna()
                    .unique()
                )
            )

            ax.set_xticklabels(
                [
                    f"{x:.0%}"
                    for x in sorted(
                        subset_ac[X_AXIS_KEY]
                        .dropna()
                        .unique()
                    )
                ]
            )

        else:
            plt.xticks(
                sorted(
                    subset_ac[X_AXIS_KEY]
                    .dropna()
                    .unique()
                )
            )

        plt.grid(
            True,
            linestyle="--",
            alpha=0.4,
        )

        legend_titles = {
            "absence_prob": "Absence Probability",
            "delay_prob": "Delay Probability",
            "reserve_crew": "Reserve Crew",
        }

        legend = plt.legend(
            title=legend_titles.get(
                line_var,
                line_var.replace(
                    "_",
                    " "
                ).title(),
            ),
            loc="lower center",
            bbox_to_anchor=(0.5, -0.4),
            frameon=True,
            framealpha=1,
            fancybox=False,
            edgecolor="black",
            borderpad=0.6,
            fontsize=14,
            title_fontsize=18,
            ncol=len(sorted(subset_ac[line_var].unique())),
        )

        legend.get_title().set_ha("center")
        legend._legend_box.align = "center"

        for text in legend.get_texts():
            text.set_ha("center")

        plt.tight_layout()
        plt.subplots_adjust(bottom=0.2)

        # ----------------------------
        # SAVE PLOT
        # ----------------------------

        filename = re.sub(
            r"[^A-Za-z0-9]+",
            "_",
            metric_name
        ).strip("_")

        save_path = (
            PLOT_DIR
            / (
                f"{filename}"
                f"_{X_AXIS_KEY}"
                f"_AC{active_crew}.png"
            )
        )

        plt.savefig(
            save_path,
            dpi=300,
            bbox_inches="tight",
        )

        print(
            f"Saved: {save_path}"
        )

        #plt.show()
        plt.close()

# --------------------------------------------------
# CREATE COMBINED SUBPLOT FIGURE
# --------------------------------------------------

# Get unique active crew values
active_crew_values = sorted(
    df["active_crew"].unique()
)

for active_crew in active_crew_values:
    
    # Calculate subplot grid dimensions
    n_metrics = len(metrics_to_plot)
    n_cols = 3
    n_rows = (n_metrics + n_cols - 1) // n_cols
    
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=(18, 4 * n_rows)
    )
    
    # Flatten axes array for easier iteration
    if n_rows == 1:
        axes = [axes]
    if n_cols == 1:
        axes = [[ax] for ax in axes]
    else:
        axes = axes.flatten()
    
    for idx, metric_name in enumerate(metrics_to_plot):
        ax = axes[idx]
        
        metric_df = df[
            df["Metric"] == metric_name
        ].copy()
        
        # Build grouping columns dynamically
        group_cols = [
            "active_crew",
            "reserve_crew",
            "absence_prob",
        ]
        
        if "delay_prob" in metric_df.columns:
            group_cols.append("delay_prob")
        
        summary = (
            metric_df
            .groupby(group_cols)["Value"]
            .agg(
                mean="mean",
                std="std",
            )
            .reset_index()
        )
        
        subset_ac = summary[
            summary["active_crew"] == active_crew
        ]
        
        # Select legend variable
        if X_AXIS_KEY in [
            "reserve_crew",
            "absence_prob",
            "delay_prob",
        ]:
            line_var = "absence_prob"
        else:
            raise ValueError(
                f"Unsupported X_AXIS: {X_AXIS}"
            )
        
        for i, line_value in enumerate(
            sorted(subset_ac[line_var].unique())
        ):
            subset = (
                subset_ac[
                    subset_ac[line_var]
                    == line_value
                ]
                .sort_values(X_AXIS_KEY)
            )
            
            if len(subset) == 0:
                continue
            
            if line_var == "absence_prob":
                legend_label = (
                    f"{line_value:.0%}"
                )
            elif line_var == "delay_prob":
                legend_label = (
                    f"{line_var} = "
                    f"{line_value:.0%}"
                )
            else:
                legend_label = (
                    f"{line_var} = "
                    f"{line_value}"
                )
            
            
            style = STYLE_MAP.get(
                round(float(line_value), 2),
                {"color": "0.5", "linestyle": "-", "marker": "o"}
            )

            color = style["color"]
            linestyle = style["linestyle"]
            marker = style["marker"]

            
            ax.errorbar(
                subset[X_AXIS_KEY],
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
        
        ax.set_title(
            metric_name.title(),
            fontsize=12,
            fontname="Times New Roman",
        )
        
        ax.grid(
            which="major",
            linestyle="--",
            alpha=0.5,
        )
        
        ax.grid(
            which="minor",
            linestyle=":",
            alpha=0.25,
        )
        
        ax.minorticks_on()
        
        ax.set_xlabel(
            axis_labels.get(
                X_AXIS_KEY,
                X_AXIS_KEY,
            ),
            fontsize=10,
        )
        
        ax.set_ylabel(
            metric_name.title(),
            fontsize=10,
        )
        
        # Format probability x-axis
        if X_AXIS_KEY in [
            "absence_prob",
            "delay_prob",
        ]:
            ax.set_xticks(
                sorted(
                    subset_ac[X_AXIS_KEY]
                    .dropna()
                    .unique()
                )
            )
            
            ax.set_xticklabels(
                [
                    f"{x:.0%}"
                    for x in sorted(
                        subset_ac[X_AXIS_KEY]
                        .dropna()
                        .unique()
                    )
                ]
            )
        else:
            ax.set_xticks(
                sorted(
                    subset_ac[X_AXIS_KEY]
                    .dropna()
                    .unique()
                )
            )
        
        if idx == 0:  # Only add legend to first subplot
            legend_titles = {
                "absence_prob": "Absence\nProbability",
                "delay_prob": "Delay\nProbability",
                "reserve_crew": "Reserve Crew",
            }
            
            ax.legend(
                title=legend_titles.get(
                    line_var,
                    line_var.replace(
                        "_",
                        " "
                    ).title(),
                ),
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
    
    # Save combined figure
    combined_save_path = (
        PLOT_DIR
        / f"combined_all_metrics_{X_AXIS_KEY}_AC{active_crew}.png"
    )
    
    plt.savefig(
        combined_save_path,
        dpi=300,
        bbox_inches="tight",
    )
    
    print(
        f"Saved combined: {combined_save_path}"
    )
    
    plt.close()

# --------------------------------------------------
# SUMMARY TABLE
# --------------------------------------------------

summary_tables = []

for metric_name in metrics_to_plot:

    metric_df = df[
        df["Metric"] == metric_name
    ].copy()

    group_cols = [
        "active_crew",
        "reserve_crew",
        "absence_prob",
    ]

    if "delay_prob" in metric_df.columns:
        group_cols.append("delay_prob")

    summary = (
        metric_df
        .groupby(group_cols)["Value"]
        .agg(
            Mean="mean",
            StdDev="std",
        )
        .reset_index()
    )

    summary["Metric"] = metric_name

    summary_tables.append(summary)

summary_df = pd.concat(
    summary_tables,
    ignore_index=True,
)


print(
    summary_df.sort_values(
        [
            "Metric",
            "active_crew",
            "absence_prob",
            "reserve_crew",
        ]
    )
)

# --------------------------------------------------
# DUTY COVERAGE PIVOT TABLE
# --------------------------------------------------

coverage_table = (
    summary_df[
        summary_df["Metric"]
        == "Duty coverage (%)"
    ]
    .pivot_table(
        index=(
            "delay_prob"
            if "delay_prob" in summary_df.columns
            else "reserve_crew"
        ),
        columns="absence_prob",
        values="Mean",
    )
    .round(2)
)

print("\nDuty Coverage Summary:")
print(coverage_table)

# --------------------------------------------------
# PAPER-READY TABLE
# --------------------------------------------------

summary_df["Mean ± Std"] = (
    summary_df["Mean"].round(2).astype(str)
    + " ± "
    + summary_df["StdDev"].round(2).astype(str)
)

paper_table = (
    summary_df[
        [
            col
            for col in [
                "Metric",
                "active_crew",
                "reserve_crew",
                "absence_prob",
                "delay_prob",
                "Mean ± Std",
            ]
            if col in summary_df.columns
        ]
    ]
)

print("\nPaper Table:")
print(paper_table)