from matplotlib.lines import Line2D
import pandas as pd
import matplotlib.pyplot as plt

# Use Times New Roman throughout
plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["xtick.labelsize"] = 18
plt.rcParams["ytick.labelsize"] = 18
plt.rcParams["axes.linewidth"] = 2


# ------------------------------------------------------------------
# Load simulation results
# ------------------------------------------------------------------
file_path = "C:/Users/ChloeHaffenden/Documents/simulation_paper/output/crew_resilience_seed_sweep_20260819_105245_acrc.xlsx"

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

# ------------------------------------------------------------------
# Plot 1: 28 AC + 4 RC, 30 AC + 2 RC, 32 AC + 0 RC
# ------------------------------------------------------------------
configs_plot1 = [
    "28 AC + 4 RC",
    "30 AC + 2 RC",
    "32 AC + 0 RC",
]

styles_plot1 = {
    "28 AC + 4 RC": {"color": "black", "lw": 2.0, "ls": "-",  "marker": "o"},
    "30 AC + 2 RC": {"color": "black", "lw": 2.0, "ls": "--", "marker": "s"},
    "32 AC + 0 RC": {"color": "black", "lw": 2.0, "ls": ":", "marker": "^"},
}

fig1, ax1 = plt.subplots(figsize=(9, 6))

for config in configs_plot1:
    d = summary[summary["Configuration"] == config].sort_values(
        "absence_prob"
    )
    ax1.errorbar(
        d["absence_prob"] * 100,
        d["mean"],
        yerr=d["std"],
        capsize=4,
        label=config,
        **styles_plot1.get(config, {})
    )

ax1.set_xlabel("Absence Probability (%)", fontsize=18)
ax1.set_ylabel("Flight Completion Rate (%)", fontsize=18)

ax1.grid(True, linestyle="--", alpha=0.4)

handles1 = [
    Line2D(
        [0], [0],
        color=s["color"],
        lw=s["lw"],
        ls=s["ls"],
        marker=s["marker"],
        label=label
    )
    for label, s in styles_plot1.items()
]

legend = ax1.legend(
    title="Crew Configuration",
    loc="upper center",
    bbox_to_anchor=(0.5, -0.15),
    frameon=True,
    fontsize=14,
    title_fontsize=16,
    handles=handles1,
    ncol=len(handles1),      # spread entries across one row
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
    "flight_completion_vs_absence_plot1_varying_ac.png",
    dpi=300,
    bbox_inches="tight"
)

print("Saved: flight_completion_vs_absence_plot1_varying_ac.png")

plt.close()

# ------------------------------------------------------------------
# Plot 2: 32 AC + 0 RC, 32 AC + 2 RC, 32 AC + 4 RC
# ------------------------------------------------------------------
configs_plot2 = [
    "32 AC + 0 RC",
    "32 AC + 2 RC",
    "32 AC + 4 RC",
]

styles_plot2 = {
    "32 AC + 0 RC": {"color": "black", "lw": 2.0, "ls": "-",  "marker": "o"},
    "32 AC + 2 RC": {"color": "black", "lw": 2.0, "ls": "--", "marker": "s"},
    "32 AC + 4 RC": {"color": "black", "lw": 2.0, "ls": ":", "marker": "^"},
}

fig2, ax2 = plt.subplots(figsize=(9, 6))

for config in configs_plot2:
    d = summary[summary["Configuration"] == config].sort_values(
        "absence_prob"
    )
    ax2.errorbar(
        d["absence_prob"] * 100,
        d["mean"],
        yerr=d["std"],
        capsize=4,
        label=config,
        **styles_plot2.get(config, {})
    )

ax2.set_xlabel("Absence Probability (%)", fontsize=18)
ax2.set_ylabel("Flight Completion Rate (%)", fontsize=18)

ax2.grid(True, linestyle="--", alpha=0.4)

handles2 = [
    Line2D(
        [0], [0],
        color=s["color"],
        lw=s["lw"],
        ls=s["ls"],
        marker=s["marker"],
        label=label
    )
    for label, s in styles_plot2.items()
]

legend = ax2.legend(
    title="Crew Configuration",
    loc="upper center",
    bbox_to_anchor=(0.5, -0.15),
    frameon=True,
    fontsize=14,
    title_fontsize=16,
    handles=handles2,
    ncol=len(handles2),      # spread entries across one row
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
    "flight_completion_vs_absence_plot2_varying_rc.png",
    dpi=300,
    bbox_inches="tight"
)

print("Saved: flight_completion_vs_absence_plot2_varying_rc.png")

plt.close()