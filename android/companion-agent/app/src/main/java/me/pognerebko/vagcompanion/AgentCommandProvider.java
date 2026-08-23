package me.pognerebko.vagcompanion;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.BaseBundle;
import android.os.Bundle;
import android.util.Base64;

import java.nio.charset.StandardCharsets;

public final class AgentCommandProvider extends ContentProvider {
    private static final long SNAPSHOT_TIMEOUT_MS = 2_000;
    private static final long MAX_WAIT_MS = 10_000;

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        Bundle result = new Bundle();
        VagAccessibilityService service = VagAccessibilityService.getInstance();
        if (service == null) {
            result.putString("status", "service_disabled");
            result.putBoolean("enabled", false);
            result.putLong("revision", 0);
            return result;
        }

        result.putBoolean("enabled", true);
        result.putLong("revision", service.getRevision());
        result.putString("event_package", service.getEventPackage());
        result.putString("event_class", service.getEventClass());

        switch (method == null ? "" : method) {
            case "provision":
                String token = extras == null ? "" : extras.getString("token", "");
                String relayUrl = extras == null
                        ? ""
                        : extras.getString("relay_url", "");
                String relayUrlB64 = extras == null
                        ? ""
                        : extras.getString("relay_url_b64", "");
                if (relayUrl.isEmpty() && !relayUrlB64.isEmpty()) {
                    try {
                        relayUrl = new String(
                                Base64.decode(relayUrlB64, Base64.DEFAULT),
                                StandardCharsets.UTF_8);
                    } catch (IllegalArgumentException ignored) {
                        relayUrl = "";
                    }
                }
                String channelId = extras == null
                        ? ""
                        : extras.getString("channel_id", "");
                if (!AgentHttpServer.isValidToken(token)) {
                    result.putString("status", "invalid_token");
                } else if (!relayUrl.isEmpty()
                        && !relayUrl.startsWith("https://")
                        && !relayUrl.startsWith("http://")) {
                    result.putString("status", "invalid_relay");
                } else if (getContext() == null) {
                    result.putString("status", "no_context");
                } else {
                    AgentHttpServer.saveToken(getContext(), token);
                    if (!relayUrl.isEmpty()) {
                        AgentRelayClient.saveConfig(getContext(), relayUrl, channelId);
                    }
                    service.restartRelayClient();
                    result.putString("status", "ok");
                }
                return result;
            case "health":
                result.putString("status", "ok");
                return result;
            case "snapshot":
                addSnapshot(result, service.snapshot(SNAPSHOT_TIMEOUT_MS));
                return result;
            case "wait":
                long after = getLong(extras, "after_revision", 0);
                long timeout = Math.min(
                        MAX_WAIT_MS,
                        Math.max(0, getLong(extras, "timeout_ms", 1_500)));
                result.putLong("revision", service.waitForRevision(after, timeout));
                addSnapshot(result, service.snapshot(SNAPSHOT_TIMEOUT_MS));
                return result;
            case "tap":
                int x = getInt(extras, "x", -1);
                int y = getInt(extras, "y", -1);
                if (x < 0 || y < 0) {
                    result.putString("status", "invalid_coordinates");
                } else {
                    result.putString(
                            "status",
                            service.tap(x, y, SNAPSHOT_TIMEOUT_MS)
                                    ? "accepted"
                                    : "gesture_rejected");
                }
                return result;
            case "back":
                result.putString(
                        "status",
                        service.pressBack(SNAPSHOT_TIMEOUT_MS)
                                ? "accepted"
                                : "action_rejected");
                return result;
            default:
                result.putString("status", "unknown_method");
                return result;
        }
    }

    private static void addSnapshot(
            Bundle result,
            VagAccessibilityService.Snapshot snapshot) {
        result.putString("status", snapshot.status);
        result.putLong("revision", snapshot.revision);
        if (!snapshot.xml.isEmpty()) {
            result.putString(
                    "xml_b64",
                    Base64.encodeToString(
                            snapshot.xml.getBytes(StandardCharsets.UTF_8),
                            Base64.NO_WRAP));
        }
    }

    private static long getLong(BaseBundle extras, String key, long fallback) {
        return extras == null ? fallback : extras.getLong(key, fallback);
    }

    private static int getInt(BaseBundle extras, String key, int fallback) {
        return extras == null ? fallback : extras.getInt(key, fallback);
    }

    @Override
    public Cursor query(
            Uri uri,
            String[] projection,
            String selection,
            String[] selectionArgs,
            String sortOrder) {
        return null;
    }

    @Override
    public String getType(Uri uri) {
        return null;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(
            Uri uri,
            ContentValues values,
            String selection,
            String[] selectionArgs) {
        return 0;
    }
}
