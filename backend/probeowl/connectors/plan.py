# Author: @SaadZarook
"""A lookup's plan in each dialect's terms, reduced to what the save checks need: index or scan."""

from dataclasses import dataclass
from enum import Enum


class Access(Enum):
    """How one plan step reads a table."""

    INDEX = "index"  # a seek or range scan on an index
    SCAN = "scan"  # every row of a table, or of a whole index
    OTHER = "other"  # joins, lookups by row id, sorts: neither of the above


@dataclass(frozen=True)
class PlanStep:
    """One step of a plan, as the database names it."""

    operation: str  # 'Clustered Index Seek', 'ref', 'INDEX RANGE SCAN'
    access: Access
    table: str | None  # as the plan names it: a table or an alias, lower case
    index: str | None = None
    table_rows: int | None = None  # the plan's own estimate of the table's size, if it has one

    def describe(self) -> str:
        """'Index Seek on ix_topups_id' or 'Table Scan of topups'."""
        if self.access is Access.INDEX and self.index:
            return f"{self.operation} on {self.index}"
        target = self.table or self.index
        return f"{self.operation} of {target}" if target else self.operation
