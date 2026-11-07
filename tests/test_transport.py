"""Offline transport tests against a simulated IR3000 (no Bluetooth).

Run: .venv\\Scripts\\python.exe -I tests\\test_transport.py
"""
import asyncio
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open289.adapter import BUF, CNX_ID, CP, MAX_OUT, Adapter, AdapterError  # noqa: E402
from open289.meter import Meter, MeterError  # noqa: E402

QSRR_PAYLOAD = bytes(146)  # all-zero record decodes fine (states INACTIVE, n=0)


class FakeIR3000:
    """Minimal control-point/buffer simulation of the adapter + a meter."""

    def __init__(self, meter_ack=b"0", drop_append_reply_at=None, clear_empties_out=True):
        self.cbs = {}
        self.out = bytearray()
        self.inbuf = b""
        self.meter_ack = meter_ack
        self.drop_at = drop_append_reply_at
        self.appends = 0
        self.clear_empties_out = clear_empties_out
        self.sent = []

    async def start_notify(self, uuid, cb):
        self.cbs[uuid] = cb

    async def stop_notify(self, uuid):
        self.cbs.pop(uuid, None)

    def _cp(self, *b):
        self.cbs[CP](None, bytearray(b))

    def _reply_for(self, cmd: bytes) -> bytes:
        if self.meter_ack != b"0":
            return self.meter_ack + b"\r"
        if cmd.startswith(b"QSRR"):
            return b"0\r#0" + QSRR_PAYLOAD + b"\r"
        return b"0\rOK\r"

    async def write_gatt_char(self, uuid, data, response=True):
        data = bytes(data)
        if uuid == CNX_ID:
            return
        assert uuid == CP, uuid
        op = data[0]
        if op == 0x81:
            if self.clear_empties_out:
                self.out.clear()
            self.inbuf = b""
            self._cp(3, 0x81)
        elif op == 0x8D:
            self.out += data[1:]
            self.appends += 1
            if self.appends != self.drop_at:
                self._cp(3, 0x8D)
        elif op == 0x8E:
            self.sent.append(bytes(self.out))
            self.inbuf = b"".join(self._reply_for(c) for c in bytes(self.out).split(b"\r"))
            self.out.clear()
            self._cp(1, 0x8E)
            self._cp(3, 0x8E)
        elif op == 0x80:
            self._cp(3, 0x80, *struct.pack("<I", len(self.inbuf)))
        elif op == 0x82:
            off, ln = struct.unpack_from("<II", data, 1)
            self._cp(1, 0x82)
            chunk = self.inbuf[off:off + ln]
            for i in range(0, len(chunk), 20):
                self.cbs[BUF](None, bytearray(chunk[i:i + 20]))
            self._cp(3, 0x82)


def check(cond, msg):
    assert cond, msg



async def _scenario():
    # 1. normal batch of 8 records
    f = FakeIR3000()
    a = Adapter(f); await a.start(); m = Meter(a)
    recs = await m.trend_records(772, 0, 72)
    check(len(recs) == 8 and len(f.sent[-1]) <= MAX_OUT, f"batch of 8, {len(f.sent[-1])} bytes out")
    check(await m.trend_records(772, 5, 0) == [], "count 0 -> [] without a SEND")
    sends_before = len(f.sent)

    # 2. meter on a menu page: every query answers '2' -> named error, not BAD_FRAME
    f2 = FakeIR3000(meter_ack=b"2")
    a2 = Adapter(f2); await a2.start(); m2 = Meter(a2)
    try:
        await m2.trend_records(772, 0, 8)
        check(False, "EXECUTION_ERROR batch raises")
    except MeterError as e:
        check(e.ack == "EXECUTION_ERROR", f"batch error named {e.ack}")

    # 3. lost APPEND reply: pending bytes are tracked and an oversized retry is refused
    f3 = FakeIR3000(drop_append_reply_at=4, clear_empties_out=False)
    a3 = Adapter(f3); await a3.start(); m3 = Meter(a3)
    try:
        await a3.passthrough_batch([f"QSRR 772,{k}".encode() for k in range(8)], timeout=0.3)
        check(False, "lost APPEND reply raises")
    except AdapterError as e:
        check("timeout" in str(e), "lost APPEND reply -> timeout")
    check(a3.pending_out == 68, f"pending_out after 4 appends = {a3.pending_out}")
    try:
        await a3.passthrough_batch([f"QSRR 772,{k}".encode() for k in range(8)])
        check(False, "oversized retry refused")
    except AdapterError as e:
        check("unsent" in str(e), "oversized retry refused before any write")
    check(all(len(s) <= MAX_OUT for s in f3.sent), "nothing over MAX_OUT ever reached SEND")
    # A single record still fits (68 + 10 <= 87). With a dirty buffer the meter answers the
    # leftover commands too; the strict length check must reject that, not accept garbage.
    try:
        await m3.trend_record(772, 0)
        check(False, "dirty-buffer reply rejected")
    except ValueError:
        check(a3.pending_out == 0, "dirty-buffer reply rejected; SEND flushed the leftovers")
    r = await m3.trend_record(772, 0)
    check(r is not None and f3.sent[-1] == b"QSRR 772,0", "next attempt is clean")

    try:
        await a.passthrough_batch([])
        check(False, "empty batch refused")
    except AdapterError:
        check(len(f.sent) == sends_before, "empty batch refused without a SEND")



def test_transport():
    asyncio.run(_scenario())
