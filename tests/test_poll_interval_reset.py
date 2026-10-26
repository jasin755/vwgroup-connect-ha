"""A manual/command refresh restarts the background poll interval.

Without this, a refresh at minute 14 of a 15-minute interval was followed by a
second full poll one minute later. After any ``_async_update_data`` fetch the
next background poll is due a full interval from that refresh.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.ha_required


def _coord():
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c._started = True
    return c


def test_sleep_returns_after_interval_without_refresh():
    async def run() -> float:
        c = _coord()
        loop = asyncio.get_running_loop()
        start = loop.time()
        await c._sleep_until_next_poll(0.05)
        return loop.time() - start

    assert asyncio.run(run()) >= 0.05


def test_refresh_restarts_the_full_interval():
    async def run() -> float:
        c = _coord()
        loop = asyncio.get_running_loop()
        start = loop.time()
        sleeper = asyncio.create_task(c._sleep_until_next_poll(0.2))
        await asyncio.sleep(0.15)
        c._restart_poll_interval()
        await sleeper
        return loop.time() - start

    # 0.15 s elapsed before the refresh, then a fresh 0.2 s wait.
    assert asyncio.run(run()) >= 0.34


def test_stop_during_sleep_ends_the_wait():
    async def run() -> float:
        c = _coord()
        loop = asyncio.get_running_loop()
        start = loop.time()
        sleeper = asyncio.create_task(c._sleep_until_next_poll(5))
        await asyncio.sleep(0.01)
        c._started = False
        c._restart_poll_interval()
        await sleeper
        return loop.time() - start

    assert asyncio.run(run()) < 1
