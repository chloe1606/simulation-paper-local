import matplotlib.pyplot as plt
import pandas as pd

# Use Times New Roman throughout
plt.rcParams["font.family"] = "Times New Roman"

# Mean values
data = pd.DataFrame({
    "Absence": [1, 2, 3],
    "28 AC + 4 RC": [91.60, 90.36, 88.22],
    "28 AC + 6 RC": [91.60, 90.52, 88.86],
    "30 AC + 2 RC": [98.58, 96.42, 95.42],
    "32 AC + 0 RC": [97.56, 95.92, 94.34],
    "32 AC + 4 RC": [97.78, 96.94, 94.08],
    "34 AC + 2 RC": [96.66, 96.26, 94.64]
})

# Standard deviations from the summary sheet
std_data = pd.DataFrame({
    "Absence": [1, 2, 3],
    "28 AC + 4 RC": [0.80, 0.96, 0.91],
    "28 AC + 6 RC": [1.11, 0.65, 1.75],
    "30 AC + 2 RC": [0.86, 1.60, 0.72],
    "32 AC + 0 RC": [1.25, 1.79, 0.57],
    "32 AC + 4 RC": [0.50, 0.91, 1.04],
    "34 AC + 2 RC": [1.03, 1.00, 1.01]
})

styles = {
    "28 AC + 4 RC": {"color": "#D62728", "lw": 2},
    "28 AC + 6 RC": {"color": "#FF7F0E", "lw": 2},
    "30 AC + 2 RC": {"color": "black", "lw": 4},  # Highlighted
    "32 AC + 0 RC": {"color": "#1F77B4", "lw": 2},
    "32 AC + 4 RC": {"color": "#2CA02C", "lw": 2},
    "34 AC + 2 RC": {"color": "#9467BD", "lw": 2}
}

fig, ax = plt.subplots(figsize=(9, 5))

for config in data.columns[1:]:

    ax.errorbar(
        data["Absence"],
        data[config],
        yerr=std_data[config],
        fmt='o-',
        color=styles[config]["color"],
        linewidth=styles[config]["lw"],
        markersize=7,
        capsize=3,
        capthick=1.2,
        elinewidth=1.2,
        alpha=0.8,
        label=config
    )

ax.set_title(
    "Flight Completion Rate Under Increasing Crew Absence",
    fontsize=14,
    fontweight="bold"
)

ax.set_xlabel("Crew Absence Probability (%)", fontsize=12)
ax.set_ylabel("Flight Completion Rate (%)", fontsize=12)

ax.set_xticks([1, 2, 3])
ax.set_xticklabels(["1%", "2%", "3%"])

ax.set_ylim(85, 100)

ax.grid(True, linestyle="--", alpha=0.4)

# Legend centred vertically on the right
ax.legend(
    title="Staffing Configuration",
    loc="center left",
    bbox_to_anchor=(1.02, 0.5),
    frameon=True,
    fontsize=10,
    title_fontsize=10
)

plt.tight_layout()

plt.savefig(
    "flight_completion_vs_absence_with_errorbars.png",
    dpi=300,
    bbox_inches="tight"
)

plt.show()