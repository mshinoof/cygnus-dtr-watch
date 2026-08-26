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
    # GitHub's runners have no screen, so probe must go headless there.
    # Locally we default to a visible window so you can watch it work.
    if headless is None:
        headless = os.environ.get("CI", "").lower() == "true"
    print("Probing wss.kseb.in/selfservices/reCap ...")
    result = probe(headless=headless)
    print(f"\nFound {len(result['dropdowns'])} dropdown(s):")
    for d in result["dropdowns"]:
        print(f"  [{d['index']}] id={d.get('id')} name={d.get('name')}")
        print(f"       {len(d.get('options', []))} options, "
              f"first few: {d.get('options', [])[:5]}")
    print(f"\nCaptured {len(result['xhr'])} XHR call(s). See probe.json.")
    print("If one of them returns JSON, we can drop Playwright and hit it directly.")

    # Print a ready-to-paste config block for the first district.
    if result["dropdowns"] and result["dropdowns"][0].get("options"):
        first = result["dropdowns"][0]["options"][0]
        secs = next((d.get("options", []) for d in result["dropdowns"]
                     if d.get("note")), [])
        print("\n--- paste into config.yml ---")
        print("targets:")
        print(f"  - district: {first}")
        if secs:
            print("    sections:")
            for s in secs:
                print(f"      - {s}")
        else:
            print('    sections: "*"')
        print("--- end ---")
    return 0


def cmd_scrape(dry: bool = False) -> int:
    cfg = load_config()
    targets = cfg["targets"]
    opts = cfg.get("options", {})

    print("Scraping KSEB DTR data ...")
    rows = [d.to_dict() for d in scrape(
        targets,
        headless=opts.get("headless", True),
        delay_s=opts.get("delay_seconds", 3.0),
        settle_ms=opts.get("settle_ms", 2500),
    )]
    if not rows:
        print("No rows scraped. Refusing to overwrite the last good snapshot.")
        print("Run `python -m tracker.run probe` -- KSEB may have changed the page.")
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
