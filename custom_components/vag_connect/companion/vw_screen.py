# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Volkswagen 4.3.2 screen helpers grounded against a live ID.3.

These functions are pure: navigation lives in :mod:`vw_driver`, while this
module only locates accessibility nodes and turns them into VehicleData fields.
"""
from __future__ import annotations

import re

from .presets import coerce
from .screen import UiNode


def find_by_rid(nodes: list[UiNode], resource_id: str) -> UiNode | None:
    """Return the first visible node whose resource-id basename matches."""
    for node in nodes:
        rid = node.resource_id.rsplit("/", 1)[-1]
        if rid == resource_id and node.bounds is not None and node.enabled:
            return node
    return None


def find_by_desc(nodes: list[UiNode], pattern: str) -> UiNode | None:
    """Return the first visible content-description regex match."""
    rx = re.compile(pattern, re.I)
    return next(
        (
            node
            for node in nodes
            if node.bounds is not None
            and node.enabled
            and node.content_desc
            and rx.search(node.content_desc)
        ),
        None,
    )


def find_by_text(nodes: list[UiNode], pattern: str) -> UiNode | None:
    """Return the first visible text regex match."""
    rx = re.compile(pattern, re.I)
    return next(
        (
            node
            for node in nodes
            if node.bounds is not None
            and node.enabled
            and node.text
            and rx.search(node.text)
        ),
        None,
    )


def row_toggle(nodes: list[UiNode], label_pattern: str) -> UiNode | None:
    """Find the checkable switch sharing a horizontal row with a text label."""
    label = find_by_text(nodes, label_pattern)
    if label is None or label.bounds is None:
        return None
    _, label_top, _, label_bottom = label.bounds
    candidates: list[UiNode] = []
    for node in nodes:
        if not node.checkable or node.bounds is None or not node.enabled:
            continue
        _, top, _, bottom = node.bounds
        if min(bottom, label_bottom) >= max(top, label_top):
            candidates.append(node)
    if not candidates:
        return None
    # Compose emits several duplicate checkable nodes. Prefer the widest row
    # target so taps work even when the small visual switch moves slightly.
    return max(candidates, key=lambda node: node.bounds[2] - node.bounds[0])  # type: ignore[index]


def parse_climate(nodes: list[UiNode]) -> dict[str, object]:
    """Read target/outside temperature and live window-heating state."""
    out: dict[str, object] = {}
    container = find_by_rid(nodes, "clima_compose_view")
    if container is not None and container.bounds is not None:
        left, top, right, bottom = container.bounds
        centre_x = (left + right) / 2
        candidates: list[tuple[float, float]] = []
        for node in nodes:
            if not node.text or node.bounds is None:
                continue
            match = re.fullmatch(r"(\d{1,2}(?:[.,]\d)?)", node.text.strip())
            nleft, ntop, nright, nbottom = node.bounds
            if (
                match
                and left <= nleft <= nright <= right
                and top <= ntop <= nbottom <= bottom
            ):
                value = float(match.group(1).replace(",", "."))
                node_x = (nleft + nright) / 2
                candidates.append((abs(node_x - centre_x), value))
        if candidates:
            out["target_temperature"] = min(candidates)[1]

    for node in nodes:
        if not node.text:
            continue
        match = re.search(r"(-?\d{1,2}(?:[.,]\d+)?)\s*°C\b", node.text)
        if match:
            out["outside_temp"] = float(match.group(1).replace(",", "."))
            break

    window = find_by_rid(nodes, "window_heating_description")
    if window is not None:
        raw = window.text.strip().casefold()
        if raw in {"on", "active", "ein", "aktiv"}:
            out["window_heating_front"] = True
            out["window_heating_back"] = True
        elif raw in {"off", "inactive", "aus", "inaktiv"}:
            out["window_heating_front"] = False
            out["window_heating_back"] = False
    return out


def parse_climate_settings(nodes: list[UiNode]) -> dict[str, object]:
    """Read persistent auxiliary-conditioning and automatic-window settings."""
    out: dict[str, object] = {}
    unlock = find_by_rid(nodes, "ClimatisationAtUnlockEnabled")
    if unlock is not None and unlock.checkable:
        out["climate_at_unlock"] = unlock.checked
        out["climatisation_at_unlock"] = unlock.checked
    window = find_by_rid(nodes, "WindowHeatingEnabled")
    if window is not None and window.checkable:
        out["window_heating_enabled"] = window.checked
    return out


def parse_zones(nodes: list[UiNode]) -> dict[str, object]:
    """Read the two extended-conditioning zone switches available on ID.3."""
    out: dict[str, object] = {}
    front_left = row_toggle(nodes, r"^Front left$")
    if front_left is not None:
        out["climate_zone_front_left_enabled"] = front_left.checked
    front_right = row_toggle(nodes, r"^Front right$")
    if front_right is not None:
        out["climate_zone_front_right_enabled"] = front_right.checked
    return out


def parse_vehicle_settings(nodes: list[UiNode]) -> dict[str, object]:
    """Read charge limit and the three charging preference toggles."""
    out: dict[str, object] = {}
    value = find_by_rid(nodes, "value")
    if value is not None:
        target = coerce("percent", value.text)
        if target is not None:
            out["target_soc"] = target

    battery_care = row_toggle(nodes, r"^Battery Care Mode$")
    if battery_care is not None:
        out["battery_care_enabled"] = battery_care.checked

    reduced = row_toggle(nodes, r"^Reduced AC charging current$")
    if reduced is not None:
        out["max_charging_current"] = "REDUCED" if reduced.checked else "MAXIMUM"

    release = row_toggle(nodes, r"^Automatically release AC connector$")
    if release is not None:
        out["auto_unlock_when_charged"] = release.checked
    return out


def _next_text(nodes: list[UiNode], label_pattern: str) -> str | None:
    rx = re.compile(label_pattern, re.I)
    for index, node in enumerate(nodes):
        if not node.text or not rx.search(node.text):
            continue
        for sibling in nodes[index + 1 : index + 4]:
            if sibling.text and sibling.text != node.text:
                return sibling.text
    return None


def parse_vehicle_health(nodes: list[UiNode]) -> dict[str, object]:
    """Read total distance and next-service countdown."""
    out: dict[str, object] = {}
    total = _next_text(nodes, r"^Total distance$")
    if total:
        value = coerce("int_km", re.sub(r"(?<=\d),(?=\d{3}\b)", "", total))
        if value is not None:
            out["odometer_km"] = value
    service = _next_text(nodes, r"^Next service$")
    if service:
        match = re.search(r"(\d+)", service)
        if match:
            out["service_due_in_days"] = int(match.group(1))
    return out


_MAP_URL_RE = re.compile(
    r"https?://(?:www\.)?google\.[^/\s]+/maps/place/"
    r"(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)"
)


def parse_shared_location(nodes: list[UiNode]) -> dict[str, object]:
    """Extract coordinates from the Android share-sheet map URL."""
    for node in nodes:
        raw = node.text or node.content_desc
        match = _MAP_URL_RE.search(raw)
        if match:
            return {
                "latitude": float(match.group(1)),
                "longitude": float(match.group(2)),
            }
    return {}


_OPENING_TOKENS: dict[str, str] = {
    "frontLeft": "Front left-side",
    "frontRight": "Front right-side",
    "rearLeft": "Rear left-side",
    "rearRight": "Rear right-side",
}


def parse_overview_charging(nodes: list[UiNode]) -> dict[str, object]:
    """Read SoC and the three grounded charging states from the overview.

    We Connect 4.3.2 exposes active charging in the range tile description,
    while maintenance charging is present only in the visible SoC line as
    ``Keep charge level • 61%``. A stable overview with neither marker is the
    third state: not charging. Requiring both overview anchors prevents a
    loading/detail tree from being mistaken for that negative state.
    """
    if find_by_rid(nodes, "rangeTile") is None or find_by_rid(
        nodes, "climateTile"
    ) is None:
        return {}
    range_overview = find_by_desc(nodes, r"^Range overview\.")
    if range_overview is None:
        return {}

    out: dict[str, object] = {}
    soc_line = next(
        (
            node.text
            for node in nodes
            if node.text
            and re.search(
                r"(?:Keep charge level\s*[\u2022·]\s*)?\d{1,3}\s*%",
                node.text,
                re.I,
            )
        ),
        "",
    )
    soc_match = re.search(r"(\d{1,3})\s*%", soc_line)
    if soc_match:
        soc = int(soc_match.group(1))
        if 0 <= soc <= 100:
            out["battery_soc"] = soc

    description = range_overview.content_desc.casefold()
    status_text = soc_line.casefold()
    if "currently charging" in description:
        state = "CHARGING"
    elif (
        "conservation charging" in status_text
        or "keep charge level" in status_text
    ):
        state = "CONSERVATION_CHARGING"
    else:
        state = "NOT_CHARGING"
    out["charging_state"] = state
    out["is_charging"] = state in {"CHARGING", "CONSERVATION_CHARGING"}
    return out


def parse_overview_openings(nodes: list[UiNode]) -> dict[str, object]:
    """Read per-door/window/boot state from the hidden vehicle-image semantics.

    We Connect lists only OPEN elements in one content-description. Absence is
    therefore a grounded closed state, but only after finding the dedicated
    image node whose description begins ``Vehicle is ...``; the similarly
    worded ``Your vehicle: ...`` header is not sufficient evidence.
    """
    status = next(
        (
            node.content_desc
            for node in nodes
            if node.content_desc.startswith("Vehicle is ")
        ),
        "",
    )
    if not status:
        return {}
    lowered = status.casefold()
    doors = {
        key: f"{label} door".casefold() in lowered
        for key, label in _OPENING_TOKENS.items()
    }
    # Existing model convention is True == CLOSED for windows; the entity
    # layer inverts it so BinarySensor.is_on means open.
    windows = {
        key: f"{label} window".casefold() not in lowered
        for key, label in _OPENING_TOKENS.items()
    }
    lights = {
        key: f"{label} light".casefold() in lowered
        for key, label in _OPENING_TOKENS.items()
    }
    trunk_open = "boot" in lowered or "trunk" in lowered
    hood_open = "bonnet" in lowered or "hood" in lowered
    return {
        "doors_individual": doors,
        "windows_individual": windows,
        "doors_open": any(doors.values()),
        "windows_open": any(not closed for closed in windows.values()),
        "trunk_open": trunk_open,
        "hood_open": hood_open,
        "lights_individual": lights,
        "lights_on": any(lights.values()),
        "lights_count": sum(lights.values()),
    }
