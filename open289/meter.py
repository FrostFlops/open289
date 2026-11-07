"""Fluke 287/289 remote protocol on top of the IR3000 pass-through.

Reply framing: b"<ack>\\r" [payload] b"\\r". ack: 0 OK, 1 syntax error,
2 execution error (e.g. meter in setup), 5 no data. Binary payloads begin with
b"#0". Integers are little-endian; doubles are two LE u32 words, high word
first. Layouts: notes/06-web-research.md section 4 (confirmed against
live QDDB captures from this meter).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import math
import struct
from dataclasses import dataclass, field

from .adapter import Adapter, AdapterError
from .errors import Open289Error

ACK = {b"0": "OK", b"1": "SYNTAX_ERROR", b"2": "EXECUTION_ERROR", b"5": "NO_DATA"}

# qemap tables (identical across 287/289 firmware per public captures); can be
# refreshed at runtime with Meter.load_maps().
MAPS: dict[str, dict[int, str]] = {
    "primfunction": dict(enumerate(
        "LIMBO V_AC MV_AC V_DC MV_DC V_AC_OVER_DC V_DC_OVER_AC V_AC_PLUS_DC MV_AC_OVER_DC "
        "MV_DC_OVER_AC MV_AC_PLUS_DC A_AC MA_AC UA_AC A_DC MA_DC UA_DC A_AC_OVER_DC A_DC_OVER_AC "
        "A_AC_PLUS_DC MA_AC_OVER_DC MA_DC_OVER_AC MA_AC_PLUS_DC UA_AC_OVER_DC UA_DC_OVER_AC "
        "UA_AC_PLUS_DC TEMPERATURE OHMS CONDUCTANCE CONTINUITY CAPACITANCE DIODE_TEST V_AC_LOZ "
        "OHMS_LOW CAL_V_DC_LOZ CAL_AD_GAIN_X2 CAL_AD_GAIN_X1 CAL_RMS CAL_FILT_AMP CAL_DC_AMP_X5 "
        "CAL_DC_AMP_X10 CAL_NINV_AC_AMP CAL_ISRC_500NA CAL_COMP_TRIM_MV_DC CAL_ACDC_AC_COMP "
        "CAL_V_AC_LOZ CAL_V_AC_PEAK CAL_MV_AC_PEAK CAL_TEMPERATURE".split())),
    "secfunction": dict(enumerate(
        "NONE HERTZ DUTY_CYCLE PULSE_WIDTH DBM DBV DBM_HERTZ DBV_HERTZ CREST_FACTOR PEAK_MIN_MAX".split())),
    "autorange": {0: "MANUAL", 1: "AUTO"},
    "unit": dict(enumerate(
        "NONE VDC VAC VAC_PLUS_DC V ADC AAC AAC_PLUS_DC A OHM SIE Hz S F CEL FAR PCT dB dBV dBm "
        "CREST_FACTOR".split())),
    "bolt": {0: "OFF", 1: "ON"},
    "readingid": {1: "LIVE", 2: "PRIMARY", 3: "SECONDARY", 4: "REL_LIVE", 5: "BARGRAPH",
                  7: "MINIMUM", 8: "MAXIMUM", 9: "AVERAGE", 11: "REL_REFERENCE", 12: "DB_REF",
                  13: "TEMP_OFFSET"},
    "state": dict(enumerate("INACTIVE INVALID NORMAL BLANK DISCHARGE OL OL_MINUS OPEN_TC".split())),
    "attribute": dict(enumerate(
        "NONE OPEN_CIRCUIT SHORT_CIRCUIT GLITCH_CIRCUIT GOOD_DIODE LO_OHMS NEGATIVE_EDGE "
        "POSITIVE_EDGE HIGH_CURRENT".split())),
    "recordtype": {0: "INPUT", 1: "INTERVAL"},
    "isstableflag": {0: "UNSTABLE", 1: "STABLE"},
    "transientstate": {0: "NON_T", 1: "RANGE_UP", 2: "RANGE_DOWN", 3: "OVERLOAD", 4: "OPEN_TC"},
}
MODE_BITS = {1: "AUTO_HOLD", 2: "AUTO_SAVE", 4: "HOLD", 8: "LOW_PASS_FILTER", 16: "MIN_MAX_AVG",
             32: "RECORD", 64: "REL", 128: "REL_PERCENT", 256: "CALIBRATION"}
UNIT_SYMBOL = {"VDC": "V DC", "VAC": "V AC", "VAC_PLUS_DC": "V AC+DC", "V": "V", "ADC": "A DC",
               "AAC": "A AC", "AAC_PLUS_DC": "A AC+DC", "A": "A", "OHM": "Ω", "SIE": "S",
               "Hz": "Hz", "S": "s", "F": "F", "CEL": "°C", "FAR": "°F", "PCT": "%", "dB": "dB",
               "dBV": "dBV", "dBm": "dBm", "CREST_FACTOR": "CF", "NONE": ""}
PREFIX = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}
OVERLOAD = 9.99999999e37


_ACK_CODES = {
    "EXECUTION_ERROR": ("METER_BUSY", "the meter is on a menu, result page or setup screen: close it on the meter "
                                      "(or with open289 press), then retry"),
    "NO_DATA": ("NO_DATA", "the meter has no data for this request (e.g. no saved item with that index)"),
    "SYNTAX_ERROR": ("METER_ERROR", "the meter did not accept the command"),
    "NO_REPLY": ("METER_ERROR", "the meter did not answer: is it switched on and is the adapter seated on its IR port?"),
}


class MeterError(Open289Error):
    """A meter reply other than ACK 0. ack: OK / SYNTAX_ERROR / EXECUTION_ERROR / NO_DATA / NO_REPLY / ..."""

    def __init__(self, cmd: str, ack: str, raw: bytes = b""):
        code, hint = _ACK_CODES.get(ack, ("PROTOCOL_ERROR", None))
        super().__init__(code, f"{cmd!r} -> {ack}", hint)
        self.cmd, self.ack, self.raw = cmd, ack, raw


def lookup(name: str, code: int) -> str:
    return MAPS.get(name, {}).get(code, f"{name}#{code}")


def modes(mask: int) -> list[str]:
    return [n for b, n in MODE_BITS.items() if mask & b] or ["NONE"]


def fdouble(b: bytes, off: int) -> float:
    hi, lo = struct.unpack_from("<II", b, off)
    return struct.unpack(">d", struct.pack(">II", hi, lo))[0]


def ts_text(t: float) -> str:
    """Meter clock is local wall-clock time encoded as an epoch (no timezone)."""
    if not math.isfinite(t) or t < 0.1:
        return "-"
    try:
        return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (OverflowError, OSError, ValueError):
        return repr(t)


@dataclass
class BinReading:
    reading_id: str
    value: float
    unit: str
    multiplier: int
    decimals: int
    digits: int
    state: str
    attribute: str
    ts: float

    @property
    def is_number(self) -> bool:
        """True when value is a real measurement (not OL / invalid / sentinel)."""
        return self.state == "NORMAL" and math.isfinite(self.value) and abs(self.value) < OVERLOAD

    def csv_value(self, extra_digits: int = 0) -> float | str:
        """Value in base units rounded to the meter's resolution, '' if not a number."""
        if not self.is_number:
            return ""
        return round(self.value, max(self.decimals - self.multiplier + extra_digits, 0))

    def text(self) -> str:
        sym = UNIT_SYMBOL.get(self.unit, self.unit)
        pre = PREFIX.get(self.multiplier, f"e{self.multiplier}")
        if self.state == "OL_MINUS":
            num = "-OL"
        elif self.state == "OL":
            num = "OL"
        elif self.state == "NORMAL":
            if self.is_number:
                num = f"{self.value / 10 ** self.multiplier:.{max(self.decimals, 0)}f}"
            else:
                num = "-OL" if self.value < 0 else "OL"
        else:  # INVALID / BLANK / OPEN_TC / DISCHARGE / INACTIVE: value is a sentinel
            num = {"INVALID": "----", "BLANK": ""}.get(self.state, self.state)
        s = f"{num} {pre}{sym}".strip()
        if self.attribute != "NONE":
            s += f" [{self.attribute}]"
        return s


def parse_reading(b: bytes, off: int) -> BinReading:
    rid, = struct.unpack_from("<H", b, off)
    val = fdouble(b, off + 2)
    unit, mult, dec, dig, st, attr = struct.unpack_from("<HhhhHH", b, off + 10)
    ts = fdouble(b, off + 22)
    return BinReading(lookup("readingid", rid), val, lookup("unit", unit), mult, dec, dig,
                      lookup("state", st), lookup("attribute", attr), ts)


@dataclass
class MeasHeader:
    prim: str
    sec: str
    autorange: str
    unit: str
    range_max: float
    multiplier: int
    bolt: str
    minmax_start: float
    mode: list[str]
    readings: list[BinReading] = field(default_factory=list)


def parse_meas_header(b: bytes, off: int) -> tuple[MeasHeader, int]:
    """34-byte measurement header (QDDB layout) followed by n 30-byte readings."""
    if len(b) < off + 34:
        raise ValueError(f"measurement header truncated: {len(b)} < {off + 34} bytes")
    n_declared, = struct.unpack_from("<H", b, off + 32)
    if len(b) < off + 34 + 30 * n_declared:
        raise ValueError(f"reply truncated: {n_declared} readings need {off + 34 + 30 * n_declared}"
                         f" bytes, got {len(b)}")
    prim, sec, ar, unit = struct.unpack_from("<HHHH", b, off)
    rng = fdouble(b, off + 8)
    mult, bolt = struct.unpack_from("<hH", b, off + 16)
    mmst = fdouble(b, off + 20)
    mode, _unk, n = struct.unpack_from("<HHH", b, off + 28)
    h = MeasHeader(lookup("primfunction", prim), lookup("secfunction", sec), lookup("autorange", ar),
                   lookup("unit", unit), rng, mult, lookup("bolt", bolt), mmst, modes(mode))
    p = off + 34
    for _ in range(n):
        h.readings.append(parse_reading(b, p))
        p += 30
    return h, p


@dataclass
class Session:
    kind: str
    index: int
    seq: int
    name: str
    header: MeasHeader
    start: float = 0.0
    end: float = 0.0
    interval: float = 0.0
    threshold: float = 0.0
    reading_index: int = 0
    num_samples: int = 0


def name_from(b: bytes, p: int) -> str:
    return b[p:].rstrip(b"\r").decode("latin-1")


def parse_qsmr(b: bytes, index: int) -> Session:
    seq, = struct.unpack_from("<I", b, 0)
    h, p = parse_meas_header(b, 4)
    return Session("measurement", index, seq, name_from(b, p), h)


def parse_qmmsi(b: bytes, index: int, kind: str = "minmax") -> Session:
    seq, = struct.unpack_from("<I", b, 0)
    start, end = fdouble(b, 4), fdouble(b, 12)
    h, p = parse_meas_header(b, 20)
    return Session(kind, index, seq, name_from(b, p), h, start=start, end=end)


def parse_qrsi(b: bytes, index: int) -> Session:
    seq, = struct.unpack_from("<I", b, 0)
    start, end, interval, thr = (fdouble(b, o) for o in (4, 12, 20, 28))
    ridx, nsamp = struct.unpack_from("<II", b, 36)
    h, p = parse_meas_header(b, 44)
    return Session("recording", index, seq, name_from(b, p), h, start=start, end=end,
                   interval=interval, threshold=thr, reading_index=ridx, num_samples=nsamp)


@dataclass
class TrendRecord:
    start: float
    end: float
    maximum: BinReading
    minimum: BinReading
    average: BinReading  # value already divided by the sample count
    samples: int
    primary: BinReading
    record_type: str
    stable: str
    transient: str


def parse_qsrr(b: bytes) -> TrendRecord:
    if len(b) != 146:
        raise ValueError(f"QSRR payload must be 146 bytes, got {len(b)}")
    start, end = fdouble(b, 0), fdouble(b, 8)
    mx, mn, avg = parse_reading(b, 16), parse_reading(b, 46), parse_reading(b, 76)
    n, = struct.unpack_from("<I", b, 106)
    if n and avg.is_number:
        avg.value = avg.value / n  # meter stores the SUM of n samples
    prim = parse_reading(b, 110)
    rt, stable, tr = struct.unpack_from("<HHH", b, 140)
    return TrendRecord(start, end, mx, mn, avg, n, prim, lookup("recordtype", rt),
                       lookup("isstableflag", stable), lookup("transientstate", tr))


class Meter:
    def __init__(self, adapter: Adapter):
        self.a = adapter
        self.retries = 0

    async def raw(self, cmd: str, unsafe: bool = False) -> tuple[str, bytes]:
        """Send a command, return (ack-name, payload-without-trailing-CR)."""
        try:
            data = cmd.encode("ascii")
        except UnicodeEncodeError:
            raise AdapterError(f"non-ASCII meter command {cmd!r}") from None
        reply = await self.a.passthrough(data, unsafe=unsafe)
        if not reply:
            return "NO_REPLY", b""
        ack_b, _, rest = reply.partition(b"\r")
        ack = ACK.get(ack_b, f"ACK_{ack_b.decode('latin-1')}")
        if rest.endswith(b"\r"):
            rest = rest[:-1]
        return ack, rest

    async def query(self, cmd: str, retries: int = 2) -> bytes:
        """Read-only query with retry on transient failures.

        The IR3000 keeps polling the meter for its live BLE readings; a pass-through
        command occasionally collides with that and the meter answers SYNTAX_ERROR
        or nothing. Queries have no side effects, so retrying is safe.
        """
        ack, payload = "NO_REPLY", b""
        for attempt in range(retries + 1):
            try:
                ack, payload = await self.raw(cmd)
            except AdapterError as e:
                # adapter FAILED/CANCELLED or a reply timeout: transient, safe to retry a query
                if attempt == retries or not (e.status in (4, 5) or "timeout" in str(e)):
                    raise
                self.retries += 1
                await asyncio.sleep(0.3)
                continue
            if ack == "OK":
                return payload
            if ack not in ("SYNTAX_ERROR", "NO_REPLY") or attempt == retries:
                break
            self.retries += 1
            await asyncio.sleep(0.2)
        raise MeterError(cmd, ack, payload)

    async def query_text(self, cmd: str) -> str:
        return (await self.query(cmd)).decode("latin-1")

    async def query_bin(self, cmd: str) -> bytes:
        p = await self.query(cmd)
        if not p.startswith(b"#0"):
            raise MeterError(cmd, "NOT_BINARY", p)
        return p[2:]

    async def identify(self) -> dict[str, str]:
        f = (await self.query_text("ID")).split(",")
        return {"model": f[0], "firmware": f[1] if len(f) > 1 else "", "serial": f[2] if len(f) > 2 else ""}

    async def load_maps(self, names=("primfunction", "secfunction", "unit", "readingid", "state",
                                     "attribute", "autorange", "bolt", "recordtype",
                                     "isstableflag", "transientstate")) -> None:
        for n in names:
            txt = await self.query_text(f"QEMAP {n}")
            tok = [t.strip() for t in txt.split(",")]
            MAPS[n.lower()] = {int(tok[i]): tok[i + 1] for i in range(1, len(tok) - 1, 2)}

    async def display(self) -> MeasHeader:
        b = await self.query_bin("QDDB")
        h, _ = parse_meas_header(b, 0)
        return h

    async def saved_counts(self) -> dict[str, int]:
        v = [int(x) for x in (await self.query_text("QSLS")).split(",")]
        return dict(zip(("recordings", "minmax", "peak", "measurements"), v))

    async def saved_measurement(self, i: int) -> Session:
        return parse_qsmr(await self.query_bin(f"QSMR {i}"), i)

    async def minmax_session(self, i: int) -> Session:
        return parse_qmmsi(await self.query_bin(f"QMMSI {i}"), i, "minmax")

    async def peak_session(self, i: int) -> Session:
        return parse_qmmsi(await self.query_bin(f"QPSI {i}"), i, "peak")

    async def recording(self, i: int) -> Session:
        return parse_qrsi(await self.query_bin(f"QRSI {i}"), i)

    async def trend_record(self, reading_index: int, sample: int) -> TrendRecord:
        return parse_qsrr(await self.query_bin(f"QSRR {reading_index},{sample}"))

    async def trend_records(self, reading_index: int, start: int, count: int) -> list[TrendRecord]:
        """Fetch several QSRR records with one adapter round-trip (as many as fit
        in the adapter's out buffer, at most 8 = the verified reply size)."""
        from .adapter import MAX_OUT
        if count <= 0:
            return []
        cmds: list[bytes] = []
        for k in range(start, start + count):
            c = f"QSRR {reading_index},{k}".encode()
            if len(cmds) >= 8 or len(b"\r".join(cmds + [c])) > MAX_OUT:
                break
            cmds.append(c)
        data = await self.a.passthrough_batch(cmds)
        recs = []
        frame = 2 + 2 + 146 + 1  # b"0\r" b"#0" payload b"\r"
        for i in range(len(cmds)):
            f = data[i * frame:(i + 1) * frame]
            if len(f) != frame or not f.startswith(b"0\r#0") or not f.endswith(b"\r"):
                # frames 0..i-1 were good, so the ack for command i starts exactly here
                ack_b = data[i * frame:].partition(b"\r")[0]
                raise MeterError(cmds[i].decode(), ACK.get(ack_b, "BAD_FRAME"), f[:8])
            recs.append(parse_qsrr(f[4:-1]))
        if len(data) != frame * len(cmds):
            raise MeterError(cmds[0].decode(), f"BATCH_LENGTH_{len(data)}", data[:8])
        return recs
