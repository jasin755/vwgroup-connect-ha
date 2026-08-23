package me.pognerebko.vagcompanion;

import android.content.Context;
import android.util.Base64;
import android.util.Log;

import org.json.JSONObject;
import org.json.JSONException;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Long-polls Home Assistant so normal operation never depends on inbound
 * phone routing, a fixed phone address, or Wireless ADB. */
final class AgentRelayClient implements AutoCloseable {
    private static final String TAG = "VagCompanionRelay";
    private static final String PREFS = "relay";
    private static final String KEY_URL = "url";
    private static final String KEY_CHANNEL = "channel";

    private final VagAccessibilityService service;
    private final ExecutorService eventExecutor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean eventPushScheduled = new AtomicBoolean();
    private final AtomicLong latestAccessibilityRevision = new AtomicLong();
    private volatile boolean running;
    private Thread thread;

    AgentRelayClient(VagAccessibilityService service) {
        this.service = service;
    }

    static void saveConfig(Context context, String relayUrl, String channelId) {
        String normalized = relayUrl.endsWith("/")
                ? relayUrl.substring(0, relayUrl.length() - 1)
                : relayUrl;
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_URL, normalized)
                .putString(KEY_CHANNEL, channelId)
                .apply();
    }

    static String loadRelayUrl(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_URL, "");
    }

    static String loadChannelId(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_CHANNEL, "");
    }

    void start() {
        String url = relayUrl();
        String channel = channelId();
        String token = AgentHttpServer.loadToken(service);
        if (url.isEmpty() || token.isEmpty() || running) {
            return;
        }
        running = true;
        thread = new Thread(this::runLoop, "vag-agent-relay");
        thread.setDaemon(true);
        thread.start();
    }

    @Override
    public void close() {
        running = false;
        eventExecutor.shutdownNow();
        if (thread != null) {
            thread.interrupt();
            thread = null;
        }
    }

    void onAccessibilityChanged(long revision) {
        latestAccessibilityRevision.set(revision);
        if (!running || !eventPushScheduled.compareAndSet(false, true)) {
            return;
        }
        eventExecutor.execute(this::pushDebouncedSnapshot);
    }

    private void pushDebouncedSnapshot() {
        long sentRevision = -1;
        try {
            while (running) {
                Thread.sleep(300);
                long revision = latestAccessibilityRevision.get();
                VagAccessibilityService.Snapshot snapshot = service.snapshot(3_000);
                if ("ok".equals(snapshot.status)) {
                    JSONObject payload = new JSONObject()
                            .put("agent_version", packageVersion())
                            .put("vw_version", service.packageVersion(
                                    VagAccessibilityService.VOLKSWAGEN_PACKAGE))
                            .put("phone_battery_level", service.batteryLevel())
                            .put("event_only", true)
                            .put("revision", revision)
                            .put(
                                    "event_snapshot_b64",
                                    Base64.encodeToString(
                                            snapshot.xml.getBytes(StandardCharsets.UTF_8),
                                            Base64.NO_WRAP));
                    post(payload);
                    sentRevision = revision;
                }
                if (latestAccessibilityRevision.get() == revision) {
                    break;
                }
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        } catch (Exception error) {
            Log.d(TAG, "Event snapshot retry after " + error.getClass().getSimpleName());
            try {
                Thread.sleep(2_000);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        } finally {
            eventPushScheduled.set(false);
            if (running && latestAccessibilityRevision.get() > sentRevision) {
                onAccessibilityChanged(latestAccessibilityRevision.get());
            }
        }
    }

    private void runLoop() {
        JSONObject pendingResult = null;
        while (running) {
            try {
                JSONObject payload = new JSONObject()
                        .put("agent_version", packageVersion())
                        .put("vw_version", service.packageVersion(
                                VagAccessibilityService.VOLKSWAGEN_PACKAGE))
                        .put("phone_battery_level", service.batteryLevel());
                if (pendingResult != null) {
                    payload.put("result", pendingResult);
                }
                JSONObject response = post(payload);
                pendingResult = null;
                JSONObject command = response.optJSONObject("command");
                if (command != null) {
                    pendingResult = execute(command);
                }
            } catch (Exception error) {
                Log.d(TAG, "Relay retry after " + error.getClass().getSimpleName());
                if (!sleepBeforeRetry()) {
                    break;
                }
            }
        }
    }

    private JSONObject post(JSONObject payload) throws Exception {
        URL endpoint = new URL(relayUrl() + endpointPath(channelId()));
        HttpURLConnection connection = (HttpURLConnection) endpoint.openConnection();
        try {
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(30_000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty(
                    "X-Token", AgentHttpServer.loadToken(service));
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
            }
            int status = connection.getResponseCode();
            InputStream stream = status >= 400
                    ? connection.getErrorStream()
                    : connection.getInputStream();
            String response = stream == null ? "" : readFully(stream);
            if (status != 200) {
                throw new IOException("relay HTTP " + status);
            }
            return new JSONObject(response);
        } finally {
            connection.disconnect();
        }
    }

    private JSONObject execute(JSONObject command) {
        String id = command.optString("id", "");
        String action = command.optString("action", "");
        JSONObject params = command.optJSONObject("params");
        if (params == null) {
            params = new JSONObject();
        }
        JSONObject result = putNoThrow(new JSONObject(), "id", id);
        try {
            switch (action) {
                case "snapshot":
                    return snapshotResult(result, service.snapshot(3_000));
                case "snapshot_active":
                    return snapshotResult(result, service.snapshotActive(3_000));
                case "tap":
                    return accepted(result, service.tap(
                            params.optInt("x", -1), params.optInt("y", -1), 3_000));
                case "swipe":
                    return accepted(result, service.swipe(
                            params.optInt("x1", -1),
                            params.optInt("y1", -1),
                            params.optInt("x2", -1),
                            params.optInt("y2", -1),
                            params.optLong("duration", 300),
                            3_000));
                case "back":
                    return accepted(result, service.pressBack(3_000));
                case "wake":
                    return accepted(result, service.wakeScreen());
                case "sleep":
                    return accepted(result, service.lockScreen(3_000));
                case "foreground":
                    return result.put("status", "ok").put(
                            "value",
                            service.isPackageForeground(
                                    params.optString(
                                            "package",
                                            VagAccessibilityService.VOLKSWAGEN_PACKAGE),
                                    3_000));
                case "launch":
                    return accepted(result, service.launchPackage(
                            params.optString(
                                    "package",
                                    VagAccessibilityService.VOLKSWAGEN_PACKAGE),
                            3_000));
                case "version":
                    return result.put("status", "ok").put(
                            "value",
                            service.packageVersion(params.optString(
                                    "package",
                                    VagAccessibilityService.VOLKSWAGEN_PACKAGE)));
                case "battery":
                    int batteryLevel = service.batteryLevel();
                    return result.put(
                                    "status", batteryLevel >= 0 ? "ok" : "error")
                            .put("value", batteryLevel)
                            .put("error", batteryLevel >= 0 ? "" : "battery unavailable");
                default:
                    return result.put("status", "error")
                            .put("error", "unknown action");
            }
        } catch (Exception error) {
            putNoThrow(result, "status", "error");
            return putNoThrow(result, "error", error.getClass().getSimpleName());
        }
    }

    private static JSONObject snapshotResult(
            JSONObject result, VagAccessibilityService.Snapshot snapshot)
            throws JSONException {
        result.put("status", snapshot.status);
        result.put("revision", snapshot.revision);
        if (!snapshot.xml.isEmpty()) {
            result.put(
                    "xml_b64",
                    Base64.encodeToString(
                            snapshot.xml.getBytes(StandardCharsets.UTF_8),
                            Base64.NO_WRAP));
        }
        return result;
    }

    private static JSONObject accepted(JSONObject result, boolean accepted)
            throws JSONException {
        return result.put("status", accepted ? "accepted" : "error")
                .put("error", accepted ? "" : "action rejected");
    }

    private static JSONObject putNoThrow(JSONObject object, String key, Object value) {
        try {
            return object.put(key, value);
        } catch (JSONException impossible) {
            return object;
        }
    }

    private boolean sleepBeforeRetry() {
        try {
            Thread.sleep(2_000);
            return running;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private String relayUrl() {
        return loadRelayUrl(service);
    }

    private String channelId() {
        return loadChannelId(service);
    }

    static String endpointPath(String channelId) throws Exception {
        if (channelId == null || channelId.isEmpty()) {
            return "/api/vag_connect/companion_agent/by-token";
        }
        return "/api/vag_connect/companion_agent/"
                + URLEncoder.encode(channelId, "UTF-8");
    }

    private String packageVersion() {
        return service.packageVersion(service.getPackageName());
    }

    private static String readFully(InputStream input) throws IOException {
        try (InputStream stream = input;
                ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int read;
            while ((read = stream.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }
}
