import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

completion = pd.DataFrame(
    {
        0: [89.38, 95.84, 95.96],
        2: [90.18, 96.12, 96.26],
        4: [90.22, 96.40, 96.88],
        6: [90.14, 96.70, 96.18]
    },
    index=[28, 30, 32]
)

plt.figure(figsize=(7, 4.5))

ax = sns.heatmap(
    completion,
    annot=True,
    fmt=".1f",
    cmap="RdYlGn",
    vmin=89,
    vmax=97,
    linewidths=1,
    linecolor="white",
    cbar_kws={"label": "Flight Completion Rate (%)"}
)

ax.set_xlabel("Reserve Crew")
ax.set_ylabel("Active Crew")
ax.set_title("Effect of Active and Reserve Crew Levels on Flight Completion")

plt.tight_layout()
plt.savefig("flight_completion_heatmap.png", dpi=300, bbox_inches="tight")
plt.show()