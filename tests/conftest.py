import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test runs against the virtual IR3000 + Fluke 289 with its own state folder.

    A test must never reach a real Bluetooth adapter: OPEN289_SIM selects the simulator and OPEN289_NO_BLE makes
    the transport refuse the real backend.
    """
    monkeypatch.setenv('OPEN289_HOME', str(tmp_path))
    monkeypatch.setenv('OPEN289_SIM', '1')
    monkeypatch.setenv('OPEN289_NO_BLE', '1')
    monkeypatch.setenv('OPEN289_SIM_RATE', '40')      # live notifications per second in the simulator
    monkeypatch.setenv('OPEN289_SIM_DELAY', '0.01')   # adapter/meter latency in the simulator (hardware ~0.6 s)
    yield
