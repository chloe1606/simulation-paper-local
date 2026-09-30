"""Domain models for the simulation."""

from __future__ import annotations

from typing import Any


class Crew:
    """Represents a single crew member with assignment tracking."""

    def __init__(self, row: dict[str, Any] | Any) -> None:
        self.id: str = row["Crew ID"]
        self.base: str = row["Base"]
        self.status: str = row.get("Status", "Available")
        self.absent: bool = False
        self.last_end: Any | None = None
        self.assigned_duties: list[str] = []

    @property
    def is_standby(self) -> bool:
        """True if this crew member is in the standby reserve pool."""
        return self.status == "Standby"

    def __repr__(self) -> str:
        status = "ABSENT" if self.absent else self.status
        return f"Crew({self.id}, base={self.base}, {status})"
