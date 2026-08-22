# Copyright 2026 Nikolaj Pognerebko — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct Android companion-agent transport — no runtime ADB fallback."""
from __future__ import annotations

import base64
from typing import Any

import pytest

from custom_components.vag_connect.companion.agent_transport import (
    AgentHttpTransport,
    discover_agent_from_addon,
)
from custom_components.vag_connect.companion.transport import CompanionTransportError


class _Resp:
    def __init__(self, status: int, body: str = "", payload: Any = None) -> None:
        self.status = status
        self._body = body
        self._payload = payload

    async def text(self) -> str:
        return self._body

    async def json(self, content_type: Any = None) -> Any:
        del content_type
        return self._payload

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False


class _Session:
    def __init__(
        self,
        responses: list[_Resp] | None = None,
        addon_health: _Resp | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.addon_health = addon_health
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _Resp:
        self.calls.append(("GET", url, kwargs))
        assert self.addon_health is not None
        return self.addon_health

    async def close(self) -> None:
        self.closed = True


_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_XML = '<?xml version="1.0"?><hierarchy><node text="58%"/></hierarchy>'


@pytest.mark.asyncio
async def test_connect_and_snapshot_are_direct_http() -> None:
    encoded = base64.b64encode(_XML.encode()).decode()
    session = _Session([
        _Resp(200, '{"status":"ok","revision":7,"vw_version":"4.3.2"}'),
        _Resp(200, encoded),
    ])
    transport = AgentHttpTransport(
        "192.168.1.42", 8765, token=_TOKEN, session=session
    )

    await transport.connect()
    assert transport.connected
    assert await transport.current_app_version("com.volkswagen.weconnect") == "4.3.2"
    assert await transport.dump_ui() == _XML
    assert [call[0] for call in session.calls] == ["GET", "GET"]
    assert all("adb" not in call[1].lower() for call in session.calls)
    assert session.calls[0][2]["headers"]["X-Token"] == _TOKEN


@pytest.mark.asyncio
async def test_agent_error_is_visible_without_uiautomator_fallback() -> None:
    session = _Session([
        _Resp(200, '{"status":"ok","revision":1,"vw_version":"4.3.2"}'),
        _Resp(409, "no_volkswagen_window"),
    ])
    transport = AgentHttpTransport(
        "192.168.1.42", 8765, token=_TOKEN, session=session
    )
    await transport.connect()

    with pytest.raises(CompanionTransportError, match="no_volkswagen_window"):
        await transport.dump_ui()
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_phone_battery_uses_local_agent_endpoint() -> None:
    session = _Session([
        _Resp(
            200,
            '{"status":"ok","revision":1,"vw_version":"4.3.2",'
            '"phone_battery_level":67}',
        ),
        _Resp(200, "67"),
    ])
    transport = AgentHttpTransport(
        "192.168.1.42", 8765, token=_TOKEN, session=session
    )
    await transport.connect()

    assert await transport.device_battery_level() == 67
    assert session.calls[-1][1].endswith("/battery")


@pytest.mark.asyncio
async def test_actions_use_agent_endpoints() -> None:
    session = _Session([
        _Resp(200, '{"status":"ok","revision":1,"vw_version":"4.3.2"}'),
        _Resp(202, "accepted"),
        _Resp(202, "accepted"),
        _Resp(202, "accepted"),
    ])
    transport = AgentHttpTransport(
        "192.168.1.42", 8765, token=_TOKEN, session=session
    )
    await transport.connect()
    await transport.tap(10, 20)
    await transport.swipe(10, 20, 30, 40, 250)
    await transport.key_back()

    urls = [call[1] for call in session.calls]
    assert any("/tap?" in url for url in urls)
    assert any("/swipe?" in url for url in urls)
    assert any("/back" in url for url in urls)


@pytest.mark.asyncio
async def test_shared_home_assistant_session_is_not_closed() -> None:
    session = _Session([
        _Resp(200, '{"status":"ok","revision":1,"vw_version":"4.3.2"}')
    ])
    transport = AgentHttpTransport(
        "192.168.1.42", 8765, token=_TOKEN, session=session
    )
    await transport.connect()
    await transport.close()
    assert not session.closed


@pytest.mark.asyncio
async def test_discovers_phone_ip_from_existing_addon_once() -> None:
    session = _Session(
        responses=[
            _Resp(200, '{"status":"ok","revision":1,"vw_version":"4.3.2"}')
        ],
        addon_health=_Resp(
            200,
            payload={"connected": True, "serial": "192.168.100.42:38237"},
        ),
    )
    host = await discover_agent_from_addon(
        "home.example.test", 8129, _TOKEN, session=session
    )
    assert host == "192.168.100.42"
    assert session.calls[0][1] == "http://home.example.test:8129/health"
    assert session.calls[1][1] == "http://192.168.100.42:8765/health"
