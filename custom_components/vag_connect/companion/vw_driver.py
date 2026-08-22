# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Navigation and command flows for the verified Volkswagen Android app."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .screen import UiNode, parse_ui_dump
from .transport import CompanionTransportError, NetworkAdbTransport
from .vw_screen import (
    find_by_desc,
    find_by_rid,
    find_by_text,
    parse_climate,
    parse_climate_settings,
    parse_shared_location,
    parse_vehicle_health,
    parse_vehicle_settings,
    parse_zones,
    row_toggle,
)


class VolkswagenAppDriver:
    """Drive We Connect 4.3.2 through grounded accessibility identifiers."""

    def __init__(
        self,
        transport: NetworkAdbTransport,
        *,
        settle_s: float = 0.25,
    ) -> None:
        self._t = transport
        self._settle_s = settle_s
        self._foregrounded = False

    async def _settle(self) -> None:
        if self._settle_s:
            await asyncio.sleep(self._settle_s)

    async def _nodes(self) -> list[UiNode]:
        return parse_ui_dump(await self._t.dump_ui())

    async def _wait_for_nodes(
        self,
        predicate: Callable[[list[UiNode]], bool],
        *,
        reason: str,
        active_window: bool = False,
        require_stable: bool = True,
        timeout_s: float = 4.0,
    ) -> list[UiNode]:
        """Poll the fast agent until the destination screen is actually ready."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        last_error: CompanionTransportError | None = None
        last_signature: tuple[object, ...] | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                xml = await (
                    self._t.dump_active_ui()
                    if active_window
                    else self._t.dump_ui()
                )
                nodes = parse_ui_dump(xml)
                if predicate(nodes):
                    if not require_stable:
                        return nodes
                    signature: tuple[object, ...] = tuple(
                        (
                            node.resource_id,
                            node.text,
                            node.content_desc,
                            node.bounds,
                            node.clickable,
                            node.checked,
                        )
                        for node in nodes
                    )
                    if signature == last_signature:
                        return nodes
                    last_signature = signature
                else:
                    last_signature = None
            except CompanionTransportError as err:
                last_error = err
            await asyncio.sleep(0.15)
        suffix = f": {last_error}" if last_error else ""
        raise CompanionTransportError(
            f"Volkswagen app: timed out waiting for {reason}{suffix}"
        )

    async def _tap(self, node: UiNode | None, *, reason: str) -> None:
        if node is None or node.tap_point is None:
            raise CompanionTransportError(f"Volkswagen app: could not find {reason}")
        await self._t.tap(*node.tap_point)
        await self._settle()

    async def _tap_rid(self, nodes: list[UiNode], rid: str) -> None:
        await self._tap(find_by_rid(nodes, rid), reason=f"resource-id {rid}")

    async def _tap_desc(self, nodes: list[UiNode], pattern: str) -> None:
        await self._tap(find_by_desc(nodes, pattern), reason=f"description {pattern}")

    async def _tap_text(self, nodes: list[UiNode], pattern: str) -> None:
        await self._tap(find_by_text(nodes, pattern), reason=f"text {pattern}")

    @staticmethod
    def _safe_navigation_node(nodes: list[UiNode]) -> UiNode | None:
        """Find a VW-owned close/up control without ever using global Back."""
        for resource_id in (
            "vwd_navigation_button",
            "vehicleHealthBack",
            "climatisationSettingsLeading",
        ):
            node = find_by_rid(nodes, resource_id)
            if node is not None and node.tap_point is not None:
                return node
        # We Connect 4.3.2's Zones screen exposes its up button without text,
        # description OR resource-id. Restrict the fallback to a clickable node
        # wholly inside the physical top-left navigation slot. Overview has
        # already been ruled out before this method is called.
        for node in nodes:
            if not node.clickable or node.bounds is None:
                continue
            left, top, right, bottom = node.bounds
            if left <= 30 and 100 <= top <= 180 and right <= 180 and bottom <= 310:
                return node
        return None

    async def ensure_overview(self) -> list[UiNode]:
        """Return through VW-owned controls; never global-BACK out of the app."""
        for _ in range(12):
            if (
                not self._foregrounded
                or not await self._t.is_foreground("com.volkswagen.weconnect")
            ):
                await self._t.foreground_app("com.volkswagen.weconnect")
                self._foregrounded = True
                # Foreground becomes true while the splash/Compose tree may
                # still be empty. Do not interpret that loading window as a
                # nested screen and immediately BACK out of the app.
                await self._settle()
            nodes = await self._nodes()
            if find_by_rid(nodes, "rangeTile") and find_by_rid(nodes, "climateTile"):
                return nodes
            vehicle_tab = find_by_rid(nodes, "vehicle_tab_navigation")
            if vehicle_tab is not None and vehicle_tab.clickable:
                await self._tap(vehicle_tab, reason="Vehicle tab")
                continue
            # VW detail sheets expose their own X/up button. Tapping it is
            # bounded to the app; unlike GLOBAL_ACTION_BACK it can never reveal
            # Android Settings when the Compose navigation stack is shallow.
            navigation = self._safe_navigation_node(nodes)
            if navigation is not None and navigation.tap_point is not None:
                await self._tap(navigation, reason="Volkswagen navigation button")
                continue
            # Splash/loading/animation: wait for a known safe control. On an
            # unknown redesign, fail visibly after the cap instead of guessing.
            await self._settle()
        raise CompanionTransportError("Volkswagen app: could not return to overview")

    async def _overview_scrolled(self) -> list[UiNode]:
        overview = await self.ensure_overview()
        health_visible = find_by_desc(
            overview, r"^Vehicle Health Report\. Open details$"
        )
        settings_visible = find_by_desc(overview, r"^Settings\. Open details$")
        if health_visible is not None and settings_visible is not None:
            return overview
        await self._t.swipe(540, 1900, 540, 850, 500)
        await self._settle()
        return await self._wait_for_nodes(
            lambda nodes: find_by_desc(nodes, r"^Settings\. Open details$") is not None,
            reason="scrolled vehicle overview",
        )

    async def _read_climate(self) -> dict[str, object]:
        try:
            nodes = await self.ensure_overview()
            await self._tap_rid(nodes, "climateTile")
            detail = await self._wait_for_nodes(
                lambda current: find_by_rid(current, "clima_compose_view") is not None,
                reason="climate detail",
            )
            out = parse_climate(detail)
            await self._tap_rid(detail, "clima_settings_compose_view")
            settings = await self._wait_for_nodes(
                lambda current: (
                    find_by_rid(current, "climatisationSettingsLeading") is not None
                    or find_by_rid(current, "ClimatisationAtUnlockEnabled") is not None
                ),
                reason="climate settings",
            )
            out.update(parse_climate_settings(settings))
            if find_by_text(settings, r"^Zones$") is not None:
                await self._tap_text(settings, r"^Zones$")
                zones = await self._wait_for_nodes(
                    lambda current: find_by_text(current, r"^Front left$") is not None,
                    reason="climate zones",
                )
                out.update(parse_zones(zones))
            return out
        finally:
            await self.ensure_overview()

    async def _read_vehicle_settings(self) -> dict[str, object]:
        opened = False
        try:
            nodes = await self._overview_scrolled()
            await self._tap_desc(nodes, r"^Settings\. Open details$")
            opened = True
            settings = await self._wait_for_nodes(
                lambda current: find_by_rid(current, "subtitle") is not None,
                reason="vehicle settings",
            )
            return parse_vehicle_settings(settings)
        finally:
            if opened:
                await self.ensure_overview()

    async def _read_health(self) -> dict[str, object]:
        opened = False
        try:
            nodes = await self._overview_scrolled()
            await self._tap_desc(nodes, r"^Vehicle Health Report\. Open details$")
            opened = True
            health = await self._wait_for_nodes(
                lambda current: find_by_rid(current, "vehicleHealthBack") is not None,
                reason="vehicle health report",
            )
            return parse_vehicle_health(health)
        finally:
            if opened:
                await self.ensure_overview()

    async def _read_location(self) -> dict[str, object]:
        nodes = await self.ensure_overview()
        await self._tap_rid(nodes, "cat_nav_map_tab_navigation")
        map_nodes = await self._wait_for_nodes(
            lambda current: find_by_desc(current, r"^Find vehicle$") is not None,
            reason="vehicle map",
        )
        await self._tap_desc(map_nodes, r"^Find vehicle$")
        map_nodes = await self._wait_for_nodes(
            lambda current: find_by_desc(current, r"^Google Map$") is not None,
            reason="centred vehicle map",
        )
        google_map = find_by_desc(map_nodes, r"^Google Map$")
        if google_map is None or google_map.bounds is None:
            raise CompanionTransportError("Volkswagen app: Google Map is unavailable")
        left, top, right, bottom = google_map.bounds
        # Find vehicle centres the marker in the upper map viewport. The marker
        # itself is intentionally absent from the accessibility tree.
        marker_x = (left + right) // 2
        marker_y = round(top + (bottom - top) * 0.43)
        await self._t.tap(marker_x, marker_y)
        vehicle_card = await self._wait_for_nodes(
            lambda current: find_by_text(current, r"^Share$") is not None,
            reason="vehicle map card",
        )
        await self._tap_text(vehicle_card, r"^Share$")
        share_sheet = await self._wait_for_nodes(
            lambda current: bool(parse_shared_location(current)),
            reason="shared Google Maps URL",
            active_window=True,
            require_stable=False,
        )
        return parse_shared_location(share_sheet)

    async def read_extended(self) -> dict[str, object]:
        """Read every confirmed multi-screen ID.3 value, failure-soft per route."""
        out: dict[str, object] = {}
        routes: tuple[Callable[[], Awaitable[dict[str, object]]], ...] = (
            self._read_climate,
            self._read_vehicle_settings,
            self._read_health,
            self._read_location,
        )
        for route in routes:
            try:
                out.update(await route())
            except CompanionTransportError:
                # One redesigned screen must not discard values from the other
                # three; the channel's overview read remains authoritative.
                pass
            finally:
                try:
                    await self.ensure_overview()
                except CompanionTransportError:
                    pass
        return out

    async def _open_climate(self) -> list[UiNode]:
        nodes = await self.ensure_overview()
        await self._tap_rid(nodes, "climateTile")
        return await self._wait_for_nodes(
            lambda current: find_by_rid(current, "clima_compose_view") is not None,
            reason="climate detail",
        )

    async def start_climate(self) -> None:
        nodes = await self._open_climate()
        button = find_by_rid(nodes, "cta_start") or find_by_text(nodes, r"^Start$")
        await self._tap(button, reason="Start climate button")
        await self.ensure_overview()

    async def stop_climate(self) -> None:
        nodes = await self._open_climate()
        button = find_by_rid(nodes, "cta_stop") or find_by_text(nodes, r"^Stop$")
        await self._tap(button, reason="Stop climate button")
        await self.ensure_overview()

    async def _charging_action(self, label: str) -> None:
        nodes = await self.ensure_overview()
        await self._tap_rid(nodes, "rangeTile")
        detail = await self._nodes()
        button = find_by_desc(detail, rf"^{re.escape(label)}")
        if button is None and label == "Start charging":
            # The app keeps the charging request enabled after the target is
            # reached and therefore only offers Stop. Turning the HA switch on
            # again is already satisfied and must be a no-op, not an error.
            already_enabled = find_by_desc(
                detail,
                r"^Stop charging\..*Target charge level reached",
            )
            if already_enabled is not None:
                await self.ensure_overview()
                return
        if button is None and label == "Stop charging":
            already_stopped = find_by_desc(
                detail,
                r"^Start charging\..*(?:not charging|not connected)",
            )
            if already_stopped is not None:
                await self.ensure_overview()
                return
        await self._tap(button, reason=f"{label} button")
        await self.ensure_overview()

    async def start_charging(self) -> None:
        await self._charging_action("Start charging")

    async def stop_charging(self) -> None:
        await self._charging_action("Stop charging")

    @staticmethod
    def _temperature_nodes(nodes: list[UiNode]) -> tuple[float, list[tuple[float, UiNode]]]:
        container = find_by_rid(nodes, "clima_compose_view")
        if container is None or container.bounds is None:
            raise CompanionTransportError("Volkswagen app: temperature picker missing")
        left, top, right, bottom = container.bounds
        centre_x = (left + right) / 2
        values: list[tuple[float, UiNode]] = []
        for node in nodes:
            if not node.text or node.bounds is None:
                continue
            match = re.fullmatch(r"\d{1,2}(?:[.,]\d)?", node.text.strip())
            nleft, ntop, nright, nbottom = node.bounds
            if (
                match
                and left <= nleft <= nright <= right
                and top <= ntop <= nbottom <= bottom
            ):
                values.append((float(match.group().replace(",", ".")), node))
        if not values:
            raise CompanionTransportError("Volkswagen app: temperature values missing")
        current, _ = min(
            values,
            key=lambda pair: abs((pair[1].bounds[0] + pair[1].bounds[2]) / 2 - centre_x),  # type: ignore[index]
        )
        return current, values

    async def _set_temperature_on_open_climate(
        self, nodes: list[UiNode], target: float
    ) -> list[UiNode]:
        """Adjust the already-open climate wheel and keep the detail open."""
        target = round(float(target) * 2) / 2
        if not 16 <= target <= 30:
            raise CompanionTransportError("Volkswagen app: temperature must be 16-30 °C")
        for _ in range(30):
            current, _ = self._temperature_nodes(nodes)
            if current == target:
                return nodes
            container = find_by_rid(nodes, "clima_compose_view")
            if container is None or container.bounds is None:
                raise CompanionTransportError(
                    "Volkswagen app: temperature picker missing"
                )
            left, top, right, bottom = container.bounds
            y = (top + bottom) // 2
            width = right - left
            if target > current:
                x1, x2 = round(left + width * 0.72), round(left + width * 0.28)
            else:
                x1, x2 = round(left + width * 0.28), round(left + width * 0.72)
            await self._t.swipe(x1, y, x2, y, 350)
            await self._settle()
            nodes = await self._nodes()
            updated, _ = self._temperature_nodes(nodes)
            moved_correctly = updated > current if target > current else updated < current
            if not moved_correctly:
                raise CompanionTransportError(
                    f"Volkswagen app: temperature swipe moved {current:g} → "
                    f"{updated:g} °C in the wrong direction"
                )
        raise CompanionTransportError(
            f"Volkswagen app: temperature did not reach {target:g} °C"
        )

    async def set_temperature(self, target: float) -> None:
        nodes = await self._open_climate()
        await self._set_temperature_on_open_climate(nodes, target)
        await self.ensure_overview()

    async def apply_climate(self, target: float | None, enabled: bool) -> None:
        """Apply a staged target and HVAC mode in one climate-detail visit."""
        nodes = await self._open_climate()
        if target is not None:
            nodes = await self._set_temperature_on_open_climate(nodes, target)
        start = find_by_rid(nodes, "cta_start") or find_by_text(nodes, r"^Start$")
        stop = find_by_rid(nodes, "cta_stop") or find_by_text(nodes, r"^Stop$")
        if enabled:
            # Moving the wheel may itself start climate. If Stop is now shown,
            # the requested final state is already satisfied.
            if stop is None:
                await self._tap(start, reason="Start climate button")
        elif start is None:
            await self._tap(stop, reason="Stop climate button")
        await self.ensure_overview()

    async def _open_climate_settings(self) -> list[UiNode]:
        detail = await self._open_climate()
        await self._tap_rid(detail, "clima_settings_compose_view")
        return await self._wait_for_nodes(
            lambda current: (
                find_by_rid(current, "climatisationSettingsLeading") is not None
                or find_by_rid(current, "ClimatisationAtUnlockEnabled") is not None
            ),
            reason="climate settings",
        )

    async def _set_rid_toggle(self, rid: str, desired: bool) -> None:
        nodes = await self._open_climate_settings()
        toggle = find_by_rid(nodes, rid)
        if toggle is None or not toggle.checkable:
            raise CompanionTransportError(f"Volkswagen app: toggle {rid} missing")
        if toggle.checked != desired:
            await self._tap(toggle, reason=f"toggle {rid}")
            updated = find_by_rid(await self._nodes(), rid)
            if updated is None or updated.checked != desired:
                raise CompanionTransportError(
                    f"Volkswagen app: toggle {rid} did not reach {desired}"
                )
        await self.ensure_overview()

    async def set_auxiliary_at_unlock(self, enabled: bool) -> None:
        await self._set_rid_toggle("ClimatisationAtUnlockEnabled", enabled)

    async def set_automatic_window_heating(self, enabled: bool) -> None:
        await self._set_rid_toggle("WindowHeatingEnabled", enabled)

    async def set_zone(self, label: str, enabled: bool) -> None:
        settings = await self._open_climate_settings()
        await self._tap_text(settings, r"^Zones$")
        nodes = await self._nodes()
        toggle = row_toggle(nodes, rf"^{re.escape(label)}$")
        if toggle is None:
            raise CompanionTransportError(f"Volkswagen app: zone {label} missing")
        if toggle.checked != enabled:
            await self._tap(toggle, reason=f"zone {label}")
            updated = row_toggle(await self._nodes(), rf"^{re.escape(label)}$")
            if updated is None or updated.checked != enabled:
                raise CompanionTransportError(
                    f"Volkswagen app: zone {label} did not reach {enabled}"
                )
        await self.ensure_overview()

    async def _open_vehicle_settings(self) -> list[UiNode]:
        overview = await self._overview_scrolled()
        await self._tap_desc(overview, r"^Settings\. Open details$")
        return await self._wait_for_nodes(
            lambda current: find_by_rid(current, "subtitle") is not None,
            reason="vehicle settings",
        )

    async def _set_vehicle_toggle(self, label: str, enabled: bool) -> None:
        nodes = await self._open_vehicle_settings()
        toggle = row_toggle(nodes, rf"^{re.escape(label)}$")
        if toggle is None:
            raise CompanionTransportError(f"Volkswagen app: setting {label} missing")
        if toggle.checked != enabled:
            await self._tap(toggle, reason=label)
            updated = row_toggle(await self._nodes(), rf"^{re.escape(label)}$")
            if updated is None or updated.checked != enabled:
                raise CompanionTransportError(
                    f"Volkswagen app: setting {label} did not reach {enabled}"
                )
        await self.ensure_overview()

    async def set_battery_care(self, enabled: bool) -> None:
        await self._set_vehicle_toggle("Battery Care Mode", enabled)

    async def set_reduced_ac_current(self, enabled: bool) -> None:
        await self._set_vehicle_toggle("Reduced AC charging current", enabled)

    async def set_auto_release_connector(self, enabled: bool) -> None:
        await self._set_vehicle_toggle("Automatically release AC connector", enabled)

    async def set_target_soc(self, target: int) -> None:
        if not 50 <= target <= 100 or target % 10:
            raise CompanionTransportError(
                "Volkswagen app: charging limit must be 50-100% in 10% steps"
            )
        nodes = await self._open_vehicle_settings()
        current = parse_vehicle_settings(nodes).get("target_soc")
        if current == target:
            await self.ensure_overview()
            return
        subtitle = find_by_rid(nodes, "subtitle")
        value = find_by_rid(nodes, "value")
        if subtitle is None or subtitle.bounds is None or value is None or value.bounds is None:
            raise CompanionTransportError("Volkswagen app: charge-limit slider missing")
        left, _, right, _ = subtitle.bounds
        y = (value.bounds[1] + value.bounds[3]) // 2
        x = round(left + (right - left) * ((target - 50) / 50))
        await self._t.tap(x, y)
        await self._settle()
        actual = parse_vehicle_settings(await self._nodes()).get("target_soc")
        if actual != target:
            raise CompanionTransportError(
                f"Volkswagen app: charge-limit slider produced {actual!r}, expected {target}"
            )
        await self.ensure_overview()

    async def start_climate_control(self, **kwargs: Any) -> None:
        """Apply the optional temperature and start in one detail visit."""
        requested = {
            key: value
            for key, value in kwargs.items()
            if value is not None and key not in {"climatisation_mode", "temp_c"}
        }
        if requested:
            raise CompanionTransportError(
                "Volkswagen companion: set temperature, automatic window "
                "heating, auxiliary conditioning and zones with their own HA "
                "entities first; one rich command may not send several app "
                "requests inside the 60-second safety interval"
            )
        temp = kwargs.get("temp_c")
        await self.apply_climate(float(temp) if temp is not None else None, True)

    async def execute(self, action: str, **kwargs: Any) -> None:
        """Dispatch a version-gated companion action onto the grounded flow."""
        if action == "start_climate":
            await self.start_climate()
        elif action == "stop_climate":
            await self.stop_climate()
        elif action == "start_charging":
            await self.start_charging()
        elif action == "stop_charging":
            await self.stop_charging()
        elif action == "start_climate_control":
            await self.start_climate_control(**kwargs)
        elif action == "apply_climate":
            temp = kwargs.get("temp_c")
            await self.apply_climate(
                float(temp) if temp is not None else None,
                bool(kwargs["enabled"]),
            )
        elif action == "set_climate_temperature":
            await self.set_temperature(float(kwargs["temp_c"]))
        elif action == "set_target_soc":
            await self.set_target_soc(int(kwargs["target"]))
        elif action == "set_battery_care":
            await self.set_battery_care(bool(kwargs["enabled"]))
        elif action == "set_auxiliary_at_unlock":
            await self.set_auxiliary_at_unlock(bool(kwargs["enabled"]))
        elif action == "set_automatic_window_heating":
            await self.set_automatic_window_heating(bool(kwargs["enabled"]))
        elif action == "set_zone_front_left":
            await self.set_zone("Front left", bool(kwargs["enabled"]))
        elif action == "set_zone_front_right":
            await self.set_zone("Front right", bool(kwargs["enabled"]))
        elif action == "set_auto_unlock_plug":
            mode = str(kwargs.get("mode", "OFF")).upper()
            await self.set_auto_release_connector(mode not in {"OFF", "FALSE", "0"})
        elif action == "update_charging_settings":
            requested = [
                key
                for key in ("target_soc", "max_charge_current", "auto_unlock_charge")
                if kwargs.get(key) is not None
            ]
            if len(requested) > 1:
                raise CompanionTransportError(
                    "Volkswagen app: change one charging setting per command"
                )
            if kwargs.get("target_soc") is not None:
                await self.set_target_soc(int(kwargs["target_soc"]))
            if kwargs.get("max_charge_current") is not None:
                reduced = str(kwargs["max_charge_current"]).upper() == "REDUCED"
                await self.set_reduced_ac_current(reduced)
            if kwargs.get("auto_unlock_charge") is not None:
                await self.set_auto_release_connector(bool(kwargs["auto_unlock_charge"]))
        else:
            raise CompanionTransportError(f"Volkswagen app: unsupported action {action}")


VW_DRIVER_ACTIONS = frozenset(
    {
        "start_climate",
        "stop_climate",
        "start_climate_control",
        "apply_climate",
        "start_charging",
        "stop_charging",
        "set_climate_temperature",
        "set_target_soc",
        "set_battery_care",
        "set_auxiliary_at_unlock",
        "set_automatic_window_heating",
        "set_zone_front_left",
        "set_zone_front_right",
        "set_auto_unlock_plug",
        "update_charging_settings",
    }
)
