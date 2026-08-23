# VAG Companion Agent

Small Android AccessibilityService used by the experimental Volkswagen
companion channel. It reads only the visible accessibility tree of the official
Volkswagen app and performs gestures requested by Home Assistant.

Normal operation does **not** use ADB. The agent opens an authenticated outbound
HTTPS long-poll to Home Assistant and receives commands in the response;
Wireless Debugging can be disabled after installation and provisioning. The
phone does not need a fixed address or an inbound firewall/VLAN rule. The local
port `8765` remains available for trusted-LAN diagnostics.

Volkswagen accessibility changes are debounced for 300 ms and pushed as
overview snapshots over the relay. Home Assistant ignores detail/splash trees,
so only complete overview state produces an event-driven entity update.

While its AccessibilityService is enabled, the agent holds a dim screen wake
lock. This keeps a dedicated companion phone available even when a smart plug
has disconnected its charger; Android releases the lock when the service stops.

## Security model

- The API listens on the phone's LAN interfaces and requires `X-Token` on every
  request.
- The outbound Home Assistant relay uses the same token on every HTTPS poll.
- The token is provisioned through a ContentProvider protected by Android's
  signature-level `DUMP` permission. Regular phone applications cannot call it.
- The agent never reads Volkswagen credentials, app-private storage, OAuth
  tokens or HTTPS traffic.
- Accessibility is restricted to `com.volkswagen.weconnect` in the service
  configuration.

Use a random URL-safe token of at least 32 characters and keep the phone and
Home Assistant on a trusted network.

## Build

The project uses Java 17, Android SDK 35 and the checked-in Gradle wrapper.

```bash
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## Install and configure

Download [`releases/vag-companion-agent.apk`](releases/vag-companion-agent.apk),
open it on the Android phone and allow installation from that source. ADB is
not required for the prebuilt APK.

Open **VAG Companion Agent** on the phone:

1. Enter the Home Assistant base URL, preferably HTTPS.
2. Tap **Generate secure token**, then **Copy token for Home Assistant**.
3. Tap **Save Home Assistant configuration**.
4. Tap **Open Accessibility settings** and enable **VAG Companion Agent**.
5. Open the official Volkswagen app and leave it on the vehicle overview.

Before the HA config entry exists, the Agent retries the token-discovery relay
route. The HA integration's one-time LAN probe then creates the matching broker
and automatically switches normal operation to the outbound relay. Users never
need to know the internal HA config-entry id.

ADB remains available to developers and preserves existing configuration when
the APK has the same signature:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Verify from another machine on the same LAN:

```bash
curl -H 'X-Token: YOUR_RANDOM_TOKEN' http://PHONE_IP:8765/health
```

Installing a newer APK from this repository over the existing copy preserves
the token and AccessibilityService, provided Android accepts the matching
signature.

## API

Read endpoints:

- `GET /health`
- `GET /battery` — Android device battery percentage
- `GET /snapshot` — base64-encoded uiautomator-compatible XML
- `GET /snapshot-active` — explicit active-window snapshot used only for the
  Android share sheet after the agent itself selected Share
- `GET /version?package=...`
- `GET /foreground?package=...`
- `GET /wait?after=REVISION&timeout=MILLISECONDS`

Action endpoints accept `POST`:

- `/launch?package=...`
- `/tap?x=...&y=...`
- `/swipe?x1=...&y1=...&x2=...&y2=...&duration=...`
- `/back`
- `/wake`
- `/sleep`
