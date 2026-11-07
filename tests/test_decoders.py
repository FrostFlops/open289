import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open289.meter import BinReading, OVERLOAD, parse_meas_header, ts_text  # noqa: E402
from open289.reading import decode_notification, decode_record  # noqa: E402

# Real QDDB reply payload (after b"0\r#0", trailing CR stripped) captured 2026-11-06.
QDDB = bytes.fromhex(
    "0300 0000 0100 0100 00001440 00000000 0000 0000 00000000 00000000 0000 0000 0200"
    "0100 cb3f1440c8293a92 0100 0000 0400 0500 0200 0000 8fbbda4100508c28"
    "0200 cb3f1440c8293a92 0100 0000 0400 0500 0200 0000 8fbbda4100508c28".replace(" ", ""))


def check(cond, msg):
    assert cond, msg


def test_decoders():

    h, end = parse_meas_header(QDDB, 0)
    check(end == len(QDDB) == 94, f"QDDB length 34+30*2 ({end})")
    check((h.prim, h.autorange, h.unit, h.range_max) == ("V_DC", "AUTO", "VDC", 5.0), f"QDDB header {h.prim} {h.autorange} {h.range_max}")
    check([r.reading_id for r in h.readings] == ["LIVE", "PRIMARY"], "reading ids")
    check(h.readings[0].text() == "5.0623 V DC", f"reading text {h.readings[0].text()!r}")
    check(ts_text(h.readings[0].ts).startswith("2026-11-06"), f"timestamp {ts_text(h.readings[0].ts)}")
    try:
        parse_meas_header(QDDB[:70], 0)
        check(False, "truncated QDDB rejected")
    except ValueError:
        check(True, "truncated QDDB rejected")

    for st, want in [("OL", "OL V DC"), ("OL_MINUS", "-OL V DC"), ("OPEN_TC", "OPEN_TC V DC"),
                     ("INVALID", "---- V DC"), ("NORMAL", "OL V DC")]:
        r = BinReading("LIVE", OVERLOAD, "VDC", 0, 4, 5, st, "NONE", 0)
        check(r.text() == want and r.csv_value() == "", f"sentinel state {st} -> {r.text()!r}")
    check(BinReading("LIVE", -1.5824, "VDC", 0, 4, 5, "NORMAL", "NONE", 0).text() == "-1.5824 V DC", "negative")
    check(BinReading("LIVE", 0.004700, "ADC", -3, 3, 5, "NORMAL", "NONE", 0).text() == "4.700 mA DC", "milli prefix")

    for t in (0.0, float("nan"), float("inf"), OVERLOAD, 1e12):
        try:
            ts_text(t)
            check(True, f"ts_text({t}) does not raise")
        except Exception as e:  # noqa: BLE001
            check(False, f"ts_text({t}) raised {e!r}")

    p, s = decode_notification(bytes.fromhex("d9c50008020c0000" + "00" * 8))
    check(p.text() == "5.0649 V DC" and s is None, f"live 16B notification {p.text()!r}")
    check(decode_record(bytes.fromhex("00008028 0b280500")).text() == "OL MΩ", "live OL")
    check(decode_record(bytes.fromhex("d2040088020c0500")).text() == "-0.1234 V DC", "live negative")
