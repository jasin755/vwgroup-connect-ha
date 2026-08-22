# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Companion (ADB) client adapter — v3.0.0-alpha.

Presents the same duck-typed surface the coordinator already expects from a
CARIAD client (``authenticate`` / ``get_vehicles`` / ``get_status`` /
``command_*``), so the companion channel slots in as just another source with
no special-casing in the poll loop. The token/portal/MBB attributes the
coordinator touches via ``getattr(..., default)`` simply are not here, so those
paths fall through cleanly.

There is no network login: the "auth" is the phone already being signed into the
app. ``get_vehicles`` returns the single VIN the user entered at setup, because
this channel reads one phone showing one account's car.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..cariad.models import VehicleData
from .channel import CompanionChannel, CompanionWriteBlocked
from .presets import ACTION_TO_COMMAND, PRESETS
from .transport import NetworkAdbTransport

_LOGGER = logging.getLogger(__name__)


class CompanionClient:
    """Coordinator-compatible client backed by a companion phone."""

    def __init__(
        self,
        *,
        brand: str,
        vin: str,
        host: str,
        port: int,
        adbkey_path: str,
        time_fn: Callable[[], float],
        read_charge_detail: bool = False,
        read_extended: bool = False,
        wake_sleep: bool = False,
        use_addon: bool = False,
        use_agent: bool = False,
        use_relay: bool = False,
        addon_token: str = "",
        session: object | None = None,
        relay_broker: Any = None,
    ) -> None:
        self._brand = brand.lower()
        self._vin = vin.upper()
        preset = PRESETS.get(self._brand)
        if preset is None:
            raise ValueError(f"no companion preset for brand {brand!r}")
        self._transport: NetworkAdbTransport
        if use_relay:
            if relay_broker is None:
                raise ValueError("companion relay requested without a broker")
            from .relay import AgentRelayTransport  # noqa: PLC0415

            self._transport = AgentRelayTransport(
                relay_broker, wake_sleep=wake_sleep
            )
        elif use_agent:
            from .agent_transport import AgentHttpTransport  # noqa: PLC0415

            self._transport = AgentHttpTransport(
                host,
                port,
                token=addon_token,
                session=session,
                wake_sleep=wake_sleep,
            )
        elif use_addon:
            # host/port address the ADB Bridge add-on, which owns the phone
            # connection. Everything above the wire is identical, so the
            # channel below neither knows nor cares which transport it got.
            from .addon_transport import AddOnAdbTransport  # noqa: PLC0415

            self._transport = AddOnAdbTransport(
                host, port, token=addon_token, wake_sleep=wake_sleep
            )
        else:
            self._transport = NetworkAdbTransport(
                host, port, adbkey_path, wake_sleep=wake_sleep
            )
        self._channel = CompanionChannel(
            self._transport, preset, time_fn=time_fn,
            read_charge_detail=read_charge_detail,
            read_extended=read_extended,
        )
        # Last snapshot we actually read, so a throttled poll can return the
        # known values instead of a spurious no_data that the coordinator would
        # count as a failed poll (the channel reads far less often than the poll
        # interval on purpose).
        self._last_data: VehicleData | None = None
        # Attributes the coordinator reads via getattr with a default; declared
        # here so a plain attribute access also works.
        self.on_tokens_changed: Callable[[Any], None] | None = None
        self._eu_portal = None
        self._tokens = None

    # -- token/portal no-ops the coordinator may call directly ----------------

    def set_persisted_tokens(self, _tokens: Any) -> None:
        """No stored tokens on this channel; nothing to restore."""

    def set_website_authproxy_mode(self, *_args: Any, **_kwargs: Any) -> None:
        """No supplementary web channel on this channel."""

    # -- the read surface -----------------------------------------------------

    async def authenticate(self) -> None:
        """'Auth' is the phone being signed in; just open the ADB connection."""
        await self._transport.connect()

    async def get_vehicles(self) -> list[str]:
        return [self._vin]

    async def get_status(self, vin: str) -> VehicleData:
        """Read the app screen and map it onto a VehicleData for this VIN.

        A read that matches nothing returns a ``no_data`` VehicleData rather
        than raising, so the coordinator's own no-data failsafe (keep
        last-known-good visible) applies exactly as it does for a portal outage.
        """
        fields = await self._channel.read()
        # None = the channel is in its post-failure cooldown. Return the last
        # snapshot we actually took so the coordinator sees unchanged-but-valid
        # data, not a no_data its poll loop would count as a failure (which
        # would self-reinforce the cooldown). On the very first poll (nothing
        # read yet) fall through to a no_data VehicleData.
        if fields is None and self._last_data is not None:
            return self._last_data
        data = VehicleData(vin=vin.upper())
        data.source_channel = "companion_adb"
        if not fields:  # None (first-ever throttle) or {} (empty screen)
            data.no_data = True
            return data
        for key, val in fields.items():
            if hasattr(data, key):
                setattr(data, key, val)
        # Companion presets read only the values visible in the brand app, so
        # they do not receive the drivetrain metadata that the API clients do.
        # Derive it from concrete screen readings; otherwise electric entities
        # are filtered out even though SoC/range were parsed successfully.
        electric_signals = (
            data.battery_soc,
            data.electric_range_km,
            data.charging_state,
            data.is_charging,
            data.charging_power_kw,
            data.target_soc,
            data.remaining_charge_time_min,
        )
        combustion_signals = (
            data.fuel_level,
            data.combustion_range_km,
        )
        if any(value is not None for value in electric_signals):
            data.has_battery = True
        if any(value is not None for value in combustion_signals):
            data.has_combustion = True
        data.is_hybrid = data.has_battery and data.has_combustion
        data.is_electric = data.has_battery and not data.has_combustion
        if data.range_km is None and data.electric_range_km is not None:
            data.range_km = data.electric_range_km
        # A companion read is a two-way-capable source only when writes are on;
        # expose that so the entity layer can reflect it.
        data.companion_writes_enabled = self._channel.writes_enabled
        data.companion_source_age_s = self._channel.source_data_age_s
        self._last_data = data
        return data

    # -- the command surface --------------------------------------------------

    async def _dispatch(self, command_name: str, **kwargs: Any) -> None:
        action = next(
            (a for a, cmd in ACTION_TO_COMMAND.items() if cmd == command_name), None
        )
        if action is None:
            from ..cariad.exceptions import VehicleCommandError  # noqa: PLC0415

            raise VehicleCommandError(
                command_name,
                "this command is not available on the companion (ADB) channel",
            )
        try:
            await self._channel.do_action(action, **kwargs)
        except CompanionWriteBlocked as err:
            from ..cariad.exceptions import VehicleCommandError  # noqa: PLC0415

            raise VehicleCommandError(command_name, str(err)) from err

    async def command_start_climate(self, vin: str, *_a: Any, **_k: Any) -> None:
        await self._dispatch("command_start_climate")

    async def command_start_climate_control(
        self,
        vin: str,
        *,
        temp_c: float | None = None,
        glass_heating: bool | None = None,
        seat_fl: bool | None = None,
        seat_fr: bool | None = None,
        seat_rl: bool | None = None,
        seat_rr: bool | None = None,
        climatisation_at_unlock: bool | None = None,
        climatisation_mode: str | None = None,
        **_k: Any,
    ) -> None:
        await self._dispatch(
            "command_start_climate_control",
            temp_c=temp_c,
            glass_heating=glass_heating,
            seat_fl=seat_fl,
            seat_fr=seat_fr,
            seat_rl=seat_rl,
            seat_rr=seat_rr,
            climatisation_at_unlock=climatisation_at_unlock,
            climatisation_mode=climatisation_mode,
        )

    async def command_stop_climate(self, vin: str, *_a: Any, **_k: Any) -> None:
        await self._dispatch("command_stop_climate")

    async def command_start_charging(self, vin: str, *_a: Any, **_k: Any) -> None:
        await self._dispatch("command_start_charging")

    async def command_stop_charging(self, vin: str, *_a: Any, **_k: Any) -> None:
        await self._dispatch("command_stop_charging")

    async def command_set_climate_temperature(
        self, vin: str, temp_c: float, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_climate_temperature", temp_c=temp_c)

    async def command_set_target_soc(
        self, vin: str, target: int, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_target_soc", target=target)

    async def command_set_battery_care(
        self, vin: str, enabled: bool, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_battery_care", enabled=enabled)

    async def command_set_auto_unlock_plug(
        self, vin: str, mode: str, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_auto_unlock_plug", mode=mode)

    async def command_update_charging_settings(
        self,
        vin: str,
        *,
        target_soc: int | None = None,
        max_charge_current: str | None = None,
        auto_unlock_charge: bool | None = None,
        **_k: Any,
    ) -> None:
        await self._dispatch(
            "command_update_charging_settings",
            target_soc=target_soc,
            max_charge_current=max_charge_current,
            auto_unlock_charge=auto_unlock_charge,
        )

    async def command_set_companion_aux_air_conditioning(
        self, vin: str, enabled: bool, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_companion_aux_air_conditioning", enabled=enabled)

    async def command_set_companion_automatic_window_heating(
        self, vin: str, enabled: bool, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch(
            "command_set_companion_automatic_window_heating", enabled=enabled
        )

    async def command_set_companion_zone_front_left(
        self, vin: str, enabled: bool, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_companion_zone_front_left", enabled=enabled)

    async def command_set_companion_zone_front_right(
        self, vin: str, enabled: bool, *_a: Any, **_k: Any
    ) -> None:
        await self._dispatch("command_set_companion_zone_front_right", enabled=enabled)

    # -- rate-limit persistence + manual reset (delegated to the channel) ------

    @property
    def companion_rate_limited_until(self) -> float:
        """Wall-clock time until the channel is backed off (0 = not). The
        coordinator persists this so a lockout survives a restart."""
        return self._channel.rate_limited_until

    def restore_rate_limit(self, until: float) -> None:
        """Re-apply a persisted rate-limit backoff at setup."""
        self._channel.restore_rate_limit(until)

    def reset_cooldown(self) -> None:
        """Clear a stuck failure/rate-limit backoff (user-initiated retry)."""
        self._channel.reset_cooldown()

    async def close(self) -> None:
        await self._transport.close()
