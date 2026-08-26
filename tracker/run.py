#!/usr/bin/env python3
"""
Entry point.

    python -m tracker.run probe        reconnaissance: dump selectors + XHR
    python -m tracker.run scrape       scrape, diff, save, notify
    python -m tracker.run scrape --dry  scrape and diff, but send nothing
    python -m tracker.run render       rebuild dashboard.html from saved data
"""

from __future__ import annotations

import json
import os
import sys

import yaml

from . import dashboard, notify, store
from .diff import compare, summarise
from .scrape import probe, scrape


def load_config(path: str = "config.yml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def cmd_probe(headless: bool | None = None) -> int:
    print("Listing KSEB districts and sections ...")
    result = probe()
    print(f"\n{len(result['districts'])} districts:")
    for name, did in sorted(result["districts"].items()):
        secs = result["sections"].get(name, {})
        n = len(secs) if isinstance(secs, dict) and "error" not in secs else "?"
        print(f"  {name:<20} id={did:<4} {n} sections")

    print("\n--- paste into config.yml ---")
    print("targets:")
    for name in sorted(result["districts"]):
        secs = result["sections"].get(name, {})
        if not isinstance(secs, dict) or "error" in secs:
            continue
        mark = "" if name.upper() == "KANNUR" else "# "
        print(f"{mark}  - district: {name}")
        print(f'{mark}    sections: "*"    # {len(secs)} sections')
    print("--- end ---")
    print("\nFull district/section listing written to probe.json")
    return 0


def cmd_scrape(dry: bool = False) -> int:
    cfg = load_config()
    targets = cfg["targets"]
    opts = cfg.get("options", {})

    print("Scraping KSEB DTR data ...")
    rows = [d.to_dict() for d in scrape(
        targets,
        delay_s=opts.get("delay_seconds", 1.5),
    )]
    if not rows:
        print("No rows scraped. Refusing to overwrite the last good snapshot.")
        print("Run `python -m tracker.run probe` -- KSEB may have changed the API.")
        return 1

    previous, prev_at = store.load_latest()
    print(f"\nScraped {len(rows)} DTRs. Previous snapshot: "
          f"{len(previous)} DTRs from {prev_at or 'never'}.")

    # Guard against a partial scrape wiping history and firing a flood of
    # bogus DTR_REMOVED alerts.
    guard = opts.get("min_row_ratio", 0.6)
    if previous and len(rows) < len(previous) * guard:
        print(f"Row count dropped below {guard:.0%} of last run. "
              "Treating as a failed scrape and stopping.")
        return 1

    changes = compare(
        previous,
        rows,
        min_kw=opts.get("min_kw", 0.5),
        watch_balance=opts.get("watch_balance", True),
    )
    summary = summarise(changes)
    captured_at = store.save_snapshot(rows)

    if previous:
        change_dicts = [c.to_dict() for c in changes]
        store.append_changes(change_dicts, captured_at)
        print(f"\n{summary['total']} change(s): {summary['by_type']}")
        print(f"Net headroom: {summary['net_headroom_kw']:+} kW")
        for c in changes[:15]:
            print(f"  {c.change_type:<16} {c.section} / {c.transformer}")
        if not dry:
            notify.dispatch(change_dicts, summary, captured_at,
                            cfg.get("dashboard_url"))
        else:
            print("\n--dry: no alerts sent")
    else:
        print("\nFirst run -- baseline saved, no alerts. "
              "Changes will be reported from the next run onward.")

    dashboard.render()
    return 0


def cmd_render() -> int:
    dashboard.render()
    print("dashboard.html rebuilt")
    return 0


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "scrape"
    if cmd == "probe":
        return cmd_probe()
    if cmd == "render":
        return cmd_render()
    if cmd == "scrape":
        return cmd_scrape(dry="--dry" in args)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
