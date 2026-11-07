"""Does CLEAR (0x81) empty the IR3000's OUT buffer? (read-only probe, 6 bytes max out)

Sends: CLEAR, APPEND 'QM', CLEAR, APPEND 'ID', SEND.
  - reply is only the ID answer        -> CLEAR empties the OUT buffer
  - reply contains a syntax error / QM -> CLEAR does not; keep the pending_out guard

Usage: python tools/probe_clear.py <address>   (real hardware only)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bleak import BleakClient, BleakScanner  # noqa: E402

from open289.adapter import Adapter  # noqa: E402


async def main(addr: str) -> None:
    dev = await BleakScanner.find_device_by_address(addr, timeout=10.0) or addr
    async with BleakClient(dev, timeout=20.0) as c:
        a = Adapter(c, verbose=True)
        await a.start()
        try:
            for op in (b"\x81", b"\x8dQM", b"\x81", b"\x8dID"):
                print(op, "->", await a.command(op))
            print("SEND ->", await a.command(b"\x8e", timeout=10))
            size = await a.buffer_size()
            reply = await a.read_buffer(0, size) if size else b""
            print(f"reply ({size} bytes): {reply!r}")
            if reply.startswith(b"0\rFLUKE") and reply.count(b"\r") == 2:
                print("RESULT: CLEAR empties the OUT buffer")
            else:
                print("RESULT: CLEAR does NOT reliably empty the OUT buffer (or unexpected reply)")
        finally:
            await a.stop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
