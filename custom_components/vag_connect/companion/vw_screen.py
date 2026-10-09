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


def _unique_visible(candidates: list[UiNode]) -> UiNode | None:
    """Duplicate semantic wrappers at the same location are fine; two targets aren't."""
    matches = [n for n in candidates if n.enabled and n.visible_bounds is not None]
    if not matches or len({n.visible_bounds for n in matches}) != 1:
        return None
    return matches[0]


def find_by_rid(nodes: list[UiNode], resource_id: str) -> UiNode | None:
    """Return the first visible node whose resource-id basename matches."""
    return _unique_visible([
        node for node in nodes if node.resource_id.rsplit("/", 1)[-1] == resource_id
    ])


def find_by_desc(nodes: list[UiNode], pattern: str) -> UiNode | None:
    """Return the first visible content-description regex match."""
    rx = re.compile(pattern, re.I)
    return _unique_visible([n for n in nodes if n.content_desc and rx.search(n.content_desc)])


def find_by_text(nodes: list[UiNode], pattern: str) -> UiNode | None:
    """Return the first visible text regex match."""
    rx = re.compile(pattern, re.I)
    return _unique_visible([n for n in nodes if n.text and rx.search(n.text)])


def zones_navigation_node(nodes: list[UiNode]) -> UiNode | None:
    """An ID-less Zones back button is a sibling of the Zones toolbar title.

    No absolute navigation slot: identify the page by its title and zone labels,
    then require exactly one unlabelled non-toggle Button beside that title.
    """
    title = find_by_desc(nodes, r"^Zones$")
    if (title is None or title.parent_index is None or title.bounds is None
            or not any(n.text in {"Front left", "Front right"} for n in nodes)):
        return None
    left, top, right, bottom = title.bounds
    return _unique_visible([
        n for n in nodes
        if n.parent_index == title.parent_index
        and n.clazz == "android.widget.Button" and n.clickable and not n.checkable
        and not (n.text or n.content_desc or n.resource_id)
        and n.bounds is not None and n.visible_bounds == n.bounds
        and top <= (n.bounds[1] + n.bounds[3]) / 2 <= bottom
        and (n.bounds[0] + n.bounds[2]) / 2 < left
    ])


def vertical_scroll_gesture(nodes: list[UiNode]) -> tuple[int, int, int, int] | None:
    """Scroll inside one visible vertical list; refuse nested/ambiguous lists."""
    containers = [n for n in nodes if n.scrollable and n.enabled
                  and n.clazz.endswith(("ScrollView", "RecyclerView"))
                  and n.visible_bounds is not None]
    container = _unique_visible(containers)
    if container is None or container.visible_bounds is None:
        return None
    left, top, right, bottom = container.visible_bounds
    x = (left + right) // 2
    start, end = round(top + (bottom - top) * 0.8), round(top + (bottom - top) * 0.25)
    # Do not begin a vertical scroll on an interactive slider/toggle.
    for n in nodes:
        if n.visible_bounds is None or not (n.checkable or n.clazz.endswith("SeekBar")):
            continue
        nl, nt, nr, nb = n.visible_bounds
        if nl <= x <= nr and nt <= start <= nb:
            return None
    return (x, start, x, end) if start > end else None


def overview_vehicle_name(nodes: list[UiNode]) -> str | None:
    """Read the selected car name, including dots in models such as ID.3."""
    header = find_by_desc(nodes, r"^Your vehicle:")
    if header is None:
        return None
    match = re.match(r"^Your vehicle:\s*(.+?)\.\s+Vehicle is\b", header.content_desc)
    return " ".join(match.group(1).split()) if match else None


def centered_vehicle_marker_point(nodes: list[UiNode]) -> tuple[int, int] | None:
    """After Find vehicle, try the centre of the map above its collapsed sheet.

    The marker has no semantics. This is a geometric candidate, not evidence
    that a vehicle is there: the driver MUST validate the resulting card before
    sharing its coordinates. No Pixel resolution or fixed percentage is used.
    """
    map_node = find_by_desc(nodes, r"^Google Map$")
    find_vehicle = find_by_desc(nodes, r"^Find vehicle$")
    sheet = find_by_desc(nodes, r"^Bottom sheet collapsed$")
    if (map_node is None or map_node.bounds is None or find_vehicle is None
            or sheet is None or sheet.bounds is None
            or map_node.visible_bounds != map_node.bounds
            or sheet.visible_bounds != sheet.bounds
            or find_by_desc(nodes, r"^Close details view$") is not None):
        return None
    left, top, right, bottom = map_node.bounds
    sl, st, sr, sb = sheet.bounds
    if not (sl <= left < right <= sr and top < st < sb <= bottom):
        return None  # side panel, expanded sheet or mismatched coordinate space
    x, y = (left + right) // 2, (top + st) // 2
    map_index = next(i for i, node in enumerate(nodes) if node is map_node)
    marker_bounds: set[tuple[int, int, int, int]] = set()
    for n in nodes:
        if n is map_node or not n.enabled or not n.clickable or n.visible_bounds is None:
            continue
        nl, nt, nr, nb = n.visible_bounds
        covers_map = nl <= left and nt <= top and nr >= right and nb >= bottom
        if not covers_map and nl <= x <= nr and nt <= y <= nb:
            # Google Maps sometimes exposes the marker itself as an unlabelled
            # clickable View. It is a small direct map child, not a sibling UI
            # overlay. Allow one such candidate under the centre; its identity
            # is still untrusted until the driver verifies the opened card.
            if (n.parent_index == map_index and n.clazz == "android.view.View"
                    and not (n.text or n.content_desc or n.resource_id or n.checkable or n.scrollable)
                    and n.visible_bounds == n.bounds
                    and n.package in {"", "com.volkswagen.weconnect"}
                    and left <= nl < nr <= right and top <= nt < nb <= st
                    and nr - nl <= (right - left) / 4 and nb - nt <= (st - top) / 4):
                marker_bounds.add(n.visible_bounds)
                if len(marker_bounds) == 1:
                    continue
            return None  # an interactive overlay covers the candidate
    return x, y


def vehicle_map_share(nodes: list[UiNode], expected_name: str) -> UiNode | None:
    """Accept Share only inside this car's parking card, never a POI card.

    The captured VW card has name, Close details view and Parked since under
    the same parent, with Share in a nested action row. Restrict all evidence
    to that subtree so a matching label elsewhere on the map is insufficient.
    """
    names = [n for n in nodes if " ".join(n.text.split()) == expected_name]
    title = _unique_visible(names)
    if title is None or title.parent_index is None:
        return None
    card_index = title.parent_index
    card = nodes[card_index]
    map_node = find_by_desc(nodes, r"^Google Map$")
    sheet = find_by_desc(nodes, r"^Bottom sheet (?:collapsed|expanded)$")
    if (map_node is None or map_node.bounds is None or card.visible_bounds is None
            or sheet is None or sheet.bounds is None
            or card.visible_bounds[1] < sheet.bounds[1]):
        return None
    subtree = []
    for n in nodes:
        parent = n.parent_index
        while parent is not None and parent != card_index:
            parent = nodes[parent].parent_index
        if parent == card_index:
            subtree.append(n)
    if (find_by_desc(subtree, r"^Close details view$") is None
            or find_by_text(subtree, r"\bParked since\b") is None):
        return None
    share = find_by_text(subtree, r"^Share$")
    return share if share is not None and share.visible_bounds == share.bounds else None


def row_toggle(nodes: list[UiNode], label_pattern: str) -> UiNode | None:
    """Find the checkable switch sharing a horizontal row with a text label."""
    label = find_by_text(nodes, label_pattern)
    if label is None or label.bounds is None:
        return None
    _, label_top, _, label_bottom = label.bounds
    candidates: list[UiNode] = []
    for node in nodes:
        if not node.checkable or node.visible_bounds is None or not node.enabled:
            continue
        assert node.bounds is not None
        _, top, _, bottom = node.bounds
        if min(bottom, label_bottom) >= max(top, label_top):
            candidates.append(node)
    if not candidates:
        return None
    # Compose emits several duplicate checkable nodes. Prefer the widest row
    # target so taps work even when the small visual switch moves slightly.
    widest = max(candidates, key=lambda node: node.bounds[2] - node.bounds[0])  # type: ignore[index]
    assert widest.bounds is not None
    left, top, right, bottom = widest.bounds
    tolerance = max(1, (bottom - top) * 0.02)  # Compose nested switch rounding
    for n in candidates:
        assert n.bounds is not None
        nl, nt, nr, nb = n.bounds
        if n.checked != widest.checked or not (
            left - tolerance <= nl < nr <= right + tolerance
            and top - tolerance <= nt < nb <= bottom + tolerance
        ):
            return None
    return widest


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


def charge_limit_gesture(
    nodes: list[UiNode], target: int
) -> tuple[int, int, int, int] | None:
    """Locate the actual SeekBar and its current thumb, never the wider title.

    The VW slider's child is an accessibility touch target around the thumb
    (``Value, 60``), not the entire track. Inset the slider bounds by half that
    target width, then drag from the current thumb centre to the desired tick.
    Both geometry and the current percentage must agree before any gesture.
    """
    if not 50 <= target <= 100 or target % 10:
        return None
    slider = find_by_rid(nodes, "slider")
    current = parse_vehicle_settings(nodes).get("target_soc")
    if (
        slider is None or slider.bounds is None
        or slider.visible_bounds != slider.bounds
        or slider.clazz != "android.widget.SeekBar"
        or not isinstance(current, int) or not 50 <= current <= 100
    ):
        return None
    left, top, right, bottom = slider.bounds
    thumbs = []
    for node in nodes:
        if not node.enabled or node.bounds is None or node.clazz != slider.clazz:
            continue
        match = re.fullmatch(r"Value,\s*(\d+)", node.content_desc)
        if match is None or int(match.group(1)) != current:
            continue
        x1, y1, x2, y2 = node.bounds
        # At 50/100% the thumb's accessibility touch target may overhang
        # the track. Its centre must still belong to this slider.
        if x1 < x2 and left <= (x1 + x2) / 2 <= right and top <= y1 < y2 <= bottom:
            thumbs.append(node)
    if len(thumbs) != 1:
        return None
    thumb = thumbs[0]
    assert thumb.bounds is not None and thumb.tap_point is not None
    inset = (thumb.bounds[2] - thumb.bounds[0]) / 2
    start, end = left + inset, right - inset
    if end <= start:
        return None
    thumb_x, thumb_y = thumb.tap_point
    expected_current = start + (end - start) * ((current - 50) / 50)
    if abs(thumb_x - expected_current) > (end - start) / 10:
        return None
    return thumb_x, thumb_y, round(start + (end - start) * ((target - 50) / 50)), thumb_y


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

    We Connect lists only OPEN elements in one ImageView content-description.
    When everything is closed/off that ImageView disappears completely. Treat
    its absence as the grounded negative state only on a complete overview
    (both tiles plus the ``Your vehicle:`` header); a loading/detail tree still
    returns no fields and therefore cannot erase last-known-good state.
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
        complete_overview = (
            find_by_rid(nodes, "rangeTile") is not None
            and find_by_rid(nodes, "climateTile") is not None
            and find_by_desc(nodes, r"^Your vehicle:") is not None
        )
        if not complete_overview:
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
