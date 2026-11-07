"""Cross-check IR3000 BLE live packets against the meter's own QDDB snapshots.

Record a capture while turning the dial / pressing keys, then analyse it:
  python -m open289 log --format jsonl --snapshot-every 0.5s --duration 10m --out capture.jsonl
  python tools/analyze_monitor.py capture.jsonl [report.txt]

Segments the capture by the meter-reported state (primary function, secondary
function, modes), then for every segment lists the BLE encodings seen and
compares each BLE value with the nearest-in-time QDDB reading.
"""
import bisect
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open289.meter import parse_meas_header  # noqa: E402
from open289.reading import FUNCTION, UNIT, decode_record  # noqa: E402

PREFIX_EXP = {"": 0, "G": 9, "M": 6, "k": 3, "m": -3, "µ": -6, "n": -9, "p": -12}


def hhmmss(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def main(path, out_path=None):
    out = open(out_path, "w", encoding="utf-8") if out_path else sys.stdout
    ev = [json.loads(line) for line in open(path, encoding="utf-8")]
    snaps = []
    for e in ev:
        if e["type"] == "qddb" and e["ack"] == "OK":
            b = bytes.fromhex(e["hex"])
            if b.startswith(b"#0"):
                h, _ = parse_meas_header(b[2:], 0)
                snaps.append((e["t"], h))
        elif e["type"] == "qddb":
            snaps.append((e["t"], e["ack"]))
    ble = []
    for e in ev:
        if e["type"] == "ble":
            d = bytes.fromhex(e["hex"])
            p = decode_record(d[:8])
            s = decode_record(d[8:16]) if len(d) >= 16 and any(d[8:16]) else None
            ble.append((e["t"], p, s, d))
    snap_t = [t for t, _ in snaps]

    def nearest_snap(t):
        i = bisect.bisect_left(snap_t, t)
        cands = [j for j in (i - 1, i) if 0 <= j < len(snaps)]
        j = min(cands, key=lambda j: abs(snap_t[j] - t))
        return snaps[j], abs(snap_t[j] - t)

    # segment by meter state
    segs = []
    for t, h in snaps:
        key = h if isinstance(h, str) else (h.prim, h.sec, tuple(h.mode), h.autorange)
        if not segs or segs[-1]["key"] != key:
            segs.append({"key": key, "t0": t, "t1": t, "snaps": []})
        segs[-1]["t1"] = t
        segs[-1]["snaps"].append((t, h))

    problems = collections.Counter()
    for sg in segs:
        key = sg["key"]
        t0, t1 = sg["t0"], sg["t1"]
        pk = [x for x in ble if t0 - 0.3 <= x[0] <= t1 + 0.3]
        if isinstance(key, str):
            print(f"\n### {hhmmss(t0)}-{hhmmss(t1)}  QDDB -> {key}  ({len(pk)} BLE packets)", file=out)
            continue
        prim, sec, mode, ar = key
        h_last = sg["snaps"][-1][1]
        print(f"\n### {hhmmss(t0)}-{hhmmss(t1)}  meter: {prim} / {sec}  {ar} {h_last.range_max:g}  "
              f"modes={','.join(mode)}  ({len(sg['snaps'])} snaps, {len(pk)} BLE)", file=out)
        rids = collections.Counter(r.reading_id for _, h in sg["snaps"] for r in h.readings)
        print(f"    QDDB readings: {dict(rids)}", file=out)
        ex = h_last.readings
        print("    last QDDB: " + "  ".join(f"{r.reading_id}={r.text()}({r.unit},{r.state},{r.attribute})" for r in ex),
              file=out)
        enc = collections.Counter()
        for _, p, s, _d in pk:
            k = (p.function, FUNCTION.get(p.function, "?"), p.unit, UNIT.get(p.unit, "?"), p.state_name,
                 p.attribute, None if s is None else (s.function, s.unit, s.state_name))
            enc[k] += 1
        for k, n in enc.most_common():
            print(f"    BLE x{n:<4} prim func={k[0]}({k[1]}) unit={k[2]}({k[3]}) state={k[4]} attr={k[5]}"
                  + (f"  2nd func={k[6][0]} unit={k[6][1]}({UNIT.get(k[6][1], '?')}) st={k[6][2]}" if k[6] else ""),
                  file=out)
        # value comparison: BLE primary vs QDDB PRIMARY (or the LIVE reading), nearest in time
        shown = 0
        for t, p, s, d in pk:
            (st, h), dt = nearest_snap(t)
            if isinstance(h, str) or dt > 0.6:
                continue
            q = {r.reading_id: r for r in h.readings}
            ref = q.get("PRIMARY") or q.get("LIVE")
            if ref is None:
                continue
            ble_txt = p.text()
            qd_txt = ref.text()
            ok_val = None
            if p.value is not None and ref.is_number:
                ok_val = abs(p.value - ref.value) <= max(abs(ref.value) * 0.02, 10 ** (-ref.decimals + ref.multiplier) * 30)
            ok_state = (p.state_name in ("OL_RANGE", "OL_A2D")) == (ref.state in ("OL", "OL_MINUS"))
            flag = ""
            if ok_val is False:
                flag += " VALUE?"
            if not ok_state:
                flag += " STATE?"
            sec_q = q.get("SECONDARY")
            if (s is None) != (sec_q is None):
                flag += " SECONDARY-PRESENCE?"
            if flag:
                problems[(prim, sec, flag.strip())] += 1
            if shown < 3 or (flag and shown < 6):
                shown += 1
                line = f"      {hhmmss(t)} BLE {ble_txt:<22} | QDDB {ref.reading_id} {qd_txt:<22}"
                if s is not None or sec_q is not None:
                    line += f" || BLE2 {s.text() if s else '-':<18} | QDDB2 {sec_q.text() if sec_q else '-'}"
                line += f"  [{d.hex(' ')}]{flag}"
                print(line, file=out)
    print("\n=== flagged mismatches by segment ===", file=out)
    for k, n in problems.most_common():
        print(f"  {n:5d}  {k}", file=out)
    if not problems:
        print("  none", file=out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
