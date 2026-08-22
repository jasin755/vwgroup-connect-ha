# Copyright 2026 Nikolaj Pognerebko — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct transport for the VAG Companion Android AccessibilityService.

The agent runs the official Volkswagen app on a genuine Android device, but
Home Assistant talks to the agent's authenticated LAN API rather than to ADB.
ADB is therefore only an installation/update mechanism; normal reads and
writes continue when Android Wireless Debugging is disabled.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, cast
from urllib.parse import urlencode

from .transport import CompanionTransportError, NetworkAdbTransport

_LOGGER = logging.getLogger(__name__)

DEFAULT_AGENT_PORT = 8765


class AgentHttpTransport(NetworkAdbTransport):
    """Drive the phone through the companion agent's authenticated HTTP API."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_AGENT_PORT,
        *,
        token: str,
        session: object | None = None,
        wake_sleep: bool = False,
    ) -> None:
        super().__init__(host, port or DEFAULT_AGENT_PORT, "", wake_sleep=wake_sleep)
        self._token = token
        self._session = session
        self._owns_session = False
        self._vw_version: str | None = None

    @property
    def _base(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def _get_session(self) -> object:
        if self._session is None:
            from aiohttp import ClientSession  # noqa: PLC0415

            self._session = ClientSession()
            self._owns_session = True
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        timeout_s: float = 10.0,
        accepted: tuple[int, ...] = (200,),
    ) -> str:
        from aiohttp import ClientError, ClientTimeout  # noqa: PLC0415

        session = await self._get_session()
        url = f"{self._base}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        try:
            async with session.request(  # type: ignore[attr-defined]
                method,
                url,
                headers={"X-Token": self._token},
                timeout=ClientTimeout(total=timeout_s),
            ) as response:
                body = cast(str, await response.text())
                if response.status == 403:
                    raise CompanionTransportError(
                        "the companion agent rejected its token; reinstall or "
                        "re-provision the agent with the token configured in HA"
                    )
                if response.status not in accepted:
                    raise CompanionTransportError(
                        f"companion agent {path} returned HTTP {response.status}: "
                        f"{body[:160] or 'empty response'}"
                    )
                return body
        except CompanionTransportError:
            raise
        except (TimeoutError, ClientError, OSError) as err:
            self._device = None
            raise CompanionTransportError(
                f"could not reach the companion agent at {self._base} "
                f"({type(err).__name__}); check the phone is on Wi-Fi and the "
                "VAG Companion Agent accessibility service is enabled"
            ) from err

    async def connect(self, timeout_s: float = 10.0) -> None:
        import json  # noqa: PLC0415

        body = await self._request("GET", "/health", timeout_s=timeout_s)
        try:
            health = json.loads(body)
        except (TypeError, ValueError) as err:
            raise CompanionTransportError(
                "the companion agent returned invalid health data"
            ) from err
        if not isinstance(health, dict) or health.get("status") != "ok":
            raise CompanionTransportError("the companion agent is not ready")
        self._vw_version = str(health.get("vw_version") or "") or None
        self._device = True

    async def close(self) -> None:
        self._device = None
        if self._owns_session and self._session is not None:
            try:
                await self._session.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            self._session = None
            self._owns_session = False

    @property
    def connected(self) -> bool:
        return self._device is not None

    async def shell(self, cmd: str, timeout_s: float = 10.0) -> str:
        del cmd, timeout_s
        raise CompanionTransportError(
            "the direct companion agent transport does not expose an ADB shell"
        )

    async def dump_ui(self, timeout_s: float = 15.0) -> str:
        encoded = (await self._request(
            "GET", "/snapshot", timeout_s=timeout_s
        )).strip()
        try:
            xml = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as err:
            raise CompanionTransportError(
                "the companion agent returned an invalid accessibility snapshot"
            ) from err
        if "<hierarchy" not in xml:
            raise CompanionTransportError(
                "the companion agent returned no Volkswagen accessibility tree"
            )
        return xml

    async def foreground_app(self, package: str, timeout_s: float = 10.0) -> None:
        await self.wake(timeout_s)
        if await self.is_foreground(package, timeout_s):
            return
        await self._request(
            "POST",
            "/launch",
            params={"package": package},
            timeout_s=timeout_s,
            accepted=(200, 202),
        )
        deadline = asyncio.get_running_loop().time() + min(timeout_s, 3.0)
        while asyncio.get_running_loop().time() < deadline:
            if await self.is_foreground(package, timeout_s):
                return
            await asyncio.sleep(0.1)
        raise CompanionTransportError(
            f"companion agent launched {package}, but it did not reach the foreground"
        )

    async def current_app_version(self, package: str) -> str | None:
        if package == "com.volkswagen.weconnect" and self._vw_version:
            return self._vw_version
        try:
            version = await self._request(
                "GET", "/version", params={"package": package}
            )
        except CompanionTransportError:
            return None
        return version.strip() or None

    async def tap(self, x: int, y: int, timeout_s: float = 10.0) -> None:
        await self._request(
            "POST",
            "/tap",
            params={"x": int(x), "y": int(y)},
            timeout_s=timeout_s,
            accepted=(200, 202),
        )
        await asyncio.sleep(0.05)

    async def wake(self, timeout_s: float = 10.0) -> None:
        await self._request(
            "POST", "/wake", timeout_s=timeout_s, accepted=(200, 202)
        )

    async def sleep_if_enabled(self, timeout_s: float = 10.0) -> None:
        if not self._wake_sleep:
            return
        try:
            await self._request(
                "POST", "/sleep", timeout_s=timeout_s, accepted=(200, 202)
            )
        except CompanionTransportError:
            pass

    async def key_back(self, timeout_s: float = 10.0) -> None:
        await self._request(
            "POST", "/back", timeout_s=timeout_s, accepted=(200, 202)
        )
        await asyncio.sleep(0.05)

    async def is_foreground(self, package: str, timeout_s: float = 10.0) -> bool:
        body = await self._request(
            "GET",
            "/foreground",
            params={"package": package},
            timeout_s=timeout_s,
        )
        return body.strip().lower() == "true"

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        dur_ms: int = 300,
        timeout_s: float = 10.0,
    ) -> None:
        await self._request(
            "POST",
            "/swipe",
            params={
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "duration": int(dur_ms),
            },
            timeout_s=timeout_s,
            accepted=(200, 202),
        )
        await asyncio.sleep(0.1)


async def probe_agent(
    host: str,
    port: int,
    token: str,
    *,
    session: object | None = None,
) -> tuple[bool, str]:
    """Validate that an agent is reachable and can see the VW package."""
    transport = AgentHttpTransport(host, port, token=token, session=session)
    try:
        await transport.connect()
        version = await transport.current_app_version("com.volkswagen.weconnect")
        if not version:
            return False, "companion_app_not_found"
    except CompanionTransportError as err:
        _LOGGER.debug("Companion agent probe failed: %s", err)
        return False, "companion_cannot_connect"
    finally:
        await transport.close()
    return True, ""


async def discover_agent_from_addon(
    addon_host: str,
    addon_port: int,
    token: str,
    *,
    session: object,
) -> str | None:
    """One-time migration: obtain the phone IP from a connected ADB add-on."""
    from aiohttp import ClientError, ClientTimeout  # noqa: PLC0415

    try:
        async with session.get(  # type: ignore[attr-defined]
            f"http://{addon_host}:{addon_port}/health",
            headers={"X-Token": token} if token else {},
            timeout=ClientTimeout(total=5),
        ) as response:
            if response.status >= 400:
                return None
            payload: Any = await response.json(content_type=None)
    except (TimeoutError, ClientError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    serial = str(payload.get("serial") or "")
    if not serial:
        return None
    if serial.startswith("[") and "]:" in serial:
        phone_host = serial[1:serial.rfind("]:")]
    elif ":" in serial:
        phone_host = serial.rsplit(":", 1)[0]
    else:
        phone_host = serial
    ok, _reason = await probe_agent(
        phone_host,
        DEFAULT_AGENT_PORT,
        token,
        session=session,
    )
    return phone_host if ok else None
