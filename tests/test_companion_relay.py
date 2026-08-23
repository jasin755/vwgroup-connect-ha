# Copyright 2026 Nikolaj Pognerebko — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound Android relay command/result rendezvous."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import json
from types import SimpleNamespace

import pytest

from custom_components.vag_connect.companion.relay import (
    AgentRelayTransport,
    CompanionAgentTokenRelayView,
    CompanionRelayBroker,
    _REGISTRY_KEY,
    _broker_by_token,
)
from custom_components.vag_connect.companion.transport import CompanionTransportError


_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_XML = '<?xml version="1.0"?><hierarchy><node text="60%"/></hierarchy>'


def test_token_discovery_requires_one_unique_matching_broker() -> None:
    first = CompanionRelayBroker("first", _TOKEN)
    assert _broker_by_token({"first": first}, _TOKEN) is first
    assert _broker_by_token({"first": first}, "wrong") is None
    assert _broker_by_token({}, _TOKEN) is None

    duplicate = CompanionRelayBroker("duplicate", _TOKEN)
    assert _broker_by_token(
        {"first": first, "duplicate": duplicate}, _TOKEN
    ) is None


class _RelayRequest:
    def __init__(self, hass: object, token: str, payload: object) -> None:
        self.app = {"hass": hass}
        self.headers = {"X-Token": token}
        self._payload = payload

    async def json(self) -> object:
        return self._payload


@pytest.mark.asyncio
async def test_token_discovery_view_routes_valid_agent_poll() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    hass = SimpleNamespace(data={_REGISTRY_KEY: {"entry": broker}})
    request = _RelayRequest(
        hass,
        _TOKEN,
        {"agent_version": "0.6.0", "event_only": True},
    )

    response = await CompanionAgentTokenRelayView().post(request)

    assert response.status == 200
    assert json.loads(response.text) == {"command": None}
    assert broker.agent_version == "0.6.0"


@pytest.mark.asyncio
async def test_token_discovery_view_rejects_unknown_token() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    hass = SimpleNamespace(data={_REGISTRY_KEY: {"entry": broker}})

    response = await CompanionAgentTokenRelayView().post(
        _RelayRequest(hass, "wrong", {})
    )

    assert response.status == 404


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_broker_round_trips_one_command_and_result() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    first_poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.2.0",
        "vw_version": "4.3.2",
    }))
    await asyncio.sleep(0)
    result_task = asyncio.create_task(broker.command("wake"))
    command = await first_poll
    assert command is not None
    assert command["action"] == "wake"

    second_poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.2.0",
        "vw_version": "4.3.2",
        "result": {"id": command["id"], "status": "accepted"},
    }))
    result = await result_task
    assert result["status"] == "accepted"
    assert broker.online
    assert broker.vw_version == "4.3.2"
    await _cancel(second_poll)


@pytest.mark.asyncio
async def test_transport_snapshot_has_no_adb_fallback() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    online_poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.2.0",
        "vw_version": "4.3.2",
    }))
    await asyncio.sleep(0)
    transport = AgentRelayTransport(broker)
    await transport.connect()

    snapshot_task = asyncio.create_task(transport.dump_ui())
    command = await online_poll
    assert command is not None and command["action"] == "snapshot"
    finish_poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.2.0",
        "vw_version": "4.3.2",
        "result": {
            "id": command["id"],
            "status": "ok",
            "xml_b64": base64.b64encode(_XML.encode()).decode(),
        },
    }))
    assert await snapshot_task == _XML
    with pytest.raises(CompanionTransportError, match="does not expose ADB"):
        await transport.shell("uiautomator dump")
    await _cancel(finish_poll)


@pytest.mark.asyncio
async def test_failed_agent_action_is_not_hidden() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    online_poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.2.0",
        "vw_version": "4.3.2",
    }))
    await asyncio.sleep(0)
    transport = AgentRelayTransport(broker)
    await transport.connect()

    tap_task = asyncio.create_task(transport.tap(10, 20))
    command = await online_poll
    assert command is not None and command["action"] == "tap"
    finish_poll = asyncio.create_task(broker.handle_poll({
        "result": {
            "id": command["id"],
            "status": "error",
            "error": "gesture rejected",
        },
    }))
    with pytest.raises(CompanionTransportError, match="gesture rejected"):
        await tap_task
    await _cancel(finish_poll)


@pytest.mark.asyncio
async def test_event_only_snapshot_calls_handler_without_long_polling() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    received: list[tuple[str, int]] = []

    async def handler(xml: str, revision: int) -> None:
        received.append((xml, revision))

    broker.event_handler = handler
    command = await broker.handle_poll({
        "agent_version": "0.4.0",
        "vw_version": "4.3.2",
        "event_only": True,
        "revision": 42,
        "event_snapshot_b64": base64.b64encode(_XML.encode()).decode(),
    })
    assert command is None
    assert received == [(_XML, 42)]


@pytest.mark.asyncio
async def test_phone_battery_heartbeat_updates_handler_and_transport() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    received: list[int] = []

    async def handler(level: int) -> None:
        received.append(level)

    broker.phone_battery_handler = handler
    poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.5.0",
        "vw_version": "4.3.2",
        "phone_battery_level": 67,
    }))
    await asyncio.sleep(0)

    transport = AgentRelayTransport(broker)
    assert received == [67]
    assert await transport.device_battery_level() == 67
    await _cancel(poll)


@pytest.mark.asyncio
async def test_invalid_phone_battery_heartbeat_is_ignored() -> None:
    broker = CompanionRelayBroker("entry", _TOKEN)
    poll = asyncio.create_task(broker.handle_poll({
        "agent_version": "0.5.0",
        "phone_battery_level": 101,
    }))
    await asyncio.sleep(0)

    assert broker.phone_battery_level is None
    await _cancel(poll)
