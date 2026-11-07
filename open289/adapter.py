"""IR3000 FC AdapterLogging service client (service B6981805).

Control point B6982918: write [opcode][params]; replies arrive as notifications
[status][echoed opcode][payload...]. Bulk data arrives as notifications on the
buffer characteristic B6982919. (notes/03-adapter-command-protocol.md)

Safety: only an allowlist of opcodes can be sent, and only to the adapter
control point. Same byte values mean destructive things on other control
points (e.g. 0x80 on B6982911 erases MSP flash), so UUIDs are hard-coded here.
"""
from __future__ import annotations

import asyncio
import re
import struct

from bleak import BleakClient

from .errors import Open289Error

ADAPTER_SVC = "b6981805-7562-11e2-b50d-00163e46f8fe"
CP = "b6982918-7562-11e2-b50d-00163e46f8fe"
BUF = "b6982919-7562-11e2-b50d-00163e46f8fe"
CNX_ID = "b6982902-7562-11e2-b50d-00163e46f8fe"  # 1-byte id; writing it arms the adapter CP

GET_BUFFER_SIZE = 0x80
CLEAR_BUFFER = 0x81
GET_BUFFER_DATA = 0x82
CANCEL = 0x83
APPEND_OUT = 0x8D
SEND_OUT = 0x8E
GET_VERSION = 0x8F
GET_MODEL = 0x95

ALLOWED = {GET_BUFFER_SIZE, CLEAR_BUFFER, GET_BUFFER_DATA, CANCEL, APPEND_OUT, SEND_OUT,
           GET_VERSION, GET_MODEL}
# 0x84-0x8C (189/289 download macros) are allowed only via explicit opt-in.
DOWNLOAD_MACROS = set(range(0x84, 0x8D))
FORBIDDEN = set(range(0x90, 0x95))  # 1550/1555 high-voltage insulation test control

STATUS = {0: "IDLE", 1: "IN_PROCESS", 2: "INCORRECT_MODEL", 3: "COMPLETE", 4: "CANCELLED", 5: "FAILED"}

# Meter commands we let through the pass-through: identification and queries only.
# Fluke 28x convention: every query starts with Q. Anything else (MP, PRESS,
# CSD = clear saved data, RI = reset instrument, OFF, ...) needs unsafe=True.
SAFE_METER_CMD = re.compile(rb"^(ID|IM|Q[A-Z]{1,10})( [0-9A-Za-z_,]*)?$", re.I)


# Outgoing (to-meter) buffer limit. 87 bytes is the largest size verified to work;
# 181+ bytes produced no reply and 373+ bytes hung the adapter's MSP until it was
# power-cycled (2026-11-06). Never exceed the verified size.
MAX_OUT = 87
# Largest reply verified to come back intact through GET_BUFFER_DATA.
MAX_REPLY_VERIFIED = 1208


class AdapterError(Open289Error):
    """status: adapter STATUS_* code when the adapter answered (4/5 are transient), None for timeouts and
    local refusals. code: stable error code (TIMEOUT, PROTOCOL_ERROR, UNSAFE_OPERATION)."""

    def __init__(self, msg: str, status: int | None = None, code: str | None = None, hint: str | None = None):
        if code is None:
            code = 'TIMEOUT' if 'timeout' in msg else 'PROTOCOL_ERROR'
        if hint is None and code == 'TIMEOUT':
            hint = 'the adapter did not answer: move closer; if it persists, power-cycle the IR3000 (button)'
        super().__init__(code, msg, hint)
        self.status = status


class Adapter:
    def __init__(self, client: BleakClient, verbose: bool = False, allow_macros: bool = False):
        self.c = client
        self.verbose = verbose
        self.allow_macros = allow_macros
        self._cp_q: asyncio.Queue[bytes] = asyncio.Queue()
        self.buf = bytearray()
        self.buf_chunks: list[bytes] = []
        self.write_response = True
        self._desync = False  # a reply timed out; late notifications may still arrive
        # Bytes appended since the last successful SEND. Whether CLEAR (0x81) empties the
        # adapter's OUT buffer is not verified, so after a failure part-way through the
        # APPENDs we conservatively assume those bytes are still there.
        self.pending_out = 0

    async def start(self, cnx_id: int = 1) -> None:
        # Observed on IR3000 FW 01.00.01 / MSP 01.00.15: the control point stores
        # writes but never replies until the CNX ID has been written (the official
        # app writes 0x01 on every connect).
        await self.c.write_gatt_char(CNX_ID, bytes([cnx_id]), response=True)
        await self.c.start_notify(CP, self._on_cp)
        await self.c.start_notify(BUF, self._on_buf)

    async def stop(self) -> None:
        for u in (CP, BUF):
            try:
                await self.c.stop_notify(u)
            except Exception:  # noqa: BLE001
                pass

    def _on_cp(self, _h, data: bytearray) -> None:
        if self.verbose:
            print(f"    <CP  {bytes(data).hex(' ')}")
        self._cp_q.put_nowait(bytes(data))

    def _on_buf(self, _h, data: bytearray) -> None:
        if self.verbose:
            d = bytes(data)
            print(f"    <BUF {d.hex(' ')}  |{''.join(chr(b) if 32 <= b < 127 else '.' for b in d)}|")
        self.buf_chunks.append(bytes(data))
        self.buf.extend(data)

    async def command(self, payload: bytes, timeout: float = 5.0) -> tuple[int, bytes]:
        """Send one control-point command; wait for the reply echoing its opcode."""
        op = payload[0]
        if op in FORBIDDEN:
            raise AdapterError(f"opcode 0x{op:02X} is forbidden", code='UNSAFE_OPERATION')
        if op not in ALLOWED and not (self.allow_macros and op in DOWNLOAD_MACROS):
            raise AdapterError(f"opcode 0x{op:02X} not in allowlist", code='UNSAFE_OPERATION')
        if self._desync:
            # Give a late reply to the timed-out command time to land, then drop it,
            # so it cannot be mistaken for the reply to this command.
            await asyncio.sleep(0.5)
            self._desync = False
        while not self._cp_q.empty():
            stale = self._cp_q.get_nowait()
            if self.verbose:
                print(f"    (dropped stale CP {stale.hex(' ')})")
        if self.verbose:
            print(f"    >CP  {payload.hex(' ')}")
        await self.c.write_gatt_char(CP, payload, response=self.write_response)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            left = deadline - loop.time()
            if left <= 0:
                self._desync = True
                raise AdapterError(f"timeout waiting for reply to 0x{op:02X}")
            try:
                msg = await asyncio.wait_for(self._cp_q.get(), left)
            except (asyncio.TimeoutError, TimeoutError):
                self._desync = True
                raise AdapterError(f"timeout waiting for reply to 0x{op:02X}") from None
            if len(msg) >= 2 and msg[1] == op:
                status = msg[0]
                if status == 1:  # in process: keep waiting
                    continue
                return status, msg[2:]
            if self.verbose:
                print(f"    (ignoring CP reply for other opcode: {msg.hex(' ')})")

    async def version(self) -> str:
        """Adapter MSP firmware version, e.g. '01.00.15' (reply is 'ver,' CSV)."""
        st, pl = await self.command(bytes([GET_VERSION]))
        if st != 3:
            raise AdapterError(f"GET_ADAPTER_VERSION -> {STATUS.get(st, st)}")
        return pl.decode("ascii", "replace").split(",")[0]

    async def model(self) -> str:
        """Model name per adapter (empty with a 289 attached; used for 1550/1555)."""
        st, pl = await self.command(bytes([GET_MODEL]))
        if st != 3:
            raise AdapterError(f"GET_MODEL_NAME -> {STATUS.get(st, st)}")
        return pl.decode("ascii", "replace").rstrip("\0")

    async def buffer_size(self) -> int:
        st, pl = await self.command(bytes([GET_BUFFER_SIZE]))
        if st != 3 or len(pl) < 4:
            raise AdapterError(f"GET_BUFFER_SIZE -> status {st} payload {pl.hex(' ')}", st)
        return struct.unpack_from("<I", pl)[0]

    async def read_buffer(self, offset: int, length: int, timeout: float = 10.0) -> bytes:
        """GET_BUFFER_DATA: data arrives as notifications on BUF, then 03 82 on the CP."""
        self.buf.clear()
        self.buf_chunks.clear()
        st, pl = await self.command(bytes([GET_BUFFER_DATA]) + struct.pack("<II", offset, length), timeout)
        if st != 3:
            raise AdapterError(f"GET_BUFFER_DATA -> status {STATUS.get(st, st)} {pl.hex(' ')}", st)
        for _ in range(50):  # normally already complete; allow for late callbacks
            if len(self.buf) >= length:
                break
            await asyncio.sleep(0.02)
        return bytes(self.buf)

    async def passthrough(self, meter_cmd: bytes, unsafe: bool = False, timeout: float = 10.0,
                          chunk: int = 400) -> bytes:
        """Send one meter command through the adapter and return the meter's raw reply.

        Observed (IR3000 MSP 01.00.15 + Fluke 289 V1.71): the adapter appends the CR
        itself; SEND answers 01 8E (in process) and 03 8E only once the meter's reply
        is fully captured, so a single GET_BUFFER_SIZE afterwards is enough.
        """
        meter_cmd = meter_cmd.rstrip(b"\r\n")
        if not unsafe and not SAFE_METER_CMD.match(meter_cmd):
            raise AdapterError(f"meter command {meter_cmd!r} is not a read-only query", code='UNSAFE_OPERATION',
                               hint='only ID, IM and Q* queries pass by default; add --unsafe for other commands')
        return await self._send_raw(meter_cmd, timeout, chunk)

    async def passthrough_batch(self, meter_cmds: list[bytes], timeout: float = 15.0,
                                chunk: int = 400) -> bytes:
        """Send several read-only queries in one SEND (CR-separated); returns the
        concatenated replies. Verified: 8 x QSRR (87 bytes out, 1208 bytes back)."""
        cmds = [c.rstrip(b"\r\n") for c in meter_cmds]
        if not cmds:
            raise AdapterError("empty batch", code='INVALID_ARG')
        for c in cmds:
            if not SAFE_METER_CMD.match(c):
                raise AdapterError(f"meter command {c!r} is not a read-only query", code='UNSAFE_OPERATION')
        return await self._send_raw(b"\r".join(cmds), timeout, chunk)

    async def _send_raw(self, meter_cmd: bytes, timeout: float, chunk: int) -> bytes:
        if len(meter_cmd) > MAX_OUT:
            raise AdapterError(f"{len(meter_cmd)}-byte command exceeds the adapter's verified "
                               f"{MAX_OUT}-byte out buffer (larger sends can hang the adapter)", code='UNSAFE_OPERATION')
        if self.pending_out + len(meter_cmd) > MAX_OUT:
            raise AdapterError(f"adapter may still hold {self.pending_out} unsent bytes from a failed "
                               f"send; sending {len(meter_cmd)} more could exceed its {MAX_OUT}-byte "
                               f"buffer (send a shorter command, or power-cycle the IR3000)", code='UNSAFE_OPERATION')
        st, pl = await self.command(bytes([CLEAR_BUFFER]))
        if st != 3:
            raise AdapterError(f"CLEAR -> {STATUS.get(st, st)} {pl.hex(' ')}", st)
        for i in range(0, len(meter_cmd), 17):  # CP rejects writes > 18 bytes (opcode + 17)
            part = meter_cmd[i:i + 17]
            self.pending_out += len(part)  # counted before the write: a lost reply may still have landed
            st, pl = await self.command(bytes([APPEND_OUT]) + part)
            if st != 3:
                raise AdapterError(f"APPEND -> {STATUS.get(st, st)} {pl.hex(' ')}", st)
        st, pl = await self.command(bytes([SEND_OUT]), timeout=timeout)
        if st != 3:
            raise AdapterError(f"SEND -> {STATUS.get(st, st)} {pl.hex(' ')}", st)
        self.pending_out = 0  # SEND transmitted (and consumed) the out buffer
        size = await self.buffer_size()
        out = bytearray()
        while len(out) < size:
            n = min(chunk, size - len(out))
            part = await self.read_buffer(len(out), n, timeout=timeout)
            if len(part) != n:
                raise AdapterError(f"buffer read at {len(out)} returned {len(part)} of {n} bytes")
            out += part
        return bytes(out)
