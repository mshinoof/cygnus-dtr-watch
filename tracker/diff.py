"""
Compares two DTR snapshots and classifies what changed.

Matching is on KSEB's own transformer id (scoped by section code), not on the
name. That means a transformer being renamed, or rows being reordered, produces
no alert at all -- which is right, because neither changes the capacity picture.

The types are chosen around one question: does this open or close a chance to
sell a rooftop connection in that section?

    DTR_UPGRADED     kVA rating went up -- a 100 kVA became a 250 kVA. The big
                     one; unlocks tens of kW of sanctionable headroom at once.
    NEW_DTR          A transformer appeared. Usually a new installation,
                     sometimes KSEB backfilling. Either way, fresh headroom.
    CAPACITY_FREED   Balance rose without a rating change -- a feasibility
                     lapsed or a sanction was released.
    CAPACITY_TAKEN   Balance fell. Someone else booked it. Time-sensitive if
                     you had a customer pending on that DTR.
    DTR_DOWNGRADED   kVA rating fell. Rare; usually a data correction, but
                     worth seeing before you promise anyone feasibility.
    DTR_REMOVED      Transformer no longer listed.
    DTR_RENAMED      Same id, different name. Informational only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChangeType = Literal[
    "DTR_UPGRADED", "NEW_DTR", "CAPACITY_FREED", "CAPACITY_TAKEN",
    "DTR_DOWNGRADED", "DTR_REMOVED", "DTR_RENAMED",
]

PRIORITY: dict[str, int] = {
    "DTR_UPGRADED": 0, "NEW_DTR": 1, "CAPACITY_FREED": 2,
    "CAPACITY_TAKEN": 3, "DTR_DOWNGRADED": 4, "DTR_REMOVED": 5,
    "DTR_RENAMED": 6,
}

HEADLINE = {
    "DTR_UPGRADED": "Transformer upgraded",
    "NEW_DTR": "New transformer listed",
    "CAPACITY_FREED": "Capacity freed up",
    "CAPACITY_TAKEN": "Capacity booked by someone else",
    "DTR_DOWNGRADED": "Transformer capacity reduced",
    "DTR_REMOVED": "Transformer no longer listed",
    "DTR_RENAMED": "Transformer renamed",
}

GAIN = {"DTR_UPGRADED", "NEW_DTR", "CAPACITY_FREED"}


@dataclass
class Change:
    change_type: ChangeType
    district: str
    section: str
    transformer: str
    feeder: str
    kva_before: float | None
    kva_after: float | None
    balance_before: float
    balance_after: float
    note: str = ""

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
            "feeder": self.feeder,
            "kva_before": self.kva_before,
            "kva_after": self.kva_after,
            "balance_before": self.balance_before,
            "balance_after": self.balance_after,
            "balance_delta": self.balance_delta,
            "note": self.note,
        }


def compare(previous: list[dict], current: list[dict],
            min_kw: float = 0.5, watch_balance: bool = True) -> list[Change]:
    prev = {r["key"]: r for r in previous}
    curr = {r["key"]: r for r in current}
    changes: list[Change] = []

    for key, now in curr.items():
        before = prev.get(key)

        if before is None:
            changes.append(Change(
                change_type="NEW_DTR", district=now["district"],
                section=now["section"], transformer=now["transformer"],
                feeder=now.get("feeder", ""), kva_before=None,
                kva_after=now["kva"], balance_before=0.0,
                balance_after=now["balance_kw"],
            ))
            continue

        kva_moved = abs(now["kva"] - before["kva"]) >= 1
        if kva_moved:
            changes.append(Change(
                change_type="DTR_UPGRADED" if now["kva"] > before["kva"]
                            else "DTR_DOWNGRADED",
                district=now["district"], section=now["section"],
                transformer=now["transformer"], feeder=now.get("feeder", ""),
                kva_before=before["kva"], kva_after=now["kva"],
                balance_before=before["balance_kw"],
                balance_after=now["balance_kw"],
            ))

        # A balance move is only reported on its own when the rating held
        # steady. After an upgrade the balance always jumps, and reporting both
        # would mean two alerts for one event.
        if watch_balance and not kva_moved:
            delta = now["balance_kw"] - before["balance_kw"]
            if abs(delta) >= min_kw:
                changes.append(Change(
                    change_type="CAPACITY_FREED" if delta > 0 else "CAPACITY_TAKEN",
                    district=now["district"], section=now["section"],
                    transformer=now["transformer"], feeder=now.get("feeder", ""),
                    kva_before=before["kva"], kva_after=now["kva"],
                    balance_before=before["balance_kw"],
                    balance_after=now["balance_kw"],
                ))

        if before["transformer"] != now["transformer"]:
            changes.append(Change(
                change_type="DTR_RENAMED", district=now["district"],
                section=now["section"], transformer=now["transformer"],
                feeder=now.get("feeder", ""), kva_before=before["kva"],
                kva_after=now["kva"], balance_before=before["balance_kw"],
                balance_after=now["balance_kw"],
                note=f"was '{before['transformer']}'",
            ))

    for key, gone in prev.items():
        if key not in curr:
            changes.append(Change(
                change_type="DTR_REMOVED", district=gone["district"],
                section=gone["section"], transformer=gone["transformer"],
                feeder=gone.get("feeder", ""), kva_before=gone["kva"],
                kva_after=None, balance_before=gone["balance_kw"],
                balance_after=0.0,
            ))

    changes.sort(key=lambda c: (PRIORITY[c.change_type],
                                -abs(c.balance_delta), c.section, c.transformer))
    return changes


def summarise(changes: list[Change]) -> dict:
    counts: dict[str, int] = {}
    for c in changes:
        counts[c.change_type] = counts.get(c.change_type, 0) + 1
    net = round(sum(
        c.balance_after if c.change_type == "NEW_DTR" else c.balance_delta
        for c in changes
        if c.change_type not in ("DTR_REMOVED", "DTR_RENAMED")
    ), 2)
    return {"total": len(changes), "by_type": counts, "net_headroom_kw": net}
