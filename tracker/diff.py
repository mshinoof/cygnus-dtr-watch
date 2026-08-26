"""
Compares two snapshots of DTR data and classifies what changed.

The change types are chosen around one question: does this open or close a
selling opportunity for a rooftop solar connection in that section?

    NEW_DTR             A transformer appeared that wasn't listed before.
                        Usually a genuinely new installation, sometimes KSEB
                        backfilling data. Either way: fresh headroom.
    DTR_UPGRADED        90%-of-capacity went UP. The transformer was swapped
                        for a bigger one (e.g. 100 kVA -> 250 kVA). This is the
                        big one -- it can unlock tens of kW at a stroke.
    DTR_DOWNGRADED      90%-of-capacity went DOWN. Rare; usually a data
                        correction, but worth knowing before you promise a
                        customer feasibility.
    CAPACITY_FREED      Balance available went UP without a capacity change --
                        someone's feasibility lapsed or a sanction was released.
    CAPACITY_TAKEN      Balance available went DOWN. A competitor booked it.
                        Time-sensitive if you had a pending customer there.
    DTR_REMOVED         A transformer disappeared from the listing.

Every comparison is done on the normalised transformer key, never on the Sl#
column, because KSEB reshuffles serial numbers whenever a row is inserted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChangeType = Literal[
    "NEW_DTR",
    "DTR_UPGRADED",
    "DTR_DOWNGRADED",
    "CAPACITY_FREED",
    "CAPACITY_TAKEN",
    "DTR_REMOVED",
]

# Priority order for reporting: most commercially useful first.
PRIORITY: dict[str, int] = {
    "DTR_UPGRADED": 0,
    "NEW_DTR": 1,
    "CAPACITY_FREED": 2,
    "CAPACITY_TAKEN": 3,
    "DTR_DOWNGRADED": 4,
    "DTR_REMOVED": 5,
}

HEADLINE = {
    "DTR_UPGRADED": "Transformer upgraded",
    "NEW_DTR": "New transformer listed",
    "CAPACITY_FREED": "Capacity freed up",
    "CAPACITY_TAKEN": "Capacity booked by someone else",
    "DTR_DOWNGRADED": "Transformer capacity reduced",
    "DTR_REMOVED": "Transformer no longer listed",
}


@dataclass
class Change:
    change_type: ChangeType
    district: str
    section: str
    transformer: str
    field: str | None
    old_value: float | None
    new_value: float | None
    balance_before: float
    balance_after: float

    @property
    def delta(self) -> float:
        if self.old_value is None or self.new_value is None:
            return 0.0
        return round(self.new_value - self.old_value, 2)

    @property
    def balance_delta(self) -> float:
        return round(self.balance_after - self.balance_before, 2)

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type,
            "headline": HEADLINE[self.change_type],
            "district": self.district,
            "section": self.section,
            "transformer": self.transformer,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "delta": self.delta,
            "balance_before": self.balance_before,
            "balance_after": self.balance_after,
            "balance_delta": self.balance_delta,
        }


def compare(
    previous: list[dict],
    current: list[dict],
    min_kw: float = 0.5,
    watch_balance: bool = True,
) -> list[Change]:
    """
    previous / current: lists of DTR dicts (as produced by DTR.to_dict()).
    min_kw: ignore movements smaller than this, so rounding noise in KSEB's
            own figures doesn't page you at 6am over 0.02 kW.
    """
    prev = {r["key"]: r for r in previous}
    curr = {r["key"]: r for r in current}
    changes: list[Change] = []

    for key, now in curr.items():
        before = prev.get(key)

        if before is None:
            changes.append(
                Change(
                    change_type="NEW_DTR",
                    district=now["district"],
                    section=now["section"],
                    transformer=now["transformer"],
                    field="capacity_90pct_kw",
                    old_value=None,
                    new_value=now["capacity_90pct_kw"],
                    balance_before=0.0,
                    balance_after=now["balance_available_kw"],
                )
            )
            continue

        cap_old = before["capacity_90pct_kw"]
        cap_new = now["capacity_90pct_kw"]
        cap_moved = abs(cap_new - cap_old) >= min_kw

        if cap_moved:
            changes.append(
                Change(
                    change_type="DTR_UPGRADED" if cap_new > cap_old else "DTR_DOWNGRADED",
                    district=now["district"],
                    section=now["section"],
                    transformer=now["transformer"],
                    field="capacity_90pct_kw",
                    old_value=cap_old,
                    new_value=cap_new,
                    balance_before=before["balance_available_kw"],
                    balance_after=now["balance_available_kw"],
                )
            )

        # Only report a balance move on its own if the capacity itself held
        # steady -- otherwise it's just a consequence of the upgrade we already
        # reported, and we'd be sending two alerts for one event.
        if watch_balance and not cap_moved:
            bal_old = before["balance_available_kw"]
            bal_new = now["balance_available_kw"]
            if abs(bal_new - bal_old) >= min_kw:
                changes.append(
                    Change(
                        change_type="CAPACITY_FREED" if bal_new > bal_old else "CAPACITY_TAKEN",
                        district=now["district"],
                        section=now["section"],
                        transformer=now["transformer"],
                        field="balance_available_kw",
                        old_value=bal_old,
                        new_value=bal_new,
                        balance_before=bal_old,
                        balance_after=bal_new,
                    )
                )

    for key, gone in prev.items():
        if key not in curr:
            changes.append(
                Change(
                    change_type="DTR_REMOVED",
                    district=gone["district"],
                    section=gone["section"],
                    transformer=gone["transformer"],
                    field="capacity_90pct_kw",
                    old_value=gone["capacity_90pct_kw"],
                    new_value=None,
                    balance_before=gone["balance_available_kw"],
                    balance_after=0.0,
                )
            )

    changes.sort(
        key=lambda c: (
            PRIORITY[c.change_type],
            -abs(c.balance_delta or 0),
            c.section,
            c.transformer,
        )
    )
    return changes


def summarise(changes: list[Change]) -> dict:
    """Counts by type plus net kW of headroom gained or lost across the run."""
    counts: dict[str, int] = {}
    for c in changes:
        counts[c.change_type] = counts.get(c.change_type, 0) + 1
    net = round(
        sum(
            (c.new_value or 0.0) if c.change_type == "NEW_DTR" else c.balance_delta
            for c in changes
            if c.change_type != "DTR_REMOVED"
        ),
        2,
    )
    return {"total": len(changes), "by_type": counts, "net_headroom_kw": net}
