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
    find_by_rid,
    find_by_text,
    legacy_vehicle_marker_point,
    row_toggle,
    vertical_scroll_gesture,
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


def test_map_guess_is_limited_to_measured_layout() -> None:
    children = node(**{"content-desc": "Google Map", "bounds": "[0,0][1080,2151]"}) + node(**{
        "content-desc": "Find vehicle", "bounds": "[949,1359][1005,1415]",
    })
    assert legacy_vehicle_marker_point(parse_ui_dump(window(children))) is not None
    assert legacy_vehicle_marker_point(parse_ui_dump(window(children, 1440, 3200))) is None
    changed = children.replace("[949,1359][1005,1415]", "[949,1459][1005,1515]")
    assert legacy_vehicle_marker_point(parse_ui_dump(window(changed))) is None


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


async def test_non_pixel_map_with_vehicle_card_needs_no_coordinate_guess() -> None:
    xml = window(
        node(**{"resource-id": "cat_nav_map_tab_navigation", "bounds": "[250,1050][400,1120]"})
        + node(**{"content-desc": "Google Map", "bounds": "[0,0][720,1150]"})
        + node(**{"content-desc": "Find vehicle", "bounds": "[600,600][660,660]"})
        + node(text="Share", bounds="[200,900][300,950]"), 720, 1280,
    )
    share = window(node(text="https://www.google.com/maps/place/50.1,14.2", bounds="[0,300][600,500]"), 720, 1280)
    transport = AsyncMock()
    transport.dump_ui.return_value = xml
    transport.dump_active_ui.return_value = share
    driver = VolkswagenAppDriver(transport, settle_s=0)
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(xml))
    assert await driver._read_location() == {"latitude": 50.1, "longitude": 14.2}
    assert transport.tap.await_count == 3  # Map tab, Find vehicle, Share; no marker


async def test_unsupported_map_layout_refuses_guessed_marker_tap() -> None:
    xml = window(
        node(**{"resource-id": "cat_nav_map_tab_navigation", "bounds": "[250,1050][400,1120]"})
        + node(**{"content-desc": "Google Map", "bounds": "[0,0][720,1150]"})
        + node(**{"content-desc": "Find vehicle", "bounds": "[600,600][660,660]"}), 720, 1280,
    )
    transport = AsyncMock()
    transport.dump_ui.return_value = xml
    driver = VolkswagenAppDriver(transport, settle_s=0)
    driver.ensure_overview = AsyncMock(return_value=parse_ui_dump(xml))
    with pytest.raises(CompanionTransportError, match="open the vehicle card manually"):
        await driver._read_location()
    assert transport.tap.await_count == 2
