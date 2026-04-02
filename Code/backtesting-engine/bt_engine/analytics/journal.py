"""Audit journal: in-memory trail of all engine actions."""

from dataclasses import dataclass, field


@dataclass
class JournalEntry:
    """A single recorded event in the audit trail."""

    timestamp_us: int
    entry_type: str    # "ORDER_SUBMIT", "ORDER_ACTIVE", "FILL", "CANCEL", "SETTLEMENT", etc.
    data: dict


class AuditJournal:
    """In-memory audit trail of all engine actions.

    Records are appended in chronological order. Use entry_type to filter.
    """

    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []

    def record(self, timestamp_us: int, entry_type: str, **kwargs) -> None:
        """Append an entry to the journal."""
        self.entries.append(
            JournalEntry(timestamp_us=timestamp_us, entry_type=entry_type, data=kwargs)
        )

    def filter(self, entry_type: str) -> list[JournalEntry]:
        """Return all entries of a given type."""
        return [e for e in self.entries if e.entry_type == entry_type]

    def __len__(self) -> int:
        return len(self.entries)
