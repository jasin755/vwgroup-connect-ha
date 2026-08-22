# Copyright 2026 Nikolaj Pognerebko — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound Android relay command/result rendezvous."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress

import pytest

from custom_components.vag_connect.companion.relay import (
    AgentRelayTransport,
    CompanionRelayBroker,
)
from custom_components.vag_connect.companion.transport import CompanionTransportError


_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_XML = '<?xml version="1.0"?><hierarchy><node text="60%"/></hierarchy>'


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
