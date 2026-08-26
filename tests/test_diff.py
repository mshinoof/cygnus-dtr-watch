import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tracker.diff import compare, summarise
from tracker.scrape import normalise_name, to_float, _rows_to_dtrs


def dtr(name, cap, feas, grid, bal, section="KANNUR", district="KANNUR"):
    return {
        "key": f"{district}|{section}|{normalise_name(name)}",
        "district": district,
        "section": section,
        "transformer": name,
        "capacity_90pct_kw": cap,
        "feasibility_issued_kw": feas,
        "grid_connected_kw": grid,
        "balance_available_kw": bal,
    }


def test_new_transformer():
    prev = [dtr("KOODALI TOWN", 90.0, 10, 20, 60)]
    curr = prev + [dtr("PALLIKKUNNU NEW", 225.0, 0, 0, 225.0)]
    ch = compare(prev, curr)
    assert len(ch) == 1
    assert ch[0].change_type == "NEW_DTR"
    assert ch[0].new_value == 225.0
    assert ch[0].balance_after == 225.0


def test_upgrade_detected_and_beats_balance_noise():
    prev = [dtr("KOODALI TOWN", 90.0, 10, 20, 60.0)]
    curr = [dtr("KOODALI TOWN", 225.0, 10, 20, 195.0)]
    ch = compare(prev, curr)
    # one alert, not two -- the balance jump is a consequence of the upgrade
    assert len(ch) == 1
    assert ch[0].change_type == "DTR_UPGRADED"
    assert ch[0].delta == 135.0
    assert ch[0].balance_delta == 135.0


def test_capacity_taken_by_competitor():
    prev = [dtr("EDAKKAD", 90.0, 10, 20, 60.0)]
    curr = [dtr("EDAKKAD", 90.0, 25, 20, 45.0)]
    ch = compare(prev, curr)
    assert [c.change_type for c in ch] == ["CAPACITY_TAKEN"]
    assert ch[0].balance_delta == -15.0


def test_capacity_freed():
    prev = [dtr("EDAKKAD", 90.0, 30, 20, 40.0)]
    curr = [dtr("EDAKKAD", 90.0, 5, 20, 65.0)]
    ch = compare(prev, curr)
    assert ch[0].change_type == "CAPACITY_FREED"
    assert ch[0].balance_delta == 25.0


def test_rounding_noise_ignored():
    prev = [dtr("EDAKKAD", 90.0, 30, 20, 40.00)]
    curr = [dtr("EDAKKAD", 90.0, 30, 20, 40.02)]
    assert compare(prev, curr, min_kw=0.5) == []


def test_name_variants_are_same_transformer():
    prev = [dtr("KOODALI  TOWN ", 90.0, 10, 20, 60.0)]
    curr = [dtr("Koodali-Town", 90.0, 10, 20, 60.0)]
    assert compare(prev, curr) == []


def test_removed():
    prev = [dtr("OLD DTR", 90.0, 0, 0, 90.0)]
    ch = compare(prev, [])
    assert ch[0].change_type == "DTR_REMOVED"


def test_priority_ordering():
    prev = [dtr("A", 90, 0, 0, 90), dtr("B", 90, 0, 0, 90)]
    curr = [dtr("A", 90, 20, 0, 70), dtr("B", 225, 0, 0, 225)]
    ch = compare(prev, curr)
    assert ch[0].change_type == "DTR_UPGRADED"      # B first
    assert ch[1].change_type == "CAPACITY_TAKEN"


def test_summary_net_headroom():
    prev = [dtr("A", 90, 0, 0, 90)]
    curr = [dtr("A", 225, 0, 0, 225), dtr("B", 160, 0, 0, 160)]
    s = summarise(compare(prev, curr))
    assert s["total"] == 2
    assert s["net_headroom_kw"] == 135.0 + 160.0


def test_number_parsing():
    assert to_float("12.50 kW") == 12.5
    assert to_float("-") == 0.0
    assert to_float("") == 0.0
    assert to_float("1,250") == 1250.0
    assert to_float(None) == 0.0


def test_row_parsing_drops_serial_and_totals():
    rows = [
        ["1", "KOODALI TOWN", "90.00", "10.00", "20.00", "60.00"],
        ["2", "EDAKKAD", "225.00", "0.00", "0.00", "225.00"],
        ["", "Total", "315.00", "10.00", "20.00", "285.00"],
    ]
    out = _rows_to_dtrs(rows, "KANNUR", "KANNUR")
    assert len(out) == 2
    assert out[0].transformer == "KOODALI TOWN"
    assert out[1].balance_available_kw == 225.0


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
