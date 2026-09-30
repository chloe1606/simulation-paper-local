import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# --------------------------------------------------
# GLOBAL FONT SETTINGS
# --------------------------------------------------
plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["axes.titlesize"] = 14      # Main title size
plt.rcParams["axes.labelsize"] = 12      # Axis heading size
plt.rcParams["xtick.labelsize"] = 11
plt.rcParams["ytick.labelsize"] = 11
plt.rcParams["legend.fontsize"] = 11
plt.rcParams["legend.title_fontsize"] = 11
plt.style.use("seaborn-v0_8-colorblind")
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
]

# Only keep metrics that exist in the dataset
metrics_to_plot = [
    m for m in key_metrics
    if m in df["Metric"].unique()
]

# --------------------------------------------------
# OUTPUT FOLDER
# --------------------------------------------------

PLOT_DIR = Path("plots_" + file_name.split(".")[0])
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

        plt.figure(figsize=(10, 6))

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

        for line_value in sorted(
            subset_ac[line_var].unique()
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

            plt.errorbar(
                subset[X_AXIS_KEY],
                subset["mean"],
                yerr=subset["std"],
                marker="o",
                linewidth=2,
                capsize=5,
                label=legend_label,
            )

        ax = plt.gca()

        ax.set_title(
            metric_name.title(),
            fontsize=14,
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

        plt.xlabel(
            axis_labels.get(
                X_AXIS_KEY,
                X_AXIS_KEY,
            )
        )

        plt.ylabel(
            metric_name.title()
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

        plt.legend(
            title=legend_titles.get(
                line_var,
                line_var.replace(
                    "_",
                    " "
                ).title(),
            )
        )

        plt.tight_layout()

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