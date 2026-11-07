"""Decoder for the Fluke Connect binary reading record (characteristic B698290F).

Format (from com/fluke/device/FlukeReading.java, see notes/02-live-readings.md):
8 bytes read as one little-endian uint64, fields LSB-first:
  counts:21 state:4 decimals:3 magnitude:3 sign:1 unit:8 function:8
  valueRange:7 rangeDecade:3 attribute:5 captureFlag:1
A notification is 8 bytes (single) or 16 bytes (primary + secondary display).
"""
from __future__ import annotations

from dataclasses import dataclass

STATE = {
    0: "NORMAL", 1: "BLANK", 2: "INACTIVE", 3: "INVALID", 4: "OL_RANGE", 5: "OL_A2D",
    6: "OPEN_TC", 7: "DISCHARGE", 8: "LEADS", 9: "GREATER_THAN", 10: "MISSING_PHASE",
    11: "ERROR", 12: "LESS_THAN", 13: "EMPTY",
}
MAGNITUDE = {  # code -> (exponent, prefix)
    0: (0, ""), 1: (9, "G"), 2: (6, "M"), 3: (3, "k"),
    4: (-3, "m"), 5: (-6, "µ"), 6: (-9, "n"), 7: (-12, "p"),
}
RANGE_DECADE = {0: 0, 1: 1, 2: 2, 3: 3, 4: 0, 5: -3, 6: -2, 7: -1}
UNIT = {
    0: "", 1: "V AC", 2: "V DC", 3: "A AC", 4: "A DC", 5: "Hz", 6: "%RH", 7: "°C", 8: "°F",
    9: "°R", 10: "K", 11: "Ω", 12: "S", 13: "%", 14: "s", 15: "F", 16: "dB", 17: "dBm",
    18: "W", 19: "J", 20: "H", 21: "PSI", 22: "mHg", 23: "inHg", 24: "ftH2O", 25: "mH2O",
    26: "inH2O", 27: "inH2O(60F)", 28: "bar", 29: "Pa", 30: "g/cm²", 31: "dBV", 32: "CF",
    33: "V AC+DC", 34: "A AC+DC", 35: "%", 36: "V/Hz", 37: "g", 38: "m/s²", 39: "in/s",
    40: "mm/s", 41: "mils", 42: "µm", 43: "?", 44: "TΩ", 45: "VA DC", 47: "VA AC",
}
FUNCTION = {
    0: "NONE", 1: "mVAC", 2: "VAC", 3: "VAC+DC", 4: "mVAC avg", 5: "VAC avg",
    6: "VAC avg+DC", 7: "LoZ", 8: "mVAC lopass", 9: "VAC lopass", 10: "µVDC", 11: "mVDC",
    12: "VDC", 13: "mAAC", 14: "AAC", 15: "AAC+DC", 16: "mAAC avg", 17: "AAC avg",
    18: "AAC avg+DC", 19: "µADC", 20: "mADC", 21: "ADC", 22: "Hz mVAC", 23: "Hz VAC",
    24: "Hz mVAC lopass", 25: "Hz VAC lopass", 26: "Hz mVDC", 27: "Hz VDC", 28: "Hz µAAC",
    29: "Hz mAAC", 30: "Hz AAC", 31: "Hz µADC", 32: "Hz mADC", 33: "Hz ADC", 34: "Temp",
    35: "°F", 36: "°C", 37: "°R", 38: "K", 39: "Continuity", 40: "Ω", 41: "Conductance",
    42: "Low Ω", 43: "Phase", 44: "AAC inrush", 45: "Capacitance", 46: "Diode",
    47: "VAC/Hz", 48: "mVAC+DC", 49: "mAAC+DC", 50: "µAAC", 51: "µAAC+DC",
    52: "Test VDC", 53: "Fault VAC", 54: "Touch VAC limit", 55: "Insulation",
    86: "Pressure", 87: "Field sense", 88: "Field sense P2P", 89: "VADC", 95: "Self check",
}
ATTRIB = {
    0: "", 1: "open circuit", 2: "short circuit", 3: "glitch", 4: "good diode",
    5: "negative edge", 6: "positive edge", 7: "high current", 8: "hazardous voltage",
    9: "low ohms", 10: "open glitch", 11: "short glitch", 12: "peak", 13: "sourced",
    14: "simulated", 15: "noise", 16: "breakdown",
}


@dataclass
class Reading:
    raw: bytes
    counts: int
    state: int
    decimals: int
    magnitude: int
    sign: bool
    unit: int
    function: int
    value_range: int
    range_decade: int
    attribute: int
    capture: bool

    @property
    def mag_exp(self) -> int:
        return MAGNITUDE[self.magnitude][0]

    @property
    def prefix(self) -> str:
        return MAGNITUDE[self.magnitude][1]

    @property
    def display_number(self) -> float:
        """Number as shown on the meter (before the SI prefix)."""
        v = self.counts * 10.0 ** (-self.decimals)
        return -v if self.sign else v

    @property
    def value(self) -> float | None:
        """Value in SI base units, None when not a normal numeric reading."""
        if self.state not in (0, 9, 12):
            return None
        v = self.counts * 10.0 ** (self.mag_exp - self.decimals)
        return -v if self.sign else v

    @property
    def state_name(self) -> str:
        return STATE.get(self.state, f"state{self.state}")

    @property
    def unit_text(self) -> str:
        u = UNIT.get(self.unit, f"unit{self.unit}")
        return f"{self.prefix}{u}"

    @property
    def range_value(self) -> float:
        return self.value_range * 10.0 ** (RANGE_DECADE[self.range_decade] + self.mag_exp)

    def text(self) -> str:
        if self.state in (4, 5):
            num = "-OL" if self.sign else "OL"
        elif self.state in (0, 9, 12):
            num = f"{self.display_number:.{self.decimals}f}"
            num = {9: "> ", 12: "< "}.get(self.state, "") + num
        elif self.state == 13:
            return ""
        else:
            num = {1: "----", 3: "----"}.get(self.state, self.state_name)
        extra = []
        if self.attribute:
            extra.append(ATTRIB.get(self.attribute, f"attr{self.attribute}"))
        if self.capture:
            extra.append("CAPTURE")
        s = f"{num} {self.unit_text}".strip()
        return s + (f"  [{', '.join(extra)}]" if extra else "")

    def describe(self) -> str:
        return (
            f"counts={self.counts} state={self.state_name} dec={self.decimals} "
            f"mag={self.prefix or '1'} sign={int(self.sign)} unit={self.unit}({UNIT.get(self.unit, '?')}) "
            f"func={self.function}({FUNCTION.get(self.function, '?')}) "
            f"range={self.value_range}e{RANGE_DECADE[self.range_decade]} attr={self.attribute} cap={int(self.capture)}"
        )


def decode_record(rec: bytes) -> Reading:
    if len(rec) < 8:
        raise ValueError(f"reading record needs 8 bytes, got {len(rec)}")
    w = int.from_bytes(rec[:8], "little")

    def f(off: int, width: int) -> int:
        return (w >> off) & ((1 << width) - 1)

    return Reading(
        raw=bytes(rec[:8]),
        counts=f(0, 21), state=f(21, 4), decimals=f(25, 3), magnitude=f(28, 3),
        sign=bool(f(31, 1)), unit=f(32, 8), function=f(40, 8), value_range=f(48, 7),
        range_decade=f(55, 3), attribute=f(58, 5), capture=bool(f(63, 1)),
    )


def decode_notification(data: bytes) -> tuple[Reading, Reading | None]:
    """Primary + optional secondary (None if absent or all-zero)."""
    primary = decode_record(data[:8])
    secondary = None
    if len(data) >= 16 and any(data[8:16]):
        secondary = decode_record(data[8:16])
    return primary, secondary


if __name__ == "__main__":  # self-test with the worked examples from the notes + live capture
    for hx, want in [
        ("39 30 00 06 02 0C 85 00", "12.345 V DC"),
        ("98 B7 00 38 0B 28 85 00", "4.7000 kΩ"),
        ("00 00 80 28 0B 28 05 00", "OL MΩ"),
        ("D2 04 00 88 02 0C 05 00", "-0.1234 V DC"),
        ("d9 c5 00 08 02 0c 00 00", "5.0649 V DC"),
    ]:
        r = decode_record(bytes.fromhex(hx))
        ok = "OK " if r.text() == want else "BAD"
        print(f"{ok} {hx} -> {r.text()!r} (want {want!r})  {r.describe()}")
