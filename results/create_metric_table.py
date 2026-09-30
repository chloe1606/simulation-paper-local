import pandas as pd

# ------------------------------------------------------------------
# Load results
# ------------------------------------------------------------------
file_path = "C:\\Users\\ChloeHaffenden\\Documents\\simulation_paper\\output\\crew_resilience_seed_sweep_20260813_085446.xlsx"

df = pd.read_excel(file_path, sheet_name="Results")

# ------------------------------------------------------------------
# Metrics required for paper
# ------------------------------------------------------------------
metrics = [
    "Flight completion rate (%)",
    "Total delay (min)",
    "Delay propagation factor",
    "Duty coverage (%)",
    "Recovered duties",
]

df = df[df["Metric"].isin(metrics)].copy()

# ------------------------------------------------------------------
# Aggregate across all seeds
# ------------------------------------------------------------------
summary = (
    df.groupby(
        ["absence_prob", "delay_prob", "Metric"],
        as_index=False
    )["Value"]
    .agg(
        Value="mean",
        Standard_Deviation="std"
    )
)

# ------------------------------------------------------------------
# Rename columns to desired format
# ------------------------------------------------------------------
summary = summary.rename(
    columns={
        "absence_prob": "Absence Probability",
        "delay_prob": "Delay Probability",
        "Standard_Deviation": "Standard Deviation",
    }
)

# ------------------------------------------------------------------
# Reorder columns
# ------------------------------------------------------------------
summary = summary[
    [
        "Metric",
        "Value",
        "Standard Deviation",
        "Absence Probability",
        "Delay Probability",
    ]
]

# Optional: tidy formatting
summary["Value"] = summary["Value"].round(2)
summary["Standard Deviation"] = summary["Standard Deviation"].round(2)

# Sort nicely
summary = summary.sort_values(
    ["Absence Probability", "Delay Probability", "Metric"]
)

# ------------------------------------------------------------------
# Save outputs
# ------------------------------------------------------------------
summary.to_excel(
    "aggregated_metrics_table.xlsx",
    index=False
)

summary.to_csv(
    "aggregated_metrics_table.csv",
    index=False
)

print(summary.head(20))
print(f"\nRows created: {len(summary)}")

