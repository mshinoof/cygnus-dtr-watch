import sys, os, json, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tracker.scrape import DTR, to_float, normalise_name, SECTION_LABEL
from tracker.diff import compare, summarise

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_alappuzha_north.json")


def d(dtr_id, name, kva, allowed, feas=0.0, regi=0.0, conn=0.0,
      section="Thalassery", code="5701", district="KANNUR", feeder="F1"):
    return DTR(district=district, section=section, section_code=code,
               dtr_id=dtr_id, transformer=name, feeder=feeder, kva=kva,
               allowed_kw=allowed, feasible_kw=feas, registered_kw=regi,
               connected_kw=conn).to_dict()


# ---- parsing real KSEB payloads ------------------------------------------

def test_parses_real_kseb_payload():
    from tracker.scrape import get_dtrs
    payload = json.load(open(FIXTURE))
    rows = []
    for r in payload["list"]:
        rows.append(DTR(
            district="ALAPUZHA", section="Alappuzha North", section_code="5501",
            dtr_id=str(r["id"]), transformer=r["transformer_name"],
            feeder=r["feeder_name"], kva=to_float(r["capacity"]),
            allowed_kw=to_float(r["allowed_cap"]), feasible_kw=to_float(r["feasible"]),
            registered_kw=to_float(r["regi"]), connected_kw=to_float(r["comp_cap"])))
    assert len(rows) == 17
    first = rows[0]
    assert first.transformer == "ARATTUVAZHY CHURCH"
    assert first.kva == 100.0
    assert first.allowed_kw == 81.0
    assert first.balance_kw == 81.0
    assert first.key == "5501|550187"

    ash = next(r for r in rows if r.transformer == "ASHRAMAM")
    assert ash.kva == 250.0 and ash.allowed_kw == 202.0
    assert ash.committed_kw == 191.115          # 4 + 34 + 153.115
    assert ash.balance_kw == 10.885


def test_allowed_cap_is_81pct_of_kva():
    payload = json.load(open(FIXTURE))
    for r in payload["list"]:
        kva, allowed = to_float(r["capacity"]), to_float(r["allowed_cap"])
        assert abs(allowed - int(kva * 0.81)) <= 1, (kva, allowed)


def test_units_parsed_from_kw_string():
    assert to_float("81 KW") == 81.0
    assert to_float("129 KW") == 129.0
    assert to_float("5.000") == 5.0
    assert to_float("0") == 0.0
    assert to_float("") == 0.0 and to_float(None) == 0.0


def test_section_label_split():
    m = SECTION_LABEL.match("Kayamkulam East [5531]")
    assert m.group("name") == "Kayamkulam East" and m.group("code") == "5531"
    assert SECTION_LABEL.match("Cherthala East [5704]").group("code") == "5704"


# ---- change detection ----------------------------------------------------

def test_upgrade_fires_once_not_twice():
    prev = [d("101", "KOODALI TOWN", 100, 81, conn=20)]
    curr = [d("101", "KOODALI TOWN", 250, 202, conn=20)]
    ch = compare(prev, curr)
    assert len(ch) == 1
    assert ch[0].change_type == "DTR_UPGRADED"
    assert (ch[0].kva_before, ch[0].kva_after) == (100, 250)
    assert ch[0].balance_delta == 121.0


def test_new_transformer():
    ch = compare([], [d("909", "AIRPORT ROAD NEW", 250, 202)])
    assert ch[0].change_type == "NEW_DTR" and ch[0].balance_after == 202.0


def test_capacity_taken():
    prev = [d("101", "EDAKKAD", 160, 129, feas=5)]
    curr = [d("101", "EDAKKAD", 160, 129, feas=23.4)]
    ch = compare(prev, curr)
    assert [c.change_type for c in ch] == ["CAPACITY_TAKEN"]
    assert ch[0].balance_delta == -18.4


def test_capacity_freed():
    prev = [d("101", "EDAKKAD", 160, 129, feas=30)]
    curr = [d("101", "EDAKKAD", 160, 129, feas=5)]
    assert compare(prev, curr)[0].change_type == "CAPACITY_FREED"


def test_rename_is_not_new_plus_removed():
    """The whole point of keying on KSEB's id."""
    prev = [d("101", "KOODALI TOWN", 160, 129)]
    curr = [d("101", "KOODALI TOWN JN", 160, 129)]
    ch = compare(prev, curr)
    assert len(ch) == 1
    assert ch[0].change_type == "DTR_RENAMED"
    assert "KOODALI TOWN" in ch[0].note


def test_rounding_noise_ignored():
    prev = [d("101", "EDAKKAD", 160, 129, feas=30.00)]
    curr = [d("101", "EDAKKAD", 160, 129, feas=30.02)]
    assert compare(prev, curr, min_kw=0.5) == []


def test_removed():
    ch = compare([d("101", "OLD DTR", 100, 81)], [])
    assert ch[0].change_type == "DTR_REMOVED"


def test_same_name_different_sections_do_not_collide():
    a = d("1", "TOWN", 160, 129, section="Thalassery", code="5701")
    b = d("1", "TOWN", 160, 129, section="Panoor", code="5702")
    assert compare([a, b], [a, b]) == []
    assert a["key"] != b["key"]


def test_priority_puts_upgrades_first():
    prev = [d("1", "A", 160, 129), d("2", "B", 160, 129)]
    curr = [d("1", "A", 160, 129, feas=20), d("2", "B", 250, 202)]
    ch = compare(prev, curr)
    assert ch[0].change_type == "DTR_UPGRADED"


def test_summary_net_headroom():
    prev = [d("1", "A", 100, 81)]
    curr = [d("1", "A", 250, 202), d("2", "B", 160, 129)]
    s = summarise(compare(prev, curr))
    assert s["total"] == 2
    assert s["net_headroom_kw"] == 121.0 + 129.0


def test_rename_excluded_from_net_headroom():
    prev = [d("1", "A", 160, 129)]
    curr = [d("1", "A RENAMED", 160, 129)]
    assert summarise(compare(prev, curr))["net_headroom_kw"] == 0.0


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1; print(f"  FAIL  {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
