# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grounded ID.3 / We Connect 4.3.2 extended companion reads."""
from __future__ import annotations

from dataclasses import replace
import re
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vag_connect.companion.channel import CompanionChannel
from custom_components.vag_connect.companion.presets import PRESETS
from custom_components.vag_connect.companion.screen import (
    parse_ui_dump,
    read_fields,
    read_selectors,
)
from custom_components.vag_connect.companion.vw_screen import (
    parse_climate,
    parse_climate_settings,
    parse_shared_location,
    parse_overview_charging,
    parse_overview_openings,
    parse_vehicle_health,
    parse_vehicle_settings,
    parse_zones,
)
from custom_components.vag_connect.companion.vw_driver import VolkswagenAppDriver
from custom_components.vag_connect.companion.transport import CompanionTransportError


def _dump(*nodes: str) -> str:
    return '<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>"


def _node(
    *,
    rid: str = "",
    text: str = "",
    desc: str = "",
    bounds: str = "[0,0][100,100]",
    clickable: bool = False,
    checkable: bool = False,
    checked: bool = False,
) -> str:
    return (
        f'<node resource-id="{rid}" text="{text}" content-desc="{desc}" '
        f'class="android.view.View" bounds="{bounds}" '
        f'clickable="{str(clickable).lower()}" '
        f'checkable="{str(checkable).lower()}" '
        f'checked="{str(checked).lower()}" enabled="true" />'
    )


def test_overview_reads_lock_range_and_climate_state() -> None:
    nodes = parse_ui_dump(
        _dump(
            _node(
                desc=(
                    "Your vehicle: ID.3 Pro Performance. Vehicle is locked. "
                    "Synchronised 58 minutes ago"
                )
            ),
            _node(desc="Range overview. Battery range: 215 kilometres. Open details"),
            _node(desc="Climate control. Off. Open details"),
        )
    )
    got = read_fields(nodes, PRESETS["volkswagen"])
    assert got["doors_locked"] is True
    assert got["electric_range_km"] == 215
    assert got["climatisation_state"] == "Off"
    assert got["climatisation_active"] is False


def test_climate_reads_centre_target_and_outside_temperature() -> None:
    nodes = parse_ui_dump(
        _dump(
            _node(rid="outside_temperature_layout", bounds="[0,895][1080,942]"),
            _node(text="Example City: 16°C", bounds="[400,895][680,942]"),
            _node(rid="clima_compose_view", bounds="[0,1005][1080,1341]"),
            _node(text="21.5", bounds="[0,1032][192,1193]"),
            _node(text="22", bounds="[412,1005][620,1220]"),
            _node(text="22.5", bounds="[852,1032][1080,1193]"),
            _node(rid="window_heating_description", text="Autom."),
        )
    )
    assert parse_climate(nodes) == {
        "target_temperature": 22.0,
        "outside_temp": 16.0,
    }


def test_climate_preferences_and_zones_read_checked_state() -> None:
    settings = parse_ui_dump(
        _dump(
            _node(
                rid="ClimatisationAtUnlockEnabled",
                checkable=True,
                checked=True,
            ),
            _node(rid="WindowHeatingEnabled", checkable=True, checked=False),
        )
    )
    assert parse_climate_settings(settings) == {
        "climate_at_unlock": True,
        "climatisation_at_unlock": True,
        "window_heating_enabled": False,
    }

    zones = parse_ui_dump(
        _dump(
            _node(text="Front left", bounds="[55,859][240,913]"),
            _node(
                bounds="[0,798][1080,974]",
                clickable=True,
                checkable=True,
                checked=False,
            ),
            _node(text="Front right", bounds="[55,1038][265,1092]"),
            _node(
                bounds="[0,977][1080,1153]",
                clickable=True,
                checkable=True,
                checked=True,
            ),
        )
    )
    assert parse_zones(zones) == {
        "climate_zone_front_left_enabled": False,
        "climate_zone_front_right_enabled": True,
    }


def test_vehicle_settings_read_limit_and_toggle_rows() -> None:
    nodes = parse_ui_dump(
        _dump(
            _node(rid="value", text="60%", bounds="[863,991][970,1045]"),
            _node(text="Battery Care Mode", bounds="[44,1543][893,1597]"),
            _node(
                bounds="[44,1504][1036,1636]",
                clickable=True,
                checkable=True,
                checked=True,
            ),
            _node(
                text="Reduced AC charging current",
                bounds="[44,1912][893,1966]",
            ),
            _node(
                bounds="[44,1873][1036,2005]",
                clickable=True,
                checkable=True,
                checked=False,
            ),
            _node(
                text="Automatically release AC connector",
                bounds="[44,2113][893,2167]",
            ),
            _node(
                bounds="[44,2074][1036,2206]",
                clickable=True,
                checkable=True,
                checked=False,
            ),
        )
    )
    assert parse_vehicle_settings(nodes) == {
        "target_soc": 60,
        "battery_care_enabled": True,
        "max_charging_current": "MAXIMUM",
        "auto_unlock_when_charged": False,
    }


def test_health_and_shared_location_are_typed() -> None:
    health = parse_ui_dump(
        _dump(
            _node(text="Total distance"),
            _node(text="51,034 km"),
            _node(text="Next service"),
            _node(text="77 days"),
        )
    )
    assert parse_vehicle_health(health) == {
        "odometer_km": 51034,
        "service_due_in_days": 77,
    }

    share = parse_ui_dump(
        _dump(
            _node(
                rid="content_preview_text",
                text="https://www.google.com/maps/place/50.123456,14.654321",
            )
        )
    )
    assert parse_shared_location(share) == {
        "latitude": 50.123456,
        "longitude": 14.654321,
    }


def test_target_reached_is_enabled_but_not_actively_charging() -> None:
    detail = parse_ui_dump(
        _dump(
            _node(
                desc=(
                    "Stop charging. Target charge level reached. "
                    "Currently not charging."
                )
            )
        )
    )
    nav = PRESETS["volkswagen"].nav_reads[0]
    got = read_selectors(detail, nav.values)
    assert got["charging_state"] == "TARGET_REACHED"
    assert got["is_charging"] is False


def test_conservation_charging_wins_over_conflicting_button_narration() -> None:
    """The live SoC badge is authoritative over the disabled target button."""
    detail = parse_ui_dump(
        _dump(
            _node(
                rid="rangeArcBatterySoc",
                text="61% • Conservation charging",
                desc=(
                    "Charging status. Battery charge level: 61 per cent. "
                    "Conservation charging"
                ),
            ),
            _node(
                desc=(
                    "Stop charging. Target charge level reached. "
                    "Currently not charging."
                )
            ),
        )
    )
    nav = PRESETS["volkswagen"].nav_reads[0]
    got = read_selectors(detail, nav.values)
    assert got["battery_soc"] == 61
    assert got["charging_state"] == "CONSERVATION_CHARGING"
    assert got["is_charging"] is True


@pytest.mark.parametrize(
    ("range_description", "soc_text", "expected_state", "expected_active"),
    (
        (
            "Range overview. Currently charging. Battery range: 218 kilometres. Open details",
            "Keep charge level • 60%",
            "CHARGING",
            True,
        ),
        (
            "Range overview. Battery range: 218 kilometres. Open details",
            "Keep charge level • 61%",
            "CONSERVATION_CHARGING",
            True,
        ),
        (
            "Range overview. Battery range: 218 kilometres. Open details",
            "61%",
            "NOT_CHARGING",
            False,
        ),
    ),
)
def test_overview_charging_has_three_explicit_states(
    range_description: str,
    soc_text: str,
    expected_state: str,
    expected_active: bool,
) -> None:
    nodes = parse_ui_dump(
        _dump(
            _node(rid="rangeTile"),
            _node(rid="climateTile"),
            _node(desc=range_description),
            _node(text=soc_text),
        )
    )
    assert parse_overview_charging(nodes) == {
        "battery_soc": int(re.search(r"\d+", soc_text).group()),  # type: ignore[union-attr]
        "charging_state": expected_state,
        "is_charging": expected_active,
    }


def test_companion_exposes_three_state_status_not_redundant_binary() -> None:
    from custom_components.vag_connect.binary_sensor import (
        _COMPANION_REDUNDANT_BINARY_KEYS,
    )
    from custom_components.vag_connect.sensor import _COMPANION_REDUNDANT_SENSOR_KEYS

    assert "is_charging" in _COMPANION_REDUNDANT_BINARY_KEYS
    assert "charging_state" not in _COMPANION_REDUNDANT_SENSOR_KEYS


def test_active_charging_reads_from_soc_narration() -> None:
    detail = parse_ui_dump(
        _dump(
            _node(
                rid="rangeArcBatterySoc",
                text="46% • Charging",
                desc=(
                    "Charging status. Battery charge level: 46 per cent. "
                    "Currently charging"
                ),
            )
        )
    )
    nav = PRESETS["volkswagen"].nav_reads[0]
    got = read_selectors(detail, nav.values)
    assert got["battery_soc"] == 46
    assert got["charging_state"] == "CHARGING"
    assert got["is_charging"] is True


def test_all_open_vehicle_semantics_maps_individual_openings() -> None:
    nodes = parse_ui_dump(_dump(_node(desc=(
        "Vehicle is unlocked. Something is still open or switched on:. "
        "Boot. Bonnet. Rear right-side door. Rear left-side door. "
        "Front right-side door. Front left-side door. "
        "Front left-side window. Front right-side window. "
        "Rear left-side window. Rear right-side window."
        " Front left-side light. Front right-side light."
        " Rear left-side light. Rear right-side light."
    ))))
    got = parse_overview_openings(nodes)
    assert got["doors_individual"] == {
        "frontLeft": True,
        "frontRight": True,
        "rearLeft": True,
        "rearRight": True,
    }
    assert got["windows_individual"] == {
        "frontLeft": False,
        "frontRight": False,
        "rearLeft": False,
        "rearRight": False,
    }
    assert got["trunk_open"] is True
    assert got["hood_open"] is True
    assert got["lights_individual"] == dict.fromkeys(
        ("frontLeft", "frontRight", "rearLeft", "rearRight"), True
    )
    assert got["lights_on"] is True
    assert got["lights_count"] == 4


def test_closed_doors_and_boot_keep_open_windows_distinct() -> None:
    nodes = parse_ui_dump(_dump(_node(desc=(
        "Vehicle is unlocked. Something is still open or switched on:. "
        "Front left-side window. Front right-side window. "
        "Rear left-side window. Rear right-side window."
        " Front left-side light. Front right-side light."
        " Rear left-side light. Rear right-side light."
    ))))
    got = parse_overview_openings(nodes)
    assert got["doors_individual"] == dict.fromkeys(
        ("frontLeft", "frontRight", "rearLeft", "rearRight"), False
    )
    assert got["windows_individual"] == dict.fromkeys(
        ("frontLeft", "frontRight", "rearLeft", "rearRight"), False
    )
    assert got["doors_open"] is False
    assert got["windows_open"] is True
    assert got["trunk_open"] is False
    assert got["hood_open"] is False
    assert got["lights_count"] == 4


def test_open_bonnet_and_open_windows_with_lights_off() -> None:
    nodes = parse_ui_dump(_dump(_node(desc=(
        "Vehicle is unlocked. Something is still open or switched on:. "
        "Bonnet. Front left-side window. Front right-side window. "
        "Rear left-side window. Rear right-side window."
    ))))
    got = parse_overview_openings(nodes)
    assert got["hood_open"] is True
    assert got["trunk_open"] is False
    assert got["doors_open"] is False
    assert got["windows_open"] is True
    assert got["lights_individual"] == dict.fromkeys(
        ("frontLeft", "frontRight", "rearLeft", "rearRight"), False
    )
    assert got["lights_on"] is False
    assert got["lights_count"] == 0


def test_complete_overview_without_alert_image_means_everything_closed() -> None:
    """The alert ImageView is absent, rather than empty, when all are closed."""
    nodes = parse_ui_dump(
        _dump(
            _node(rid="rangeTile"),
            _node(rid="climateTile"),
            _node(
                desc=(
                    "Your vehicle: ID.3 Pro Performance. Vehicle is locked. "
                    "Synchronised just now"
                )
            ),
        )
    )

    got = parse_overview_openings(nodes)

    assert got["doors_individual"] == dict.fromkeys(
        ("frontLeft", "frontRight", "rearLeft", "rearRight"), False
    )
    assert got["windows_individual"] == dict.fromkeys(
        ("frontLeft", "frontRight", "rearLeft", "rearRight"), True
    )
    assert got["doors_open"] is False
    assert got["windows_open"] is False
    assert got["trunk_open"] is False
    assert got["hood_open"] is False
    assert got["lights_on"] is False
    assert got["lights_count"] == 0


def test_incomplete_tree_without_alert_image_does_not_erase_openings() -> None:
    assert parse_overview_openings(
        parse_ui_dump(_dump(_node(rid="rangeTile"), _node(rid="climateTile")))
    ) == {}


class _DriverTransport:
    """Small state machine for command-flow tests; never touches a real car."""

    connected = True

    def __init__(self) -> None:
        self.screen = "overview"
        self.started_climate = False
        self.battery_care = True
        self.target_soc = 60
        self.temperature = 22.0
        self.taps: list[tuple[int, int]] = []
        self.back_calls = 0
        self.swipe_calls = 0

    async def connect(self) -> None:
        self.connected = True

    async def foreground_app(self, package: str) -> None:  # noqa: ARG002
        return None

    async def is_foreground(self, package: str) -> bool:  # noqa: ARG002
        return True

    async def current_app_version(self, package: str) -> str:  # noqa: ARG002
        return "4.3.2"

    def _overview(self) -> str:
        return _dump(
            _node(rid="rangeTile", bounds="[50,200][450,600]"),
            _node(rid="climateTile", bounds="[550,200][1000,600]"),
            _node(
                desc="Vehicle Health Report. Open details",
                bounds="[50,800][1000,950]",
            ),
            _node(
                desc="Settings. Open details",
                bounds="[50,1000][1000,1150]",
            ),
        )

    def _climate(self) -> str:
        return _dump(
            _node(rid="vwd_navigation_button", bounds="[0,0][120,120]"),
            _node(rid="clima_compose_view", bounds="[0,200][1080,600]"),
            _node(
                text=f"{self.temperature - 0.5:g}",
                bounds="[0,250][200,500]",
            ),
            _node(
                text=f"{self.temperature:g}",
                bounds="[420,220][660,520]",
            ),
            _node(
                text=f"{self.temperature + 0.5:g}",
                bounds="[880,250][1080,500]",
            ),
            _node(
                rid="cta_stop" if self.started_climate else "cta_start",
                text="Stop" if self.started_climate else "Start",
                bounds="[100,1000][900,1120]",
            ),
        )

    def _settings(self) -> str:
        return _dump(
            _node(rid="vwd_navigation_button", bounds="[0,0][120,120]"),
            _node(rid="subtitle", text="Charging up to (50-100%)", bounds="[100,300][900,360]"),
            _node(rid="value", text=f"{self.target_soc}%", bounds="[800,420][900,480]"),
            _node(text="Battery Care Mode", bounds="[50,600][700,660]"),
            _node(
                bounds="[40,560][1000,700]",
                clickable=True,
                checkable=True,
                checked=self.battery_care,
            ),
        )

    async def dump_ui(self) -> str:
        if self.screen == "loading":
            return _dump(_node(text="Loading"))
        if self.screen == "zones":
            return _dump(_node(clickable=True, bounds="[22,136][154,268]"))
        if self.screen == "climate":
            return self._climate()
        if self.screen == "settings":
            return self._settings()
        return self._overview()

    async def dump_active_ui(self) -> str:
        return await self.dump_ui()

    async def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        if self.screen != "overview" and x <= 120 and y <= 300:
            self.screen = "overview"
        elif self.screen == "overview" and x > 500 and y < 700:
            self.screen = "climate"
        elif self.screen == "overview" and 950 <= y <= 1200:
            self.screen = "settings"
        elif self.screen == "climate" and y >= 900:
            self.started_climate = not self.started_climate
        elif self.screen == "settings" and y >= 550:
            self.battery_care = not self.battery_care
        elif self.screen == "settings" and 350 <= y <= 550:
            self.target_soc = round((50 + 50 * ((x - 100) / 800)) / 10) * 10

    async def key_back(self) -> None:
        self.back_calls += 1
        self.screen = "overview"

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        dur_ms: int = 300,
    ) -> None:  # noqa: ARG002
        self.swipe_calls += 1
        if self.screen == "climate":
            self.temperature += 0.5 if x1 > x2 else -0.5
        return None


async def test_driver_starts_climate_via_detail_without_real_transport() -> None:
    transport = _DriverTransport()
    await VolkswagenAppDriver(transport, settle_s=0).start_climate()  # type: ignore[arg-type]
    assert transport.started_climate is True
    assert transport.screen == "overview"


async def test_driver_toggles_battery_care_only_when_needed() -> None:
    transport = _DriverTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    await driver.set_battery_care(False)
    assert transport.battery_care is False
    tap_count = len(transport.taps)
    await driver.set_battery_care(False)
    assert len(transport.taps) == tap_count + 2  # open + safe close; no switch tap


async def test_driver_sets_and_verifies_charge_limit() -> None:
    transport = _DriverTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    await driver.set_target_soc(80)
    assert transport.target_soc == 80


async def test_driver_swipes_temperature_until_target() -> None:
    transport = _DriverTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    await driver.set_temperature(23.5)
    assert transport.temperature == 23.5
    assert transport.screen == "overview"


async def test_driver_applies_temperature_and_start_in_one_detail_visit() -> None:
    transport = _DriverTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    await driver.apply_climate(23.5, True)
    assert transport.temperature == 23.5
    assert transport.started_climate is True
    assert transport.screen == "overview"


async def test_driver_applies_temperature_and_stop_in_one_detail_visit() -> None:
    transport = _DriverTransport()
    transport.started_climate = True
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    await driver.apply_climate(20.0, False)
    assert transport.temperature == 20.0
    assert transport.started_climate is False
    assert transport.screen == "overview"


async def test_unknown_loading_screen_never_global_backs_out_of_app() -> None:
    transport = _DriverTransport()
    transport.screen = "loading"
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    with pytest.raises(CompanionTransportError, match="could not return"):
        await driver.ensure_overview()
    assert transport.back_calls == 0


async def test_idless_top_left_vw_navigation_is_safe() -> None:
    transport = _DriverTransport()
    transport.screen = "zones"
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    nodes = await driver.ensure_overview()
    assert any(node.resource_id == "rangeTile" for node in nodes)
    assert transport.back_calls == 0


async def test_small_display_uses_visible_overview_without_scroll() -> None:
    transport = _DriverTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    nodes = await driver._overview_scrolled()
    assert any(node.content_desc.startswith("Settings.") for node in nodes)
    assert transport.swipe_calls == 0


async def test_extended_option_rebuilds_companion_client() -> None:
    from custom_components.vag_connect import _async_update_listener
    from custom_components.vag_connect.const import CONF_COMPANION_READ_EXTENDED

    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "entry-id"
    entry.runtime_data = None
    entry.data = {CONF_COMPANION_READ_EXTENDED: False}
    entry.options = {CONF_COMPANION_READ_EXTENDED: True}

    await _async_update_listener(hass, entry)

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        data={CONF_COMPANION_READ_EXTENDED: True},
        options={},
    )
    hass.config_entries.async_reload.assert_awaited_once_with("entry-id")


async def test_event_overview_merges_individual_openings_without_poll() -> None:
    from custom_components.vag_connect.const import STRATEGY_COMPANION_ADB
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    vin = "WVWZZZSYNTHETIC01"
    coordinator = VagConnectCoordinator.__new__(VagConnectCoordinator)
    coordinator.entry = MagicMock()
    coordinator.entry.data = {
        "brand": "volkswagen",
        "strategy": STRATEGY_COMPANION_ADB,
        "vin": vin,
    }
    coordinator._vehicles_lock = threading.Lock()
    coordinator.vehicles = {vin: {"vin": vin, "battery_soc": 50}}
    coordinator.vehicle_last_good_at = {}
    coordinator.async_set_updated_data = MagicMock()
    xml = _dump(
        _node(rid="rangeTile"),
        _node(rid="climateTile"),
        _node(desc="Range overview. Battery range: 218 kilometres. Open details"),
        _node(text="Keep charge level • 61%"),
        _node(desc=(
            "Vehicle is unlocked. Something is still open or switched on:. "
            "Boot. Front left-side door. Rear right-side window. "
            "Front left-side light."
        )),
    )

    await coordinator._async_companion_event_snapshot(xml, 77)

    updated = coordinator.vehicles[vin]
    assert updated["doors_individual"]["frontLeft"] is True
    assert updated["doors_individual"]["frontRight"] is False
    assert updated["windows_individual"]["rearRight"] is False
    assert updated["trunk_open"] is True
    assert updated["lights_individual"]["frontLeft"] is True
    assert updated["lights_count"] == 1
    assert updated["battery_soc"] == 61
    assert updated["charging_state"] == "CONSERVATION_CHARGING"
    assert updated["is_charging"] is True
    coordinator.async_set_updated_data.assert_called_once()


async def test_phone_battery_heartbeat_updates_ha_without_vw_navigation() -> None:
    from custom_components.vag_connect.const import STRATEGY_COMPANION_ADB
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    vin = "WVWZZZSYNTHETIC01"
    coordinator = VagConnectCoordinator.__new__(VagConnectCoordinator)
    coordinator.entry = MagicMock()
    coordinator.entry.data = {
        "strategy": STRATEGY_COMPANION_ADB,
        "vin": vin,
    }
    coordinator._vehicles_lock = threading.Lock()
    coordinator.vehicles = {vin: {"vin": vin}}
    coordinator.async_set_updated_data = MagicMock()

    await coordinator._async_companion_phone_battery(67)

    assert coordinator.vehicles[vin]["companion_phone_battery_level"] == 67
    coordinator.async_set_updated_data.assert_called_once()


def test_phone_battery_sensor_is_diagnostic_and_phantom_gated() -> None:
    from homeassistant.const import EntityCategory

    from custom_components.vag_connect.sensor import (
        SENSOR_DESCRIPTIONS,
        _DATA_PRESENT_REQUIRED,
    )

    description = next(
        item
        for item in SENSOR_DESCRIPTIONS
        if item.key == "companion_phone_battery_level"
    )
    assert description.entity_category is EntityCategory.DIAGNOSTIC
    assert "companion_phone_battery_level" in _DATA_PRESENT_REQUIRED


async def test_extended_refresh_finishes_with_fresh_overview_in_same_result() -> None:
    """Changes made while detail pages are open must not wait for another poll."""
    transport = MagicMock()
    preset = replace(PRESETS["volkswagen"], nav_reads=())
    channel = CompanionChannel(
        transport,
        preset,
        time_fn=lambda: 0.0,
        read_extended=True,
    )
    final_overview = parse_ui_dump(
        _dump(
            _node(rid="rangeTile"),
            _node(rid="climateTile"),
            _node(desc="Battery charge level: 44 per cent"),
            _node(desc="Battery range: 187 kilometres"),
            _node(desc="Climate control. On. Open details"),
            _node(
                desc=(
                    "Vehicle is unlocked. Something is still open or switched on:. "
                    "Bonnet. Front left-side window."
                )
            ),
        )
    )
    fields: dict[str, object] = {
        "battery_soc": 60,
        "electric_range_km": 240,
        "climatisation_active": False,
    }

    with patch(
        "custom_components.vag_connect.companion.vw_driver.VolkswagenAppDriver"
    ) as driver_cls:
        driver = driver_cls.return_value
        driver.read_extended = AsyncMock(return_value={"target_soc": 80})
        driver.ensure_overview = AsyncMock(return_value=final_overview)

        await channel._augment_via_nav(fields)

    assert fields["target_soc"] == 80
    assert fields["battery_soc"] == 44
    assert fields["electric_range_km"] == 187
    assert fields["climatisation_active"] is True
    assert fields["doors_locked"] is False
    assert fields["hood_open"] is True
    assert fields["windows_individual"]["frontLeft"] is False  # type: ignore[index]
    driver.ensure_overview.assert_awaited_once()


async def test_final_overview_race_keeps_initial_poll_instead_of_raising() -> None:
    """A transient final navigation failure is not a whole-poll failure."""
    transport = MagicMock()
    preset = replace(PRESETS["volkswagen"], nav_reads=())
    channel = CompanionChannel(
        transport,
        preset,
        time_fn=lambda: 0.0,
        read_extended=True,
    )
    fields: dict[str, object] = {
        "battery_soc": 58,
        "doors_locked": True,
    }

    with patch(
        "custom_components.vag_connect.companion.vw_driver.VolkswagenAppDriver"
    ) as driver_cls:
        driver = driver_cls.return_value
        driver.read_extended = AsyncMock(return_value={"target_soc": 60})
        driver.ensure_overview = AsyncMock(
            side_effect=CompanionTransportError(
                "Volkswagen app: could not return to overview"
            )
        )

        await channel._augment_via_nav(fields)

    assert fields == {
        "battery_soc": 58,
        "doors_locked": True,
        "target_soc": 60,
    }
