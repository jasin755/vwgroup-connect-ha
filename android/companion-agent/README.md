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

## Install and provision

ADB is needed for this one-time operation and for future APK updates:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell settings put secure enabled_accessibility_services \
  me.pognerebko.vagcompanion/.VagAccessibilityService
adb shell settings put secure accessibility_enabled 1
adb shell content call \
  --uri content://me.pognerebko.vagcompanion.agent \
  --method provision \
  --extra token:s:YOUR_RANDOM_TOKEN \
  --extra relay_url_b64:s:BASE64_OF_HTTPS_HA_URL \
  --extra channel_id:s:HA_CONFIG_ENTRY_ID
```

Verify from another machine on the same LAN:

```bash
curl -H 'X-Token: YOUR_RANDOM_TOKEN' http://PHONE_IP:8765/health
```

An update installed with `adb install -r` preserves both the token and the
enabled AccessibilityService, provided it is signed with the same key.

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
