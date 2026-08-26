"""
Scrapes https://wss.kseb.in/selfservices/reCap

The page is a JSF/PrimeFaces app: picking District then Section fires an AJAX
request that fills the DTR table. Rather than reverse-engineering the JSF
ViewState protocol (which breaks whenever KSEB redeploys), we drive a real
browser with Playwright and read the rendered table.

Two modes:
    probe()   -- one-off reconnaissance. Dumps every dropdown, its options and
                 its real DOM id, plus any XHR the page fires. Run this once
                 to confirm selectors and to discover whether there is a clean
                 JSON endpoint we can hit directly later.
    scrape()  -- the real run. Walks the configured district/section list and
                 returns normalised DTR rows.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Iterable

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

URL = "https://wss.kseb.in/selfservices/reCap"

# Column order on the KSEB table. Index 0 is the serial number, which we drop
# because it is positional and reshuffles whenever a DTR is added.
COLUMNS = [
    "sl",
    "transformer",
    "capacity_90pct_kw",
    "feasibility_issued_kw",
    "grid_connected_kw",
    "balance_available_kw",
]

NUMERIC = (
    "capacity_90pct_kw",
    "feasibility_issued_kw",
    "grid_connected_kw",
    "balance_available_kw",
)


@dataclass
class DTR:
    district: str
    section: str
    transformer: str
    capacity_90pct_kw: float
    feasibility_issued_kw: float
    grid_connected_kw: float
    balance_available_kw: float

    @property
    def key(self) -> str:
        """Stable identity for a transformer across snapshots."""
        return f"{self.district}|{self.section}|{normalise_name(self.transformer)}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


def normalise_name(name: str) -> str:
    """
    KSEB's operators type transformer names by hand, so the same DTR shows up as
    'KOODALI TOWN', 'Koodali  Town' and 'KOODALI TOWN ' across snapshots. Fold
    those together so we don't fire a false 'new transformer' alert.
    """
    n = name.upper().strip()
    n = re.sub(r"[^A-Z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def to_float(raw: str) -> float:
    """'12.50 kW' -> 12.5 ; '-' or '' -> 0.0 ; '1,250' -> 1250.0"""
    if raw is None:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", raw.replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------
# Dropdown handling
#
# PrimeFaces renders <select> elements but usually hides them behind a styled
# widget. The hidden native select is still in the DOM and still drives the
# AJAX, so select_option() on it works and is far more stable than clicking
# through the fake widget. We fall back to the widget only if that fails.
# --------------------------------------------------------------------------

def _selects(page: Page):
    return page.locator("select")


def _options(page: Page, index: int) -> list[str]:
    opts = page.locator("select").nth(index).locator("option")
    out = []
    for i in range(opts.count()):
        label = (opts.nth(i).inner_text() or "").strip()
        if label and not re.match(r"^(select|--|choose)", label, re.I):
            out.append(label)
    return out


def _choose(page: Page, index: int, label: str, settle_ms: int) -> None:
    page.locator("select").nth(index).select_option(label=label)
    page.wait_for_timeout(settle_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass  # JSF keeps a long-poll open on some deployments


def _read_table(page: Page) -> list[list[str]]:
    """
    Reads the DTR table. Handles PrimeFaces paginated tables by clicking through
    'Next' until the page number stops advancing.
    """
    rows: list[list[str]] = []
    seen_pages = 0
    while True:
        page.wait_for_timeout(400)
        body = page.locator("table tbody tr")
        for i in range(body.count()):
            cells = body.nth(i).locator("td")
            if cells.count() < 5:
                continue
            rows.append([(cells.nth(c).inner_text() or "").strip()
                         for c in range(cells.count())])

        nxt = page.locator(
            "a.ui-paginator-next:not(.ui-state-disabled), "
            "button:has-text('Next'):not([disabled])"
        )
        seen_pages += 1
        if nxt.count() == 0 or seen_pages > 60:
            break
        try:
            nxt.first.click(timeout=3_000)
            page.wait_for_timeout(800)
        except Exception:
            break
    return rows


def _rows_to_dtrs(rows: Iterable[list[str]], district: str, section: str) -> list[DTR]:
    out: list[DTR] = []
    for r in rows:
        # Some deployments omit the Sl# column. Detect by checking whether the
        # first cell is a bare integer and the second is text.
        cells = list(r)
        if cells and re.fullmatch(r"\d+\.?", cells[0]) and len(cells) >= 6:
            cells = cells[1:]
        if len(cells) < 5:
            continue
        name = cells[0]
        if not name or name.lower().startswith(("total", "grand")):
            continue
        out.append(
            DTR(
                district=district,
                section=section,
                transformer=name,
                capacity_90pct_kw=to_float(cells[1]),
                feasibility_issued_kw=to_float(cells[2]),
                grid_connected_kw=to_float(cells[3]),
                balance_available_kw=to_float(cells[4]),
            )
        )
    return out


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def probe(headless: bool = False, out_path: str = "probe.json") -> dict:
    """
    Run this once, with headless=False, and watch what happens. It writes
    probe.json containing every dropdown's id and options plus every XHR the
    page fired -- that tells us whether a direct JSON endpoint exists.
    """
    captured: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch"):
                entry = {
                    "url": resp.url,
                    "method": resp.request.method,
                    "status": resp.status,
                    "post_data": (resp.request.post_data or "")[:4000],
                }
                try:
                    entry["body_head"] = resp.text()[:4000]
                except Exception:
                    entry["body_head"] = "<binary or unavailable>"
                captured.append(entry)

        page.on("response", on_response)
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3_000)

        dropdowns = []
        sel = _selects(page)
        for i in range(sel.count()):
            el = sel.nth(i)
            dropdowns.append(
                {
                    "index": i,
                    "id": el.get_attribute("id"),
                    "name": el.get_attribute("name"),
                    "options": _options(page, i)[:60],
                }
            )

        # Exercise one district so we capture the section-load XHR too.
        if dropdowns and dropdowns[0]["options"]:
            _choose(page, 0, dropdowns[0]["options"][0], 2_500)
            page.wait_for_timeout(2_500)
            dropdowns.append(
                {
                    "index": 1,
                    "id": _selects(page).nth(1).get_attribute("id") if sel.count() > 1 else None,
                    "note": "sections after selecting first district",
                    "options": _options(page, 1)[:60] if sel.count() > 1 else [],
                }
            )

        result = {"dropdowns": dropdowns, "xhr": captured}
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        browser.close()
    return result


def scrape(
    targets: list[dict],
    headless: bool = True,
    delay_s: float = 3.0,
    settle_ms: int = 2_500,
) -> list[DTR]:
    """
    targets: [{"district": "KANNUR", "sections": ["KANNUR", "THALASSERY"]}, ...]
             sections may be the string "*" to take every section in the district.
    """
    results: list[DTR] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
                "CygnusEnergy-DTRTracker/1.0"
            )
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2_000)

        for target in targets:
            district = target["district"]
            _choose(page, 0, district, settle_ms)

            wanted = target.get("sections", "*")
            available = _options(page, 1)
            sections = available if wanted in ("*", ["*"]) else [
                s for s in available
                if normalise_name(s) in {normalise_name(w) for w in wanted}
            ]
            missing = ([] if wanted in ("*", ["*"]) else
                       [w for w in wanted
                        if normalise_name(w) not in {normalise_name(s) for s in available}])
            for m in missing:
                print(f"  ! section not found in {district}: {m}")

            for section in sections:
                try:
                    _choose(page, 1, section, settle_ms)
                    rows = _read_table(page)
                    dtrs = _rows_to_dtrs(rows, district, section)
                    results.extend(dtrs)
                    print(f"  {district} / {section}: {len(dtrs)} DTRs")
                except Exception as e:
                    print(f"  ! failed {district}/{section}: {e}")
                time.sleep(delay_s)

        browser.close()
    return results
