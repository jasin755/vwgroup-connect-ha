package me.pognerebko.vagcompanion;

/** Local pause wins over every transport, including actions already queued. */
final class AutomationGate {
    private boolean paused;
    private boolean controlsVisible;
    private long generation;

    synchronized void setPaused(boolean value) {
        if (paused != value) {
            paused = value;
            generation++;
        }
    }

    synchronized void setControlsVisible(boolean value) {
        if (controlsVisible != value) {
            controlsVisible = value;
            generation++;
        }
    }

    synchronized boolean isSuspended() {
        return paused || controlsVisible;
    }

    synchronized long lease() {
        return isSuspended() ? -1 : generation;
    }

    synchronized boolean allows(long lease) {
        return lease >= 0 && lease == generation && !isSuspended();
    }
}
