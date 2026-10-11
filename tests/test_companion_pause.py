"""Intentional phone pause must not create repairs or keep navigating."""
from unittest.mock import AsyncMock

import pytest

from custom_components.vag_connect.companion.channel import CompanionChannel
from custom_components.vag_connect.companion.client import CompanionClient
from custom_components.vag_connect.cariad.models import VehicleData
from custom_components.vag_connect.companion.presets import PRESETS
from custom_components.vag_connect.companion.transport import CompanionAutomationPaused
from custom_components.vag_connect.companion.vw_driver import VolkswagenAppDriver


async def test_pause_preserves_cached_data_without_failure_backoff() -> None:
    transport = AsyncMock()
    transport.connected = True
    transport.foreground_app.side_effect = CompanionAutomationPaused()
    channel = CompanionChannel(transport, PRESETS["volkswagen"], time_fn=lambda: 500)
    channel._last_nav_at = 123
    channel._nav_cache = {"target_soc": 80}
    assert await channel.read() is None
    assert channel._consecutive_failures == 0
    assert not channel._in_cooldown()
    assert channel._last_nav_at is None  # details/GPS refresh after resuming
    assert channel._nav_cache == {"target_soc": 80}
    transport.dump_ui.assert_not_awaited()


async def test_pause_during_extended_read_is_also_quiet() -> None:
    channel = CompanionChannel(AsyncMock(), PRESETS["volkswagen"], time_fn=lambda: 500)
    channel._read_once = AsyncMock(side_effect=CompanionAutomationPaused())
    assert await channel.read() is None
    assert not channel._in_cooldown()


async def test_driver_does_not_retry_pause_as_screen_loading() -> None:
    transport = AsyncMock()
    transport.dump_ui.side_effect = CompanionAutomationPaused()
    with pytest.raises(CompanionAutomationPaused):
        await VolkswagenAppDriver(transport)._wait_for_nodes(bool, reason="test")
    transport.dump_ui.assert_awaited_once()
    transport.tap.assert_not_awaited()


async def test_extended_routes_stop_on_pause() -> None:
    driver = VolkswagenAppDriver(AsyncMock())
    driver._read_climate = AsyncMock(side_effect=CompanionAutomationPaused())
    driver._read_vehicle_settings = AsyncMock()
    driver.ensure_overview = AsyncMock(side_effect=CompanionAutomationPaused())
    with pytest.raises(CompanionAutomationPaused):
        await driver.read_extended()
    driver._read_vehicle_settings.assert_not_awaited()


async def test_paused_cached_car_data_keeps_current_phone_battery() -> None:
    client = object.__new__(CompanionClient)
    client._channel = AsyncMock()
    client._channel.read.return_value = None
    client._transport = AsyncMock()
    client._transport.device_battery_level.return_value = 51
    client._last_data = VehicleData(vin="TESTVIN")
    client._last_data.battery_soc = 40
    client._last_data.companion_phone_battery_level = 65
    result = await client.get_status("TESTVIN")
    assert result.battery_soc == 40
    assert result.companion_phone_battery_level == 51
    client._transport.foreground_app.assert_not_awaited()
