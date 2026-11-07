"""Connect to a BLE device and dump its full GATT table (read-only).

Reads every readable characteristic except ones that could have side effects
(firmware-update service, CNX force-disconnect). Never writes, never subscribes.

Usage: python tools/gatt_dump.py <address> [--no-read]   (real hardware only)
"""
import asyncio
import sys

from bleak import BleakClient

SKIP_SERVICES = {
    "b6981804-7562-11e2-b50d-00163e46f8fe",  # IR3000 firmware image service
}
SKIP_CHARS = {
    "b6982904-7562-11e2-b50d-00163e46f8fe",  # CNX force disconnect
    "b6982905-7562-11e2-b50d-00163e46f8fe",  # CNX locator
    "b6982911-7562-11e2-b50d-00163e46f8fe",  # CNX FW control point (0x80 = erase MSP flash!)
    "b6982912-7562-11e2-b50d-00163e46f8fe",  # CNX FW image data
    "b6982913-7562-11e2-b50d-00163e46f8fe",  # image identity (FW update)
    "b6982914-7562-11e2-b50d-00163e46f8fe",  # image block (FW update)
}


def fmt(data: bytes) -> str:
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{data.hex(' ')}  |{txt}|"


async def main(addr: str, do_read: bool) -> None:
    async with BleakClient(addr, timeout=20.0) as client:
        print(f"connected={client.is_connected} mtu={client.mtu_size}")
        for svc in client.services:
            print(f"[service] {svc.uuid}  handle=0x{svc.handle:04x}  {svc.description}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                print(f"   [char] {ch.uuid}  handle=0x{ch.handle:04x}  props={props}  {ch.description}")
                for d in ch.descriptors:
                    print(f"      [desc] {d.uuid}  handle=0x{d.handle:04x}")
                if (
                    do_read
                    and "read" in ch.properties
                    and svc.uuid.lower() not in SKIP_SERVICES
                    and ch.uuid.lower() not in SKIP_CHARS
                ):
                    try:
                        val = await client.read_gatt_char(ch)
                        print(f"      value ({len(val)}): {fmt(bytes(val))}")
                    except Exception as e:  # noqa: BLE001
                        print(f"      read failed: {e!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], "--no-read" not in sys.argv))
