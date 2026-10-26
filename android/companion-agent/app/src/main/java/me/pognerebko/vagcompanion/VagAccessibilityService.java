package me.pognerebko.vagcompanion;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Path;
import android.os.BatteryManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BooleanSupplier;

public final class VagAccessibilityService extends AccessibilityService {
    public static final String VOLKSWAGEN_PACKAGE = "com.volkswagen.weconnect";

    private static volatile VagAccessibilityService instance;
    private static boolean controlsVisible;
    private final AutomationGate automation = new AutomationGate();

    private final Object revisionMonitor = new Object();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private AgentHttpServer httpServer;
    private AgentRelayClient relayClient;
    private PowerManager.WakeLock screenWakeLock;
    private long revision;
    private String eventPackage = "";
    private String eventClass = "";

    static VagAccessibilityService getInstance() {
        return instance;
    }

    static boolean isPaused(Context context) {
        return context.getSharedPreferences("agent", MODE_PRIVATE)
                .getBoolean("automation_paused", false);
    }

    // Called only by the local Activity on the main thread. No remote resume.
    static void setPaused(Context context, boolean paused) {
        context.getSharedPreferences("agent", MODE_PRIVATE).edit()
                .putBoolean("automation_paused", paused).apply();
        if (instance != null) {
            instance.automation.setPaused(paused);
            instance.automationStateChanged();
        }
    }

    static void setControlsVisible(boolean visible) {
        controlsVisible = visible;
        if (instance != null) {
            instance.automation.setControlsVisible(visible);
            instance.automationStateChanged();
        }
    }

    boolean isAutomationSuspended() {
        return automation.isSuspended();
    }

    long automationLease() {
        return automation.lease();
    }

    boolean allowsAutomation(long lease) {
        return automation.allows(lease);
    }

    private void automationStateChanged() {
        if (isAutomationSuspended()) {
            releaseScreenWakeLock();
        } else {
            acquireScreenWakeLock();
        }
        bumpRevision(VOLKSWAGEN_PACKAGE, "automation-state");
        if (relayClient != null) {
            relayClient.onAccessibilityChanged(getRevision());
        }
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        automation.setPaused(isPaused(this));
        automation.setControlsVisible(controlsVisible);
        acquireScreenWakeLock();
        httpServer = new AgentHttpServer(this);
        httpServer.start();
        startRelayClient();
        bumpRevision(VOLKSWAGEN_PACKAGE, "service-connected");
        if (relayClient != null) {
            relayClient.onAccessibilityChanged(getRevision());
        }
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || isAutomationSuspended()) {
            return;
        }
        CharSequence packageName = event.getPackageName();
        if (packageName != null && VOLKSWAGEN_PACKAGE.contentEquals(packageName)) {
            bumpRevision(packageName.toString(), chars(event.getClassName()));
            if (relayClient != null) {
                relayClient.onAccessibilityChanged(getRevision());
            }
        }
    }

    @Override
    public void onInterrupt() {
        // Android calls this when another accessibility feedback type takes over.
        // The service remains registered and the next event resumes operation.
    }

    @Override
    public boolean onUnbind(Intent intent) {
        stopHttpServer();
        stopRelayClient();
        releaseScreenWakeLock();
        if (instance == this) {
            instance = null;
        }
        synchronized (revisionMonitor) {
            revisionMonitor.notifyAll();
        }
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        stopHttpServer();
        stopRelayClient();
        releaseScreenWakeLock();
        if (instance == this) {
            instance = null;
        }
        super.onDestroy();
    }

    long getRevision() {
        synchronized (revisionMonitor) {
            return revision;
        }
    }

    String getEventPackage() {
        synchronized (revisionMonitor) {
            return eventPackage;
        }
    }

    String getEventClass() {
        synchronized (revisionMonitor) {
            return eventClass;
        }
    }

    long waitForRevision(long afterRevision, long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + Math.max(0, timeoutMs);
        synchronized (revisionMonitor) {
            while (revision <= afterRevision && instance == this) {
                long remaining = deadline - SystemClock.elapsedRealtime();
                if (remaining <= 0) {
                    break;
                }
                try {
                    revisionMonitor.wait(remaining);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            return revision;
        }
    }

    Snapshot snapshot(long timeoutMs) {
        return snapshot(timeoutMs, false);
    }

    Snapshot snapshotActive(long timeoutMs) {
        return snapshot(timeoutMs, true);
    }

    private Snapshot snapshot(long timeoutMs, boolean allowActiveSystemWindow) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return snapshotOnMainThread(allowActiveSystemWindow);
        }
        AtomicReference<Snapshot> result = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                result.set(snapshotOnMainThread(allowActiveSystemWindow));
            } finally {
                done.countDown();
            }
        });
        try {
            if (!done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS)) {
                return Snapshot.error("snapshot_timeout", getRevision());
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return Snapshot.error("snapshot_interrupted", getRevision());
        }
        Snapshot snapshot = result.get();
        return snapshot == null
                ? Snapshot.error("snapshot_failed", getRevision())
                : snapshot;
    }

    boolean tap(int x, int y, long timeoutMs) {
        return runAutomationAction(() -> tapOnMainThread(x, y), timeoutMs);
    }

    boolean swipe(
            int x1,
            int y1,
            int x2,
            int y2,
            long durationMs,
            long timeoutMs) {
        return runAutomationAction(
                () -> swipeOnMainThread(x1, y1, x2, y2, durationMs), timeoutMs);
    }

    boolean setText(String text, long timeoutMs) {
        return runAutomationAction(() -> setTextOnMainThread(text), timeoutMs);
    }

    boolean pressBack(long timeoutMs) {
        return runAutomationAction(() -> performGlobalAction(GLOBAL_ACTION_BACK), timeoutMs);
    }

    boolean lockScreen(long timeoutMs) {
        return runAutomationAction(() -> performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN), timeoutMs);
    }

    boolean isPackageForeground(String packageName, long timeoutMs) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return packageIsForegroundOnMain(packageName);
        }
        AtomicReference<Boolean> foreground = new AtomicReference<>(false);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                foreground.set(packageIsForegroundOnMain(packageName));
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        return foreground.get();
    }

    boolean launchPackage(String packageName, long timeoutMs) {
        return runAutomationAction(() -> launchPackageOnMain(packageName), timeoutMs);
    }

    boolean wakeScreen() {
        return runAutomationAction(this::wakeScreenOnMainThread, 2_000);
    }

    private boolean wakeScreenOnMainThread() {
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        if (power == null || power.isInteractive()) {
            return true;
        }
        PowerManager.WakeLock wakeLock = power.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK
                        | PowerManager.ACQUIRE_CAUSES_WAKEUP
                        | PowerManager.ON_AFTER_RELEASE,
                "vagcompanion:wake");
        wakeLock.acquire(5_000);
        wakeLock.release();
        return power.isInteractive();
    }

    @SuppressLint("WakelockTimeout")
    private void acquireScreenWakeLock() {
        if (isAutomationSuspended()) {
            return;
        }
        if (screenWakeLock != null && screenWakeLock.isHeld()) {
            return;
        }
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        if (power == null) {
            return;
        }
        screenWakeLock = power.newWakeLock(
                PowerManager.SCREEN_DIM_WAKE_LOCK
                        | PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "vagcompanion:keep-screen-awake");
        screenWakeLock.setReferenceCounted(false);
        screenWakeLock.acquire();
    }

    private void releaseScreenWakeLock() {
        if (screenWakeLock != null && screenWakeLock.isHeld()) {
            screenWakeLock.release();
        }
        screenWakeLock = null;
    }

    boolean isScreenWakeLockHeld() {
        return screenWakeLock != null && screenWakeLock.isHeld();
    }

    String packageVersion(String packageName) {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(packageName, 0);
            return info.versionName == null ? "" : info.versionName;
        } catch (PackageManager.NameNotFoundException missing) {
            return "";
        }
    }

    int batteryLevel() {
        BatteryManager battery = (BatteryManager) getSystemService(BATTERY_SERVICE);
        if (battery == null) {
            return -1;
        }
        return normalizeBatteryLevel(
                battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY));
    }

    static int normalizeBatteryLevel(int level) {
        return level >= 0 && level <= 100 ? level : -1;
    }

    void restartRelayClient() {
        stopRelayClient();
        startRelayClient();
    }

    private Snapshot snapshotOnMainThread(boolean allowActiveSystemWindow) {
        if (isAutomationSuspended()) {
            return Snapshot.error("automation_paused", getRevision());
        }
        AccessibilityNodeInfo root = findActiveRoot(allowActiveSystemWindow);
        if (root == null) {
            return Snapshot.error("no_volkswagen_window", getRevision());
        }
        try {
            return Snapshot.ok(UiHierarchySerializer.serialize(root), getRevision());
        } catch (RuntimeException error) {
            return Snapshot.error("serialize_failed", getRevision());
        } finally {
            root.recycle();
        }
    }

    private AccessibilityNodeInfo findActiveRoot(boolean allowActiveSystemWindow) {
        AccessibilityNodeInfo active = getRootInActiveWindow();
        if (active != null && (allowActiveSystemWindow || isVolkswagen(active))) {
            return active;
        }
        if (active != null) {
            active.recycle();
        }
        // Never return a background Volkswagen window while Android Settings,
        // the share sheet or another app is actually foreground. Doing so made
        // the driver believe it had returned to the overview and leave the
        // other app visible at the end of a poll.
        return null;
    }

    private boolean isVolkswagen(AccessibilityNodeInfo node) {
        return node != null
                && node.getPackageName() != null
                && VOLKSWAGEN_PACKAGE.contentEquals(node.getPackageName());
    }

    private boolean tapOnMainThread(int x, int y) {
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 40);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(stroke)
                .build();
        return dispatchGesture(gesture, null, null);
    }

    // Replaces the text of the input field that currently has focus in the
    // active window. Some Compose fields never report input focus, so a window
    // with exactly one editable field uses that one. Nothing is typed when the
    // target is missing or ambiguous.
    private boolean setTextOnMainThread(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            return false;
        }
        AccessibilityNodeInfo target = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        try {
            if (target == null || !target.isEditable()) {
                if (target != null) {
                    target.recycle();
                }
                target = singleEditable(root);
            }
            if (target == null) {
                return false;
            }
            Bundle arguments = new Bundle();
            arguments.putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            return target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
        } finally {
            if (target != null) {
                target.recycle();
            }
            root.recycle();
        }
    }

    private static AccessibilityNodeInfo singleEditable(AccessibilityNodeInfo root) {
        java.util.ArrayDeque<AccessibilityNodeInfo> pending = new java.util.ArrayDeque<>();
        AccessibilityNodeInfo found = null;
        boolean ambiguous = false;
        pending.add(AccessibilityNodeInfo.obtain(root));
        while (!pending.isEmpty()) {
            AccessibilityNodeInfo node = pending.poll();
            if (node.isEditable() && node.isVisibleToUser()) {
                if (found == null) {
                    found = AccessibilityNodeInfo.obtain(node);
                } else {
                    ambiguous = true;
                }
            }
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) {
                    pending.add(child);
                }
            }
            node.recycle();
        }
        if (ambiguous && found != null) {
            found.recycle();
            return null;
        }
        return found;
    }

    private boolean swipeOnMainThread(
            int x1,
            int y1,
            int x2,
            int y2,
            long durationMs) {
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(
                        path,
                        0,
                        Math.max(1, Math.min(durationMs, 10_000)));
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(stroke)
                .build();
        return dispatchGesture(gesture, null, null);
    }

    private boolean runAutomationAction(BooleanSupplier action, long timeoutMs) {
        long lease = automation.lease();
        if (lease < 0) {
            return false;
        }
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return automation.allows(lease) && action.getAsBoolean();
        }
        AtomicReference<Boolean> accepted = new AtomicReference<>(false);
        AtomicBoolean pending = new AtomicBoolean(true);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                if (pending.getAndSet(false) && automation.allows(lease)) {
                    accepted.set(action.getAsBoolean());
                }
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        } finally {
            pending.set(false);
        }
        return accepted.get();
    }

    private boolean packageIsForegroundOnMain(String packageName) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            return false;
        }
        try {
            return root.getPackageName() != null
                    && packageName.contentEquals(root.getPackageName());
        } finally {
            root.recycle();
        }
    }

    private boolean launchPackageOnMain(String packageName) {
        Intent launch = getPackageManager().getLaunchIntentForPackage(packageName);
        if (launch == null) {
            return false;
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            startActivity(launch);
            return true;
        } catch (RuntimeException blocked) {
            return false;
        }
    }

    private void stopHttpServer() {
        if (httpServer != null) {
            httpServer.close();
            httpServer = null;
        }
    }

    private void startRelayClient() {
        relayClient = new AgentRelayClient(this);
        relayClient.start();
    }

    private void stopRelayClient() {
        if (relayClient != null) {
            relayClient.close();
            relayClient = null;
        }
    }

    private void bumpRevision(String packageName, String className) {
        synchronized (revisionMonitor) {
            revision++;
            eventPackage = packageName;
            eventClass = className;
            revisionMonitor.notifyAll();
        }
    }

    private static String chars(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    static final class Snapshot {
        final String status;
        final String xml;
        final long revision;

        private Snapshot(String status, String xml, long revision) {
            this.status = status;
            this.xml = xml;
            this.revision = revision;
        }

        static Snapshot ok(String xml, long revision) {
            return new Snapshot("ok", xml, revision);
        }

        static Snapshot error(String status, long revision) {
            return new Snapshot(status, "", revision);
        }
    }
}
