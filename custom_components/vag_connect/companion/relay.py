# Copyright 2026 Nikolaj Pognerebko — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound HTTPS relay between Home Assistant and the Android companion agent.

The phone long-polls a token-protected HA endpoint. Commands travel in the
response and their result arrives in the next poll. This direction works across
VLANs, carrier-style Wi-Fi isolation and changing phone addresses, while ADB is
needed only for installing/provisioning the Android agent.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import time
import uuid
from typing import Any, cast

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .transport import CompanionTransportError, NetworkAdbTransport

_LOGGER = logging.getLogger(__name__)

_REGISTRY_KEY = "vag_connect_companion_relay_registry"
_VIEW_REGISTERED_KEY = "vag_connect_companion_relay_view_registered"
_ONLINE_WINDOW_S = 45.0
_POLL_WAIT_S = 20.0


class CompanionRelayBroker:
    """One config entry's authenticated command/result rendezvous."""

    def __init__(self, entry_id: str, token: str) -> None:
        self.entry_id = entry_id
        self.token = token
        self._commands: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._online_event = asyncio.Event()
        self.last_seen: float = 0.0
        self.agent_version: str | None = None
        self.vw_version: str | None = None

    @property
    def online(self) -> bool:
        return bool(self.last_seen and time.monotonic() - self.last_seen < _ONLINE_WINDOW_S)

    async def wait_online(self, timeout_s: float) -> None:
        if self.online:
            return
        self._online_event.clear()
        try:
            await asyncio.wait_for(self._online_event.wait(), timeout_s)
        except TimeoutError as err:
            raise CompanionTransportError(
                "the Android companion agent has not connected to Home Assistant; "
                "check its Accessibility service, Wi-Fi and relay provisioning"
            ) from err

    async def handle_poll(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Record agent health/result and return the next still-live command."""
        self.last_seen = time.monotonic()
        self.agent_version = str(payload.get("agent_version") or "") or None
        self.vw_version = str(payload.get("vw_version") or "") or None
        self._online_event.set()

        result = payload.get("result")
        if isinstance(result, dict):
            command_id = str(result.get("id") or "")
            future = self._pending.get(command_id)
            if future is not None and not future.done():
                future.set_result(result)

        deadline = time.monotonic() + _POLL_WAIT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                command = await asyncio.wait_for(self._commands.get(), remaining)
            except TimeoutError:
                return None
            command_id = str(command.get("id") or "")
            future = self._pending.get(command_id)
            if future is not None and not future.done():
                return command
            # The HA caller timed out/cancelled before the phone collected this
            # command. Discard it so a stale climate/charging action never runs.

    async def command(
        self,
        action: str,
        *,
        timeout_s: float = 12.0,
        **params: object,
    ) -> dict[str, Any]:
        await self.wait_online(min(timeout_s, 5.0))
        command_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[command_id] = future
        await self._commands.put({"id": command_id, "action": action, "params": params})
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError as err:
            raise CompanionTransportError(
                f"companion relay command '{action}' timed out"
            ) from err
        finally:
            self._pending.pop(command_id, None)

    def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()


def _registry(hass: HomeAssistant) -> dict[str, CompanionRelayBroker]:
    return cast(
        dict[str, CompanionRelayBroker],
        hass.data.setdefault(_REGISTRY_KEY, {}),
    )


def register_relay(
    hass: HomeAssistant, entry_id: str, token: str
) -> CompanionRelayBroker:
    ensure_relay_view(hass)
    registry = _registry(hass)
    broker = registry.get(entry_id)
    if broker is None:
        broker = CompanionRelayBroker(entry_id, token)
        registry[entry_id] = broker
    else:
        broker.token = token
    return broker


def unregister_relay(hass: HomeAssistant, entry_id: str) -> None:
    broker = _registry(hass).pop(entry_id, None)
    if broker is not None:
        broker.close()


class CompanionAgentRelayView(HomeAssistantView):
    """Unauthenticated HA route protected by the per-entry agent token."""

    url = "/api/vag_connect/companion_agent/{entry_id}"
    name = "api:vag_connect:companion_agent"
    requires_auth = False

    async def post(self, request: Any, entry_id: str) -> Any:
        hass: HomeAssistant = request.app["hass"]
        broker = _registry(hass).get(entry_id)
        if broker is None:
            return self.json({"error": "unknown channel"}, status_code=404)
        supplied = request.headers.get("X-Token", "")
        if not supplied or not hmac.compare_digest(supplied, broker.token):
            return self.json({"error": "forbidden"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - malformed/untrusted phone request
            return self.json({"error": "invalid json"}, status_code=400)
        if not isinstance(payload, dict):
            return self.json({"error": "invalid payload"}, status_code=400)
        command = await broker.handle_poll(payload)
        return self.json({"command": command})


def ensure_relay_view(hass: HomeAssistant) -> None:
    if hass.data.get(_VIEW_REGISTERED_KEY):
        return
    hass.http.register_view(CompanionAgentRelayView)
    hass.data[_VIEW_REGISTERED_KEY] = True


class AgentRelayTransport(NetworkAdbTransport):
    """NetworkAdbTransport-compatible facade backed by an outbound relay."""

    def __init__(
        self,
        broker: CompanionRelayBroker,
        *,
        wake_sleep: bool = False,
    ) -> None:
        super().__init__("relay", 0, "", wake_sleep=wake_sleep)
        self._broker = broker

    async def connect(self, timeout_s: float = 10.0) -> None:
        await self._broker.wait_online(timeout_s)
        self._device = True

    async def close(self) -> None:
        self._device = None

    @property
    def connected(self) -> bool:
        return self._device is not None and self._broker.online

    async def shell(self, cmd: str, timeout_s: float = 10.0) -> str:
        del cmd, timeout_s
        raise CompanionTransportError("the companion relay does not expose ADB shell")

    @staticmethod
    def _require_ok(action: str, result: dict[str, Any]) -> dict[str, Any]:
        status = str(result.get("status") or "")
        if status not in {"ok", "accepted"}:
            raise CompanionTransportError(
                f"companion relay action '{action}' failed: "
                f"{result.get('error') or status or 'unknown error'}"
            )
        return result

    async def dump_ui(self, timeout_s: float = 15.0) -> str:
        result = self._require_ok(
            "snapshot",
            await self._broker.command("snapshot", timeout_s=timeout_s),
        )
        encoded = str(result.get("xml_b64") or "")
        try:
            xml = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as err:
            raise CompanionTransportError(
                "the companion relay returned an invalid accessibility snapshot"
            ) from err
        if "<hierarchy" not in xml:
            raise CompanionTransportError(
                "the companion relay returned no Volkswagen accessibility tree"
            )
        return xml

    async def foreground_app(self, package: str, timeout_s: float = 10.0) -> None:
        await self.wake(timeout_s)
        if await self.is_foreground(package, timeout_s):
            return
        self._require_ok(
            "launch",
            await self._broker.command(
                "launch", package=package, timeout_s=timeout_s
            ),
        )
        deadline = asyncio.get_running_loop().time() + min(timeout_s, 3.0)
        while asyncio.get_running_loop().time() < deadline:
            if await self.is_foreground(package, timeout_s):
                return
            await asyncio.sleep(0.1)
        raise CompanionTransportError(
            f"companion relay launched {package}, but it is not foreground"
        )

    async def current_app_version(self, package: str) -> str | None:
        if package == "com.volkswagen.weconnect" and self._broker.vw_version:
            return self._broker.vw_version
        result = self._require_ok(
            "version",
            await self._broker.command("version", package=package),
        )
        return str(result.get("value") or "") or None

    async def tap(self, x: int, y: int, timeout_s: float = 10.0) -> None:
        self._require_ok(
            "tap",
            await self._broker.command(
                "tap", x=int(x), y=int(y), timeout_s=timeout_s
            ),
        )
        await asyncio.sleep(0.05)

    async def wake(self, timeout_s: float = 10.0) -> None:
        self._require_ok(
            "wake", await self._broker.command("wake", timeout_s=timeout_s)
        )

    async def sleep_if_enabled(self, timeout_s: float = 10.0) -> None:
        if not self._wake_sleep:
            return
        try:
            self._require_ok(
                "sleep", await self._broker.command("sleep", timeout_s=timeout_s)
            )
        except CompanionTransportError:
            pass

    async def key_back(self, timeout_s: float = 10.0) -> None:
        self._require_ok(
            "back", await self._broker.command("back", timeout_s=timeout_s)
        )
        # Multi-level climate reads issue up to three BACK actions. Give Android
        # time to commit each Activity/Compose transition so they cannot bunch
        # together and overshoot from Volkswagen into the previous system app.
        await asyncio.sleep(0.25)

    async def is_foreground(self, package: str, timeout_s: float = 10.0) -> bool:
        result = self._require_ok(
            "foreground",
            await self._broker.command(
                "foreground", package=package, timeout_s=timeout_s
            ),
        )
        return bool(result.get("value"))

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        dur_ms: int = 300,
        timeout_s: float = 10.0,
    ) -> None:
        self._require_ok(
            "swipe",
            await self._broker.command(
                "swipe",
                x1=int(x1),
                y1=int(y1),
                x2=int(x2),
                y2=int(y2),
                duration=int(dur_ms),
                timeout_s=timeout_s,
            ),
        )
        await asyncio.sleep(0.1)
