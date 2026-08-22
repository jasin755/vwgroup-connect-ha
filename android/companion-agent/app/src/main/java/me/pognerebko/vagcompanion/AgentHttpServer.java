package me.pognerebko.vagcompanion;

import android.content.Context;
import android.net.Uri;
import android.util.Base64;
import android.util.Log;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;

/** A tiny authenticated HTTP server bound strictly to the phone's loopback.
 *
 * Home Assistant reaches it directly over the trusted LAN. Every request is
 * authenticated, and no Volkswagen credential or app token is ever exposed.
 * ADB is only needed to install/update the APK and provision the first token.
 */
final class AgentHttpServer implements AutoCloseable {
    static final int PORT = 8765;

    private static final String TAG = "VagCompanionAgent";
    private static final String PREFS = "agent";
    private static final String KEY_TOKEN = "api_token";
    private static final int SOCKET_TIMEOUT_MS = 5_000;
    private static final int MAX_HEADER_LINES = 64;
    private static final long MAX_WAIT_MS = 10_000;

    private final VagAccessibilityService service;
    private final ExecutorService clients = Executors.newFixedThreadPool(2);
    private final AtomicLong requestCount = new AtomicLong();
    private final AtomicLong snapshotCount = new AtomicLong();
    private final AtomicLong actionCount = new AtomicLong();
    private volatile boolean running;
    private volatile String lastNonHealthClient = "";
    private volatile String lastNonHealthPath = "";
    private ServerSocket serverSocket;
    private Thread acceptThread;

    AgentHttpServer(VagAccessibilityService service) {
        this.service = service;
    }

    void start() {
        if (running) {
            return;
        }
        running = true;
        acceptThread = new Thread(this::acceptLoop, "vag-agent-http");
        acceptThread.setDaemon(true);
        acceptThread.start();
    }

    @Override
    public void close() {
        running = false;
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {
                // Closing the listening socket is the normal shutdown path.
            }
        }
        clients.shutdownNow();
    }

    static void saveToken(Context context, String token) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_TOKEN, token)
                .apply();
    }

    static String loadToken(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_TOKEN, "");
    }

    static boolean isValidToken(String token) {
        if (token == null || token.length() < 32 || token.length() > 128) {
            return false;
        }
        for (int index = 0; index < token.length(); index++) {
            char character = token.charAt(index);
            boolean allowed = Character.isLetterOrDigit(character)
                    || character == '-'
                    || character == '_';
            if (!allowed) {
                return false;
            }
        }
        return true;
    }

    private void acceptLoop() {
        try (ServerSocket listener = new ServerSocket()) {
            serverSocket = listener;
            listener.setReuseAddress(true);
            listener.bind(new InetSocketAddress(InetAddress.getByName("0.0.0.0"), PORT), 4);
            while (running) {
                try {
                    Socket client = listener.accept();
                    clients.execute(() -> handle(client));
                } catch (SocketException closed) {
                    if (running) {
                        Log.w(TAG, "Agent socket closed unexpectedly", closed);
                    }
                    break;
                }
            }
        } catch (IOException error) {
            if (running) {
                Log.e(TAG, "Could not start loopback agent server", error);
            }
        } finally {
            running = false;
            serverSocket = null;
        }
    }

    private void handle(Socket socket) {
        try (Socket client = socket) {
            client.setSoTimeout(SOCKET_TIMEOUT_MS);
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                    client.getInputStream(), StandardCharsets.US_ASCII));
            String requestLine = reader.readLine();
            if (requestLine == null) {
                return;
            }
            Map<String, String> headers = readHeaders(reader);
            String expected = loadToken(service);
            if (!tokensEqual(expected, headers.getOrDefault("x-token", ""))) {
                write(client.getOutputStream(), 403, "forbidden");
                return;
            }

            String[] requestParts = requestLine.split(" ", 3);
            if (requestParts.length < 2
                    || !("GET".equals(requestParts[0]) || "POST".equals(requestParts[0]))) {
                write(client.getOutputStream(), 400, "bad_request");
                return;
            }
            String target = requestParts[1];
            int queryStart = target.indexOf('?');
            String path = queryStart >= 0 ? target.substring(0, queryStart) : target;
            Map<String, String> query = queryStart >= 0
                    ? parseQuery(target.substring(queryStart + 1))
                    : new HashMap<>();
            requestCount.incrementAndGet();
            if ("/snapshot".equals(path) || "/wait".equals(path)) {
                snapshotCount.incrementAndGet();
            } else if (!"/health".equals(path)
                    && !"/version".equals(path)
                    && !"/foreground".equals(path)) {
                actionCount.incrementAndGet();
            }
            if (!"/health".equals(path)) {
                lastNonHealthClient = client.getInetAddress().getHostAddress();
                lastNonHealthPath = path;
            }

            switch (path) {
                case "/health":
                    write(
                            client.getOutputStream(),
                            200,
                            "{\"status\":\"ok\",\"revision\":"
                                    + service.getRevision()
                                    + ",\"vw_version\":\""
                                    + jsonEscape(service.packageVersion(
                                            VagAccessibilityService.VOLKSWAGEN_PACKAGE))
                                    + "\",\"requests\":"
                                    + requestCount.get()
                                    + ",\"snapshots\":"
                                    + snapshotCount.get()
                                    + ",\"actions\":"
                                    + actionCount.get()
                                    + ",\"last_client\":\""
                                    + jsonEscape(lastNonHealthClient)
                                    + "\",\"last_path\":\""
                                    + jsonEscape(lastNonHealthPath)
                                    + "\"}");
                    return;
                case "/snapshot":
                    writeSnapshot(client.getOutputStream(), service.snapshot(2_000));
                    return;
                case "/snapshot-active":
                    writeSnapshot(client.getOutputStream(), service.snapshotActive(2_000));
                    return;
                case "/wait":
                    long after = parseLong(query.get("after"), 0);
                    long timeout = Math.min(
                            MAX_WAIT_MS,
                            Math.max(0, parseLong(query.get("timeout"), 1_500)));
                    service.waitForRevision(after, timeout);
                    writeSnapshot(client.getOutputStream(), service.snapshot(2_000));
                    return;
                case "/tap":
                    int x = (int) parseLong(query.get("x"), -1);
                    int y = (int) parseLong(query.get("y"), -1);
                    if (x < 0 || y < 0) {
                        write(client.getOutputStream(), 400, "invalid_coordinates");
                    } else {
                        boolean accepted = service.tap(x, y, 2_000);
                        write(
                                client.getOutputStream(),
                                accepted ? 202 : 409,
                                accepted ? "accepted" : "gesture_rejected");
                    }
                    return;
                case "/swipe":
                    int x1 = (int) parseLong(query.get("x1"), -1);
                    int y1 = (int) parseLong(query.get("y1"), -1);
                    int x2 = (int) parseLong(query.get("x2"), -1);
                    int y2 = (int) parseLong(query.get("y2"), -1);
                    long duration = parseLong(query.get("duration"), 300);
                    if (x1 < 0 || y1 < 0 || x2 < 0 || y2 < 0) {
                        write(client.getOutputStream(), 400, "invalid_coordinates");
                    } else {
                        boolean accepted = service.swipe(
                                x1, y1, x2, y2, duration, 2_000);
                        write(
                                client.getOutputStream(),
                                accepted ? 202 : 409,
                                accepted ? "accepted" : "gesture_rejected");
                    }
                    return;
                case "/back":
                    boolean backAccepted = service.pressBack(2_000);
                    write(
                            client.getOutputStream(),
                            backAccepted ? 202 : 409,
                            backAccepted ? "accepted" : "action_rejected");
                    return;
                case "/wake":
                    write(
                            client.getOutputStream(),
                            service.wakeScreen() ? 202 : 409,
                            "accepted");
                    return;
                case "/sleep":
                    boolean sleepAccepted = service.lockScreen(2_000);
                    write(
                            client.getOutputStream(),
                            sleepAccepted ? 202 : 409,
                            sleepAccepted ? "accepted" : "action_rejected");
                    return;
                case "/foreground":
                    String foregroundPackage = query.getOrDefault(
                            "package",
                            VagAccessibilityService.VOLKSWAGEN_PACKAGE);
                    write(
                            client.getOutputStream(),
                            200,
                            service.isPackageForeground(foregroundPackage, 2_000)
                                    ? "true"
                                    : "false");
                    return;
                case "/launch":
                    String launchPackage = query.getOrDefault(
                            "package",
                            VagAccessibilityService.VOLKSWAGEN_PACKAGE);
                    boolean launched = service.launchPackage(launchPackage, 2_000);
                    write(
                            client.getOutputStream(),
                            launched ? 202 : 409,
                            launched ? "accepted" : "launch_rejected");
                    return;
                case "/version":
                    String versionPackage = query.getOrDefault(
                            "package",
                            VagAccessibilityService.VOLKSWAGEN_PACKAGE);
                    String version = service.packageVersion(versionPackage);
                    write(
                            client.getOutputStream(),
                            version.isEmpty() ? 404 : 200,
                            version.isEmpty() ? "package_not_found" : version);
                    return;
                default:
                    write(client.getOutputStream(), 404, "not_found");
            }
        } catch (IOException error) {
            Log.d(TAG, "Agent client disconnected", error);
        }
    }

    private static Map<String, String> readHeaders(BufferedReader reader) throws IOException {
        Map<String, String> headers = new HashMap<>();
        for (int count = 0; count < MAX_HEADER_LINES; count++) {
            String line = reader.readLine();
            if (line == null || line.isEmpty()) {
                break;
            }
            int colon = line.indexOf(':');
            if (colon > 0) {
                headers.put(
                        line.substring(0, colon).trim().toLowerCase(),
                        line.substring(colon + 1).trim());
            }
        }
        return headers;
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> query = new HashMap<>();
        for (String pair : rawQuery.split("&")) {
            int equals = pair.indexOf('=');
            String key = equals >= 0 ? pair.substring(0, equals) : pair;
            String value = equals >= 0 ? pair.substring(equals + 1) : "";
            query.put(
                    Uri.decode(key),
                    Uri.decode(value));
        }
        return query;
    }

    private static void writeSnapshot(
            OutputStream output,
            VagAccessibilityService.Snapshot snapshot) throws IOException {
        if (!"ok".equals(snapshot.status)) {
            write(output, 409, snapshot.status);
            return;
        }
        String body = Base64.encodeToString(
                snapshot.xml.getBytes(StandardCharsets.UTF_8),
                Base64.NO_WRAP);
        write(output, 200, body);
    }

    private static void write(OutputStream output, int code, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        String reason;
        switch (code) {
            case 200: reason = "OK"; break;
            case 202: reason = "Accepted"; break;
            case 400: reason = "Bad Request"; break;
            case 403: reason = "Forbidden"; break;
            case 404: reason = "Not Found"; break;
            default: reason = "Conflict"; break;
        }
        String headers = "HTTP/1.0 " + code + " " + reason + "\r\n"
                + "Content-Type: text/plain; charset=utf-8\r\n"
                + "Content-Length: " + bytes.length + "\r\n"
                + "Connection: close\r\n\r\n";
        output.write(headers.getBytes(StandardCharsets.US_ASCII));
        output.write(bytes);
        output.flush();
    }

    private static boolean tokensEqual(String expected, String actual) {
        if (!isValidToken(expected) || actual == null) {
            return false;
        }
        return MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                actual.getBytes(StandardCharsets.UTF_8));
    }

    private static long parseLong(String value, long fallback) {
        if (value == null) {
            return fallback;
        }
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
