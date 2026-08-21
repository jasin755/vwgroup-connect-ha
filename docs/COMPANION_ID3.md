# ID.3 companion map (We Connect 4.3.2)

This fork grounds the Android companion channel against a live Volkswagen ID.3
running the English We Connect 4.3.2 UI. Extended navigation is opt-in under:

`VW Group Connect → Configure → Companion: read all confirmed ID.3 screens`

It runs at most every 15 minutes and caches values between navigation reads.

## Read map

| Value | App screen | Accessibility anchor | Home Assistant field |
|---|---|---|---|
| Locked/unlocked | Vehicle overview | vehicle title `content-desc` (`Vehicle is locked/unlocked`) | `doors_locked` |
| Electric range | Vehicle overview | `rangeTile` narration | `electric_range_km`, `range_km` |
| Battery SoC | Range detail | `rangeArcBatterySoc` | `battery_soc` |
| Climate on/off | Vehicle overview | `climateTile` narration | `climatisation_state`, `climatisation_active` |
| Climate target | Air Conditioning | centre value inside `clima_compose_view` | `target_temperature` |
| Outside temperature | Air Conditioning | `outside_temperature_layout` text | `outside_temp` |
| Auxiliary conditioning at unlock | Air Conditioning → Settings | `ClimatisationAtUnlockEnabled.checked` | `climate_at_unlock` |
| Automatic window heating | Air Conditioning → Settings | `WindowHeatingEnabled.checked` | `window_heating_enabled` |
| Front-left/right zones | Air Conditioning → Settings → Zones | labelled checkable rows | `climate_zone_front_*_enabled` |
| Charge limit | Vehicle → Settings | `Charging up to` / `value` | `target_soc` |
| Battery Care Mode | Vehicle → Settings | labelled checkable row | `battery_care_enabled` |
| Reduced AC current | Vehicle → Settings | labelled checkable row | `max_charging_current` |
| Auto-release AC connector | Vehicle → Settings | labelled checkable row | `auto_unlock_when_charged` |
| Total distance | Vehicle Health Report | `totalDistance` / sibling text | `odometer_km` |
| Next service | Vehicle Health Report | `nextInspection` / sibling text | `service_due_in_days` |
| Parking coordinates | Navigation → Find vehicle → marker → Share | `content_preview_text` Google Maps `/place/lat,lon` URL | `latitude`, `longitude` |

All read routes above were exercised against the live phone. The location test
redacts the coordinate values from logs.

## Control map

| Control | Home Assistant surface | App flow |
|---|---|---|
| Start/stop climate | Climate entity + climate switch | `climateTile` → `cta_start` / `cta_stop` |
| Target temperature | Climate/Number entity | adjacent 0.5 °C value in `clima_compose_view` |
| Auxiliary conditioning at unlock | Switch | `climateTile` → Settings → `ClimatisationAtUnlockEnabled` |
| Automatic window heating | Switch | `climateTile` → Settings → `WindowHeatingEnabled` |
| Front-left/right zones | Two switches | `climateTile` → Settings → Zones → labelled switch |
| Charge limit | Charge Target Number | Vehicle Settings custom slider (50–100%, 10% step) |
| Battery Care Mode | Existing Battery Care switch | Vehicle Settings labelled switch |
| Reduced AC current | Maximum/Reduced select | Vehicle Settings labelled switch |
| Auto-release connector | Existing auto-unlock switch | Vehicle Settings labelled switch |

Write flows are version-gated, serialised against polls, verify toggle/slider
state after a tap, debounce duplicate logical commands, and never block an
immediate climate/charging stop. Tests
exercise them through a simulated transport. They have deliberately not been
actuated against the live car during read-only mapping; live validation should
change one reversible setting at a time and restore it afterwards.

Rich climate payloads cannot change several preferences and start climate in
one call; use the individual setting entities first. This keeps a multi-setting
service from producing an uncontrolled burst while normal UI adjustments remain
usable.
