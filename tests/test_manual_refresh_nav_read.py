"""A manual refresh re-reads the companion detail screens and GPS at once.

Plain polls read the app overview only; detail screens and the parking position
are re-read at most every ``_NAV_READ_INTERVAL_S``. The Refresh button and the
``refresh_vehicle`` service must not return a position up to that old.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.vag_connect.companion.channel import CompanionChannel
from custom_components.vag_connect.companion.presets import PRESETS

from .test_v2260_grounded_vw_cupra import (
    _VW_OVERVIEW,
    _cd,
    _clock,
    _dump,
    _NavTransport,
)


class TestChannelRequestNavRead:
    @pytest.mark.asyncio
    async def test_request_forces_nav_read_inside_cadence_window(self) -> None:
        now, _ = _clock()
        t = _NavTransport(_VW_OVERVIEW, _dump(_cd("Target charge level: 80 %")))
        ch = CompanionChannel(t, PRESETS["volkswagen"], time_fn=now, read_charge_detail=True)
        assert (await ch.read())["target_soc"] == 80
        t._detail = _dump(_cd("Target charge level: 60 %"))
        # Within the window a plain read keeps the cached detail value.
        assert (await ch.read())["target_soc"] == 80
        ch.request_nav_read()
        assert (await ch.read())["target_soc"] == 60
        assert len(t.taps) == 2

    @pytest.mark.asyncio
    async def test_cadence_restarts_from_the_forced_read(self) -> None:
        now, _ = _clock()
        t = _NavTransport(_VW_OVERVIEW, _dump(_cd("Target charge level: 80 %")))
        ch = CompanionChannel(t, PRESETS["volkswagen"], time_fn=now, read_charge_detail=True)
        await ch.read()
        ch.request_nav_read()
        await ch.read()
        await ch.read()
        assert len(t.taps) == 2

    def test_request_keeps_cached_values_and_backoff(self) -> None:
        now, _ = _clock()
        ch = CompanionChannel(_NavTransport(_VW_OVERVIEW, _VW_OVERVIEW), PRESETS["volkswagen"], time_fn=now)
        ch._nav_cache["latitude"] = 50.0
        ch._cooldown_until = 1e12
        ch.request_nav_read()
        assert ch._nav_cache == {"latitude": 50.0}
        assert ch._cooldown_until == 1e12


pytestmark_ha = pytest.mark.ha_required


def _coord(client: object):
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c._cariad_client = client
    c.async_request_refresh = AsyncMock()
    return c


@pytestmark_ha
def test_manual_refresh_requests_nav_read_before_polling():
    calls: list[str] = []
    client = MagicMock()
    client.request_nav_read = MagicMock(side_effect=lambda: calls.append("nav"))
    c = _coord(client)
    c.async_request_refresh.side_effect = lambda: calls.append("refresh")
    asyncio.run(c.async_manual_refresh())
    assert calls == ["nav", "refresh"]


@pytestmark_ha
def test_manual_refresh_without_companion_client_just_polls():
    c = _coord(object())  # a cloud client has no request_nav_read
    asyncio.run(c.async_manual_refresh())
    c.async_request_refresh.assert_awaited_once()
