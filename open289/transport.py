"""BLE transport: backend selection (real adapter via bleak, or the virtual IR3000 + 289), discovery and connection.

OPEN289_SIM=1 (or --sim) selects the simulator in open289.sim. OPEN289_NO_BLE=1 makes the real backend refuse to
start, so a test can never reach real hardware.
"""
from __future__ import annotations

import asyncio
import os

from .errors import Open289Error

READING_SVC = 'b6981800-7562-11e2-b50d-00163e46f8fe'
READING_CHR = 'b698290f-7562-11e2-b50d-00163e46f8fe'
DIS_MODEL = '00002a24-0000-1000-8000-00805f9b34fb'
DIS_SERIAL = '00002a25-0000-1000-8000-00805f9b34fb'
DIS_RADIO_FW = '00002a26-0000-1000-8000-00805f9b34fb'
BATTERY = '00002a19-0000-1000-8000-00805f9b34fb'

_SIM = [None]          # set by the CLI's --sim; None = decide from the environment


def use_sim(on: bool = True) -> None:
    _SIM[0] = on


def sim_enabled() -> bool:
    if _SIM[0] is not None:
        return _SIM[0]
    return os.environ.get('OPEN289_SIM', '').strip() not in ('', '0', 'false', 'no')


def backend():
    """(ClientClass, ScannerClass, error base class) for the selected backend."""
    if sim_enabled():
        from . import sim
        from bleak.exc import BleakError
        return sim.SimBleakClient, sim.SimBleakScanner, BleakError
    if os.environ.get('OPEN289_NO_BLE', '').strip() not in ('', '0'):
        raise Open289Error('BLE_ERROR', 'real Bluetooth is disabled (OPEN289_NO_BLE is set)',
                           'unset OPEN289_NO_BLE, or use --sim / OPEN289_SIM=1 for the virtual meter')
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
    return BleakClient, BleakScanner, BleakError


def is_fluke(adv) -> bool:
    return READING_SVC in [u.lower() for u in (adv.service_uuids or [])]


async def scan(seconds: float = 10.0) -> list[dict]:
    _, Scanner, BleakError = backend()
    seen: dict[str, dict] = {}

    def cb(dev, adv):
        if is_fluke(adv):
            e = seen.setdefault(dev.address, {'address': dev.address,
                                             'name': adv.local_name or dev.name or ''})
            e['rssi'] = adv.rssi
            if adv.local_name:
                e['name'] = adv.local_name

    try:
        async with Scanner(cb):
            await asyncio.sleep(seconds)
    except BleakError as e:
        raise Open289Error('BLE_ERROR', f'scan failed: {e}', 'is Bluetooth switched on?') from None
    return sorted(seen.values(), key=lambda d: -(d.get('rssi') or -999))


async def find_adapter(address: str | None, timeout: float = 15.0):
    """BLEDevice (or the simulator's equivalent) of the adapter to use; NOT_FOUND when nothing advertises."""
    _, Scanner, BleakError = backend()
    try:
        if address:
            dev = await Scanner.find_device_by_address(address, timeout=timeout)
        else:
            dev = await Scanner.find_device_by_filter(lambda d, a: is_fluke(a), timeout=timeout)
    except BleakError as e:
        raise Open289Error('BLE_ERROR', f'scan failed: {e}', 'is Bluetooth switched on?') from None
    if dev is None:
        what = f'adapter {address}' if address else 'IR3000 FC adapter'
        raise Open289Error('NOT_FOUND', f'no {what} is advertising',
                           'press the IR3000 button (>1 s) until the LED flashes green; close Fluke Connect on any '
                           'phone (the adapter accepts one connection)')
    return dev


class Link:
    """Connected adapter + meter for one command: async with Link(address) as link: link.meter.query(...)."""

    def __init__(self, address: str | None = None, verbose: bool = False, connect_timeout: float = 20.0,
                 attempts: int = 3, on_disconnect=None):
        self.address = address
        self.verbose = verbose
        self.connect_timeout = connect_timeout
        self.attempts = attempts
        self.on_disconnect = on_disconnect
        self.client = None
        self.adapter = None
        self.meter = None
        self.lost = asyncio.Event()

    def _disconnected(self, _client):
        self.lost.set()
        if self.on_disconnect:
            self.on_disconnect()

    async def __aenter__(self):
        from .adapter import Adapter
        from .meter import Meter
        Client, _, BleakError = backend()
        dev = await find_adapter(self.address)
        last = None
        for attempt in range(self.attempts):
            try:
                self.client = Client(dev, timeout=self.connect_timeout, disconnected_callback=self._disconnected)
                await self.client.connect()
                break
            except (BleakError, OSError, asyncio.TimeoutError) as e:
                last = e
                self.client = None
                await asyncio.sleep(2.0)
        else:
            raise Open289Error('BLE_ERROR', f'could not connect: {last!r}',
                               'move closer, check that no phone is connected to the adapter, then retry')
        self.adapter = Adapter(self.client, verbose=self.verbose)
        try:
            await self.adapter.start()
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        self.meter = Meter(self.adapter)
        return self

    async def __aexit__(self, *exc):
        try:
            if self.adapter:
                await self.adapter.stop()
        finally:
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    async def read_text(self, uuid: str) -> str:
        return bytes(await self.client.read_gatt_char(uuid)).rstrip(b'\0 ').decode('ascii', 'replace')

    async def attached_model(self) -> str:
        """Meter model as the adapter reports it ('FLUKE 289'); 'FLUKE PC3000' means no powered meter."""
        return await self.read_text(DIS_MODEL)

    async def require_meter(self) -> str:
        model = await self.attached_model()
        if 'PC3000' in model.upper():
            raise Open289Error('DETACHED', 'the adapter is not attached to a powered meter',
                               'clip the IR3000 onto the meter\'s IR port and switch the meter on')
        return model
