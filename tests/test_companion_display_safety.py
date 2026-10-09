"""Display portability and fail-closed navigation, without sending car commands."""
from __future__ import annotations

from unittest.mock import AsyncMock
from xml.sax.saxutils import quoteattr

import pytest

from custom_components.vag_connect.companion.screen import parse_ui_dump
from custom_components.vag_connect.companion.transport import CompanionTransportError
from custom_components.vag_connect.companion.vw_driver import VolkswagenAppDriver
from custom_components.vag_connect.companion.vw_screen import (
    charge_limit_gesture,
    find_by_desc,
    find_by_rid,
    find_by_text,
    centered_vehicle_marker_point,
    overview_vehicle_name,
    row_toggle,
    vertical_scroll_gesture,
    vehicle_map_share,
)


def node(children: str = "", **attrs: str) -> str:
    defaults = {"class": "android.view.View", "enabled": "true", **attrs}
    return "<node " + " ".join(f"{key}={quoteattr(value)}" for key, value in defaults.items()) + ">" + children + "</node>"


def window(children: str, width: int = 1080, height: int = 2340) -> str:
    return "<hierarchy>" + node(children, bounds=f"[0,0][{width},{height}]") + "</hierarchy>"


def test_zero_offscreen_disabled_and_hidden_controls_are_not_targets() -> None:
    nodes = parse_ui_dump(window(
        node(**{"resource-id": "zero", "bounds": "[50,50][50,50]"})
        + node(**{"resource-id": "outside", "bounds": "[0,2400][300,2500]"})
        + node(node(**{"resource-id": "disabled", "bounds": "[50,100][150,150]"}), enabled="false")
        + node(**{"resource-id": "hidden", "bounds": "[50,100][150,150]", "visible-to-user": "false"})
    ))
    for rid in ("zero", "outside", "disabled", "hidden"):
        assert find_by_rid(nodes, rid) is None
        assert next(n for n in nodes if n.resource_id == rid).tap_point is None


def test_scroll_viewport_clips_children_and_handles_negative_bounds() -> None:
    nodes = parse_ui_dump(window(node(
        node(**{"resource-id": "above", "bounds": "[10,-150][200,-10]"})
        + node(**{"resource-id": "partial", "bounds": "[10,180][200,260]"})
        + node(**{"resource-id": "below", "bounds": "[10,600][200,650]"}),
        **{"class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,200][1080,500]"},
    )))
    assert find_by_rid(nodes, "above") is None
    assert find_by_rid(nodes, "below") is None
    partial = find_by_rid(nodes, "partial")
    assert partial is not None and partial.visible_bounds == (10, 200, 200, 260)
    assert not VolkswagenAppDriver._fully_visible(partial)


@pytest.mark.parametrize("scale", [2 / 3, 1, 4 / 3])
def test_zones_back_follows_toolbar_structure_across_displays(scale: float) -> None:
    def bounds(left: int, top: int, right: int, bottom: int) -> str:
        return f"[{round(left * scale)},{round(top * scale)}][{round(right * scale)},{round(bottom * scale)}]"

    nodes = parse_ui_dump(window(
        node(
            node(**{"class": "android.widget.Button", "clickable": "true", "bounds": bounds(19,240,131,352)})
            + node(**{"content-desc": "Zones", "bounds": bounds(122,274,958,318)}),
            bounds=bounds(0,240,1080,352),
        )
        + node(text="Front left", bounds=bounds(47,650,190,700)),
        round(1080 * scale), round(2340 * scale),
    ))
    target = VolkswagenAppDriver._safe_navigation_node(nodes)
    assert target is not None and target.clazz == "android.widget.Button"
    assert target.parent_index == next(n.parent_index for n in nodes if n.content_desc == "Zones")


def test_unknown_top_left_button_is_not_used_as_back() -> None:
    nodes = parse_ui_dump(window(node(**{
        "class": "android.widget.Button", "clickable": "true", "bounds": "[19,136][131,248]",
    })))
    assert VolkswagenAppDriver._safe_navigation_node(nodes) is None


def test_duplicate_buttons_and_adjacent_toggle_rows_fail_closed() -> None:
    nodes = parse_ui_dump(window(
        node(text="Save", bounds="[0,100][100,150]")
        + node(text="Save", bounds="[900,100][1000,150]")
        + node(text="Battery Care Mode", bounds="[40,300][400,430]")
        + node(checkable="true", checked="true", bounds="[30,290][1050,380]")
        + node(checkable="true", checked="false", bounds="[30,385][1050,490]")
    ))
    assert find_by_text(nodes, r"^Save$") is None
    assert row_toggle(nodes, r"^Battery Care Mode$") is None


@pytest.mark.parametrize("width,height", [(720,1280), (1080,2340), (1440,3200)])
async def test_scroll_uses_actual_container_and_stops_when_target_visible(width: int, height: int) -> None:
    def page(target_top: int) -> str:
        return window(node(
            node(**{"resource-id": "wanted", "bounds": f"[40,{target_top}][{width - 40},{target_top + 40}]"}),
            **{"class": "android.widget.ScrollView", "scrollable": "true", "bounds": f"[0,100][{width},{height - 100}]"},
        ), width, height)

    before, after = page(height + 10), page(400)
    transport = AsyncMock()
    transport.dump_ui.side_effect = [before, after, after]
    driver = VolkswagenAppDriver(transport, settle_s=0)
    result = await driver._scroll_to(
        parse_ui_dump(before), lambda ns: find_by_rid(ns, "wanted"), reason="wanted",
    )
    assert driver._fully_visible(find_by_rid(result, "wanted"))
    x1, y1, x2, y2, _ = transport.swipe.call_args.args
    assert x1 == x2 == width // 2
    assert 100 < y2 < y1 < height - 100
    assert transport.swipe.await_count == 1


async def test_scroll_stops_at_unchanged_end_of_list() -> None:
    xml = window(node(**{"class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,100][720,1100]"}), 720, 1280)
    transport = AsyncMock()
    transport.dump_ui.return_value = xml
    driver = VolkswagenAppDriver(transport, settle_s=0)
    with pytest.raises(CompanionTransportError, match="unsupported layout"):
        await driver._scroll_to(parse_ui_dump(xml), lambda ns: find_by_rid(ns, "absent"), reason="absent")
    assert transport.swipe.await_count == 1


def test_scroll_does_not_start_on_slider() -> None:
    nodes = parse_ui_dump(window(node(
        node(**{"class": "android.widget.SeekBar", "bounds": "[10,800][710,1000]"}),
        **{"class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,100][720,1100]"},
    ), 720, 1280))
    assert vertical_scroll_gesture(nodes) is None


async def test_layout_moves_after_lookup_no_stale_tap_sent() -> None:
    old = window(node(text="Start", bounds="[0,200][100,260]"))
    new = window(node(text="Start", bounds="[0,350][100,410]"))
    transport = AsyncMock()
    transport.dump_ui.return_value = new
    with pytest.raises(CompanionTransportError, match="layout changed"):
        await VolkswagenAppDriver(transport)._tap(find_by_text(parse_ui_dump(old), "Start"), reason="Start")
    transport.tap.assert_not_awaited()


@pytest.mark.parametrize("width,height,top,panel_y", [
    (720, 1280, 0, 1000), (1080, 2340, 0, 1868),
    (1440, 3200, 80, 2400), (1080, 2340, 136, 1600),
])
def test_map_candidate_uses_actual_map_and_sheet_geometry(
    width: int, height: int, top: int, panel_y: int,
) -> None:
    nodes = parse_ui_dump(window(
        node(**{"content-desc": "Google Map", "bounds": f"[0,{top}][{width},{height - 100}]"})
        + node(**{"content-desc": "Find vehicle", "bounds": f"[{width - 80},500][{width - 20},560]"})
        + node(**{"content-desc": "Bottom sheet collapsed", "bounds": f"[0,{panel_y}][{width},{panel_y + 30}]"}),
        width, height,
    ))
    assert centered_vehicle_marker_point(nodes) == (width // 2, (top + panel_y) // 2)


@pytest.mark.parametrize("sheet", [
    "", node(**{"content-desc": "Bottom sheet expanded", "bounds": "[0,800][720,830]"}),
    node(**{"content-desc": "Bottom sheet collapsed", "bounds": "[400,800][720,830]"}),
    node(**{"content-desc": "Bottom sheet collapsed", "bounds": "[0,1200][720,1230]"}),
])
def test_missing_expanded_side_or_outside_panel_refuses_marker_tap(sheet: str) -> None:
    nodes = parse_ui_dump(window(
        node(**{"content-desc": "Google Map", "bounds": "[0,0][720,1150]"})
        + node(**{"content-desc": "Find vehicle", "bounds": "[600,600][660,660]"})
        + sheet, 720, 1280,
    ))
    assert centered_vehicle_marker_point(nodes) is None


@pytest.mark.parametrize("scale", [2 / 3, 1, 4 / 3])
def test_charge_slider_coordinates_scale_with_actual_geometry(scale: float) -> None:
    def bounds(left: int, top: int, right: int, bottom: int) -> str:
        return f"[{round(left * scale)},{round(top * scale)}][{round(right * scale)},{round(bottom * scale)}]"

    nodes = parse_ui_dump(window(
        node(**{"resource-id": "subtitle", "text": "Charging up to (50-100%)", "bounds": bounds(94,730,986,770)})
        + node(**{"resource-id": "slider", "class": "android.widget.SeekBar", "bounds": bounds(66,807,887,919)})
        + node(**{"content-desc": "Value, 60", "class": "android.widget.SeekBar", "bounds": bounds(199,807,311,919)})
        + node(**{"resource-id": "value", "text": "60%", "bounds": bounds(906,843,986,883)}),
        round(1080 * scale), round(2340 * scale),
    ))
    gesture = charge_limit_gesture(nodes, 80)
    assert gesture is not None
    assert abs(gesture[2] - 547 * scale) < 2


def map_card(name: str = "ID.3 Pro Performance", *, parked: bool = True) -> str:
    return node(
        # Like the real VW card, Close straddles the scrolling viewport edge.
        node(**{"content-desc": "Close details view", "clickable": "true", "bounds": "[620,830][680,890]"})
        + node(text=name, bounds="[40,890][340,920]")
        + (node(text="Test address. Parked since 1h", bounds="[40,925][600,965]") if parked else "")
        + node(node(text="Share", bounds="[200,1000][300,1040]"), bounds="[180,980][320,1080]"),
        **{"class": "android.widget.ScrollView", "bounds": "[0,840][720,1150]"},
    )


def map_page(card: str = "", *, sheet: bool = True, map_children: str = "") -> str:
    return window(
        node(map_children, **{"content-desc": "Google Map", "bounds": "[0,0][720,1150]"})
        + node(**{"content-desc": "Find vehicle", "bounds": "[600,600][660,660]"})
        + (node(**{"content-desc": "Bottom sheet collapsed", "bounds": "[0,800][720,830]"}) if sheet else "")
        + card, 720, 1280,
    )


def map_overview() -> str:
    return window(
        node(**{"content-desc": "Your vehicle: ID.3 Pro Performance. Vehicle is locked. Synchronised just now", "bounds": "[10,100][700,140]"})
        + node(**{"resource-id": "cat_nav_map_tab_navigation", "bounds": "[250,1160][400,1230]"}),
        720, 1280,
    )


class _MapTransport:
    def __init__(
        self, *, card_open: bool = False, card_name: str = "ID.3 Pro Performance",
        sheet: bool = True, close_works: bool = True, accessible_marker: bool = False,
    ) -> None:
        self.page = "overview"
        self.card_open = card_open
        self.card_name = card_name
        self.sheet = sheet
        self.close_works = close_works
        self.fresh_card = False
        self.accessible_marker = accessible_marker
        self.taps: list[tuple[int, int]] = []

    async def dump_ui(self) -> str:
        return map_overview() if self.page == "overview" else map_page(
            map_card(self.card_name) if self.card_open else "", sheet=self.sheet,
            map_children=node(clickable="true", bounds="[340,390][380,450]") if self.accessible_marker else "",
        )

    async def dump_active_ui(self) -> str:
        assert self.taps[-1] == (250, 1020)  # verified Share was actually tapped
        # Address/name are identical in both cards. Only selecting the marker
        # again refreshes the URL; Find vehicle deliberately does not do so.
        coords = "50.1,14.2" if self.fresh_card else "50.9,14.9"
        return window(node(text=f"https://www.google.com/maps/place/{coords}", bounds="[0,300][600,500]"), 720, 1280)

    async def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        if self.page == "overview":
            self.page = "map"
        elif (x, y) == (650, 865) and self.close_works:
            self.card_open = False
        elif (x, y) == (360, 400):
            self.card_open = True
            self.fresh_card = True


@pytest.mark.parametrize("card_open", [True, False])
@pytest.mark.parametrize("accessible_marker", [True, False])
async def test_map_reopens_stale_card_before_sharing_even_if_address_matches(
    card_open: bool, accessible_marker: bool,
) -> None:
    transport = _MapTransport(card_open=card_open, accessible_marker=accessible_marker)
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(map_overview()))
    assert await driver._read_location() == {"latitude": 50.1, "longitude": 14.2}
    expected = [(325, 1195)]  # map tab
    if card_open:
        expected.append((650, 865))  # visible centre of partly clipped Close
    expected.extend([(630, 630), (360, 400), (250, 1020)])  # Find, marker, Share
    assert transport.taps == expected


async def test_failed_card_close_never_shares_stale_location() -> None:
    transport = _MapTransport(card_open=True, close_works=False)
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(map_overview()))
    with pytest.raises(CompanionTransportError, match="old vehicle parking card to close"):
        await driver._read_location()
    assert transport.taps == [(325, 1195), (650, 865)]


async def test_each_gps_read_reselects_marker_instead_of_reusing_previous_card() -> None:
    transport = _MapTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(map_overview()))
    for _ in range(2):
        transport.page = "overview"
        transport.fresh_card = False  # another parking event, same visible card
        assert await driver._read_location() == {"latitude": 50.1, "longitude": 14.2}
    assert transport.taps.count((360, 400)) == 2
    assert transport.taps.count((650, 865)) == 1


async def test_partial_visibility_remains_rejected_for_normal_taps() -> None:
    nodes = parse_ui_dump(map_page(map_card()))
    transport = AsyncMock()
    driver = VolkswagenAppDriver(transport)
    with pytest.raises(CompanionTransportError, match="could not find Close"):
        await driver._tap(find_by_desc(nodes, r"^Close details view$"), reason="Close")
    transport.tap.assert_not_awaited()


async def test_clipped_close_rechecks_visible_viewport_before_tapping() -> None:
    old = map_page(map_card())
    moved = old.replace("[0,840][720,1150]", "[0,850][720,1150]")
    transport = AsyncMock()
    transport.dump_ui.return_value = moved
    driver = VolkswagenAppDriver(transport)
    with pytest.raises(CompanionTransportError, match="layout changed"):
        await driver._tap(
            find_by_desc(parse_ui_dump(old), r"^Close details view$"),
            reason="Close", allow_clipped=True,
        )
    transport.tap.assert_not_awaited()


async def test_mostly_clipped_close_is_not_tapped() -> None:
    xml = map_page(map_card()).replace("[620,830][680,890]", "[620,750][680,890]")
    transport = AsyncMock()
    driver = VolkswagenAppDriver(transport)
    with pytest.raises(CompanionTransportError, match="mostly clipped"):
        await driver._tap(
            find_by_desc(parse_ui_dump(xml), r"^Close details view$"),
            reason="Close", allow_clipped=True,
        )
    transport.tap.assert_not_awaited()


@pytest.mark.parametrize("card_open", [True, False])
async def test_wrong_map_card_never_shares_coordinates(card_open: bool) -> None:
    transport = _MapTransport(card_open=card_open, card_name="Nearby charging station")
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(map_overview()))
    with pytest.raises(CompanionTransportError):
        await driver._read_location()
    assert (250, 1020) not in transport.taps


def test_matching_name_without_parking_evidence_is_insufficient() -> None:
    assert vehicle_map_share(parse_ui_dump(map_page(map_card(parked=False))), "ID.3 Pro Performance") is None
    assert overview_vehicle_name(parse_ui_dump(map_overview())) == "ID.3 Pro Performance"


def test_share_from_another_panel_cannot_borrow_vehicle_identity() -> None:
    card_without_share = map_card().replace('text="Share"', 'text="Other action"')
    xml = map_page(card_without_share + node(text="Share", bounds="[0,300][100,350]"))
    assert vehicle_map_share(parse_ui_dump(xml), "ID.3 Pro Performance") is None


def test_overlay_covering_map_centre_blocks_candidate() -> None:
    xml = map_page(node(clickable="true", text="Search", bounds="[200,300][500,500]"))
    assert centered_vehicle_marker_point(parse_ui_dump(xml)) is None


@pytest.mark.parametrize("scale", [2 / 3, 1, 4 / 3])
def test_unlabelled_map_marker_is_not_mistaken_for_overlay(scale: float) -> None:
    def bounds(l: int, t: int, r: int, b: int) -> str:
        return f"[{round(l * scale)},{round(t * scale)}][{round(r * scale)},{round(b * scale)}]"
    xml = window(
        node(node(clickable="true", bounds=bounds(340,390,380,450)),
             **{"content-desc": "Google Map", "bounds": bounds(0,0,720,1150)})
        + node(**{"content-desc": "Find vehicle", "bounds": bounds(600,600,660,660)})
        + node(**{"content-desc": "Bottom sheet collapsed", "bounds": bounds(0,800,720,830)}),
        round(720 * scale), round(1280 * scale),
    )
    assert centered_vehicle_marker_point(parse_ui_dump(xml)) == (
        round(720 * scale) // 2, round(800 * scale) // 2,
    )


@pytest.mark.parametrize("attrs", [
    {"text": "Search"}, {"content-desc": "Zoom"}, {"resource-id": "map_control"},
    {"class": "android.widget.Button"}, {"checkable": "true"},
    {"bounds": "[100,100][600,700]"}, {"package": "another.app"},
])
def test_map_child_controls_are_not_treated_as_markers(attrs: dict[str, str]) -> None:
    child = node(**{"clickable": "true", "bounds": "[340,390][380,450]", **attrs})
    assert centered_vehicle_marker_point(parse_ui_dump(map_page(map_children=child))) is None


def test_unlabelled_sibling_overlay_still_blocks_marker_tap() -> None:
    xml = map_page(node(clickable="true", bounds="[340,390][380,450]"))
    assert centered_vehicle_marker_point(parse_ui_dump(xml)) is None


def test_two_overlapping_map_candidates_are_ambiguous() -> None:
    children = (node(clickable="true", bounds="[340,390][380,450]")
                + node(clickable="true", bounds="[330,380][370,440]"))
    assert centered_vehicle_marker_point(parse_ui_dump(map_page(map_children=children))) is None


async def test_missing_overview_vehicle_name_prevents_navigation() -> None:
    transport = _MapTransport()
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    driver.ensure_overview = AsyncMock(return_value=[])
    with pytest.raises(CompanionTransportError, match="vehicle name unavailable"):
        await driver._read_location()
    assert not transport.taps


async def test_card_swapped_before_share_is_rejected() -> None:
    original = parse_ui_dump(map_page(map_card()))
    transport = AsyncMock()
    transport.dump_ui.return_value = map_page(map_card("Other car"))
    driver = VolkswagenAppDriver(transport)
    with pytest.raises(CompanionTransportError, match="screen verification failed"):
        await driver._tap(
            vehicle_map_share(original, "ID.3 Pro Performance"), reason="Share",
            validate_screen=lambda ns: vehicle_map_share(ns, "ID.3 Pro Performance") is not None,
        )
    transport.tap.assert_not_awaited()


async def test_unsupported_map_layout_refuses_guessed_marker_tap() -> None:
    transport = _MapTransport(sheet=False)
    driver = VolkswagenAppDriver(transport, settle_s=0)  # type: ignore[arg-type]
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(map_overview()))
    with pytest.raises(CompanionTransportError, match="cannot be verified"):
        await driver._read_location()
    assert len(transport.taps) == 2
