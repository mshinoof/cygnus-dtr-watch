"""
Reads KSEB's DTR capacity data from https://wss.kseb.in/selfservices/reCap

The page looks like a JSF app, but underneath it calls three plain JSON
endpoints. We call those directly -- no browser, no Playwright, about a minute
for a whole district instead of five.

    POST /selfservices/getDistricts                    -> {"KANNUR": 13, ...}
    POST /selfservices/getinputSection  distictid=13   -> {"Thalassery [5701]": 5701, ...}
    POST /selfservices/getDTRAvailable  sectionId=5701 -> {office:{...}, list:[...]}

(The `distictid` misspelling is KSEB's, not ours. Don't correct it.)

Each transformer record looks like:

    {"feeder_name": "Munnodi [ALAPPUZHA 66 KV]",
     "id": "550187",                  <- stable, section code + serial
     "transformer_name": "ARATTUVAZHY CHURCH",
     "capacity": "100",               <- kVA rating of the transformer
     "allowed_cap": "81 KW",          <- 90% of capacity at 0.9 pf => kVA * 0.81
     "feasible": "0",                 <- feasibility issued, kW
     "regi": "0",                     <- registered but not yet commissioned, kW
     "comp_cap": "0"}                 <- commissioned and grid-connected, kW

Balance available = allowed_cap - (feasible + regi + comp_cap).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict

BASE = "https://wss.kseb.in/selfservices"
DISTRICTS_URL = f"{BASE}/getDistricts"
SECTIONS_URL = f"{BASE}/getinputSection"
DTR_URL = f"{BASE}/getDTRAvailable"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"{BASE}/reCap",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}

SECTION_LABEL = re.compile(r"^(?P<name>.+?)\s*\[(?P<code>\d+)\]\s*$")


@dataclass
class DTR:
    district: str
    section: str
    section_code: str
    dtr_id: str
    transformer: str
    feeder: str
    kva: float              # nameplate rating
    allowed_kw: float       # 90% of capacity at 0.9 pf -- what may be sanctioned
    feasible_kw: float      # feasibility issued, not yet commissioned
    registered_kw: float    # registered applications
    connected_kw: float     # commissioned, exporting today

    @property
    def committed_kw(self) -> float:
        return round(self.feasible_kw + self.registered_kw + self.connected_kw, 3)

    @property
    def balance_kw(self) -> float:
        return round(self.allowed_kw - self.committed_kw, 3)

    @property
    def key(self) -> str:
        """
        KSEB's own transformer id, scoped by section. Stable across renames and
        across rows being reordered -- far better than matching on the name.
        """
        return f"{self.section_code}|{self.dtr_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        d["committed_kw"] = self.committed_kw
        d["balance_kw"] = self.balance_kw
        return d


def to_float(raw) -> float:
    """'81 KW' -> 81.0 ; '5.000' -> 5.0 ; '' or None -> 0.0"""
    if raw is None:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", str(raw).replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def normalise_name(name: str) -> str:
    n = str(name).upper().strip()
    n = re.sub(r"[^A-Z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _post(url: str, data: dict | None = None, retries: int = 3,
          timeout: int = 45) -> dict:
    body = urllib.parse.urlencode(data or {}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=HEADERS,
                                         method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if not text.strip():
                raise ValueError("empty response")
            return json.loads(text)
        except Exception as e:                      # noqa: BLE001
            last = e
            if attempt < retries - 1:
                # Back off before retrying: KSEB's server is occasionally slow
                # rather than down, and hammering it makes that worse.
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} failed after {retries} attempts: {last}")


def get_districts() -> dict[str, int]:
    """{'KANNUR': 13, 'KASARGODE': 14, ...}"""
    return {str(k).strip(): int(v) for k, v in _post(DISTRICTS_URL).items()}


def get_sections(district_id: int) -> dict[str, str]:
    """{'Thalassery': '5701', ...} -- the '[code]' suffix is stripped."""
    raw = _post(SECTIONS_URL, {"distictid": district_id})
    out: dict[str, str] = {}
    for label, code in raw.items():
        m = SECTION_LABEL.match(str(label))
        name = m.group("name").strip() if m else str(label).strip()
        out[name] = str(code)
    return out


def get_dtrs(district: str, section: str, section_code: str) -> list[DTR]:
    payload = _post(DTR_URL, {"sectionId": section_code})
    if payload.get("err_flag") not in (0, "0", None):
        raise RuntimeError(
            f"KSEB returned an error for {section}: {payload.get('disp_msg')}"
        )
    out: list[DTR] = []
    for r in payload.get("list") or []:
        name = str(r.get("transformer_name") or "").strip()
        if not name:
            continue
        out.append(
            DTR(
                district=district,
                section=section,
                section_code=str(section_code),
                dtr_id=str(r.get("id") or normalise_name(name)),
                transformer=name,
                feeder=str(r.get("feeder_name") or "").strip(),
                kva=to_float(r.get("capacity")),
                allowed_kw=to_float(r.get("allowed_cap")),
                feasible_kw=to_float(r.get("feasible")),
                registered_kw=to_float(r.get("regi")),
                connected_kw=to_float(r.get("comp_cap")),
            )
        )
    return out


def probe(out_path: str = "probe.json", **_) -> dict:
    """Lists districts and the sections of each. Cheap; no DTR data pulled."""
    districts = get_districts()
    result: dict = {"districts": districts, "sections": {}}
    for name, did in districts.items():
        try:
            result["sections"][name] = get_sections(did)
        except Exception as e:                      # noqa: BLE001
            result["sections"][name] = {"error": str(e)}
        time.sleep(0.5)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    return result


def scrape(targets: list[dict], delay_s: float = 1.5, **_) -> list[DTR]:
    """
    targets: [{"district": "KANNUR", "sections": "*"}, ...]
             sections may be "*" for every section in the district, or a list
             of names (matched case- and punctuation-insensitively).
    """
    districts = get_districts()
    dmap = {normalise_name(k): (k, v) for k, v in districts.items()}
    results: list[DTR] = []

    for target in targets:
        want_d = normalise_name(target["district"])
        if want_d not in dmap:
            print(f"  ! unknown district: {target['district']}")
            print(f"    available: {', '.join(sorted(districts))}")
            continue
        dname, did = dmap[want_d]

        sections = get_sections(did)
        wanted = target.get("sections", "*")
        if wanted in ("*", ["*"]):
            chosen = sections
        else:
            want = {normalise_name(w) for w in wanted}
            chosen = {n: c for n, c in sections.items() if normalise_name(n) in want}
            for w in wanted:
                if normalise_name(w) not in {normalise_name(n) for n in sections}:
                    print(f"  ! section not found in {dname}: {w}")

        print(f"  {dname}: {len(chosen)} section(s) to read")
        for name, code in sorted(chosen.items()):
            try:
                rows = get_dtrs(dname, name, code)
                results.extend(rows)
                print(f"    {name} [{code}]: {len(rows)} DTRs")
            except Exception as e:                  # noqa: BLE001
                print(f"    ! {name} [{code}] failed: {e}")
            time.sleep(delay_s)

    return results
