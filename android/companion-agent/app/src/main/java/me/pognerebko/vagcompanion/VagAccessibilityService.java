package me.pognerebko.vagcompanion;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Path;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public final class VagAccessibilityService extends AccessibilityService {
    public static final String VOLKSWAGEN_PACKAGE = "com.volkswagen.weconnect";

    private static volatile VagAccessibilityService instance;

    private final Object revisionMonitor = new Object();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private AgentHttpServer httpServer;
    private long revision;
    private String eventPackage = "";
    private String eventClass = "";

    static VagAccessibilityService getInstance() {
        return instance;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        httpServer = new AgentHttpServer(this);
        httpServer.start();
        bumpRevision(VOLKSWAGEN_PACKAGE, "service-connected");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) {
            return;
        }
        CharSequence packageName = event.getPackageName();
        if (packageName != null && VOLKSWAGEN_PACKAGE.contentEquals(packageName)) {
            bumpRevision(packageName.toString(), chars(event.getClassName()));
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
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return snapshotOnMainThread();
        }
        AtomicReference<Snapshot> result = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                result.set(snapshotOnMainThread());
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
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return tapOnMainThread(x, y);
        }
        AtomicReference<Boolean> accepted = new AtomicReference<>(false);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                accepted.set(tapOnMainThread(x, y));
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        return accepted.get();
    }

    boolean swipe(
            int x1,
            int y1,
            int x2,
            int y2,
            long durationMs,
            long timeoutMs) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return swipeOnMainThread(x1, y1, x2, y2, durationMs);
        }
        AtomicReference<Boolean> accepted = new AtomicReference<>(false);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                accepted.set(swipeOnMainThread(x1, y1, x2, y2, durationMs));
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        return accepted.get();
    }

    boolean pressBack(long timeoutMs) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return performGlobalAction(GLOBAL_ACTION_BACK);
        }
        AtomicReference<Boolean> accepted = new AtomicReference<>(false);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                accepted.set(performGlobalAction(GLOBAL_ACTION_BACK));
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        return accepted.get();
    }

    boolean lockScreen(long timeoutMs) {
        return performGlobalActionOnMain(GLOBAL_ACTION_LOCK_SCREEN, timeoutMs);
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
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return launchPackageOnMain(packageName);
        }
        AtomicReference<Boolean> launched = new AtomicReference<>(false);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                launched.set(launchPackageOnMain(packageName));
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        return launched.get();
    }

    boolean wakeScreen() {
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

    String packageVersion(String packageName) {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(packageName, 0);
            return info.versionName == null ? "" : info.versionName;
        } catch (PackageManager.NameNotFoundException missing) {
            return "";
        }
    }

    private Snapshot snapshotOnMainThread() {
        AccessibilityNodeInfo root = findVolkswagenRoot();
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

    private AccessibilityNodeInfo findVolkswagenRoot() {
        AccessibilityNodeInfo active = getRootInActiveWindow();
        if (isVolkswagen(active)) {
            return active;
        }
        if (active != null) {
            active.recycle();
        }
        List<AccessibilityWindowInfo> windows = getWindows();
        for (int i = windows.size() - 1; i >= 0; i--) {
            AccessibilityNodeInfo candidate = windows.get(i).getRoot();
            if (isVolkswagen(candidate)) {
                return candidate;
            }
            if (candidate != null) {
                candidate.recycle();
            }
        }
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

    private boolean performGlobalActionOnMain(int action, long timeoutMs) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return performGlobalAction(action);
        }
        AtomicReference<Boolean> accepted = new AtomicReference<>(false);
        CountDownLatch done = new CountDownLatch(1);
        mainHandler.post(() -> {
            try {
                accepted.set(performGlobalAction(action));
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
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
